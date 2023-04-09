import asyncio
import logging
import re
from collections import defaultdict
from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from urllib.parse import parse_qs, urlparse

import aiohttp
import bs4
import dateutil.parser
import dateutil.tz
from calmjs.parse import es5
from calmjs.parse.asttypes import Assign, ExprStatement, FunctionCall

import trackers.events
from trackers.base import Tracker
from trackers.bin_utils import (
    assign_rider_colors_inner,
    async_command,
    event_command_parser,
    event_name_clean,
    process_route,
    process_secondary_route_details,
    update_bounds_inner,
)
from trackers.dulwich_helpers import TreeWriter

# TODO: proper start/end filtering.

logger = logging.getLogger(__name__)


@asynccontextmanager
async def config(app, settings):
    app["trackleaders.session"] = session = aiohttp.ClientSession(
        connector=aiohttp.TCPConnector(limit=4)
    )
    # app['trackleaders.rate_limit_sem'] = asyncio.Semaphore()
    try:
        yield
    finally:
        await session.close()


def print_node(node):
    print(repr(node)[:600])
    print([i for i in dir(node) if not i.startswith("_")])
    print(str(node)[:200])
    print("------------------------------------------------------------")


async def get(session, *args, **kwargs):
    response = await session.get(*args, **kwargs)
    response.raise_for_status()
    return await response.text()


@async_command(event_command_parser, basic=True)
async def get_config(app, settings, args):
    tree_writer = TreeWriter(app["trackers.data_repo"])

    event_name = event_name_clean(args.event_name, settings)
    trackleaders_event_name = event_name
    try:
        event = await trackers.events.Event.load(app, event_name, tree_reader=tree_writer)
    except KeyError:
        event = await trackers.events.Event(app, event_name)

    logger.info("Downloading.")

    async with aiohttp.ClientSession() as session:
        race_page_text = await get(
            session, f"http://trackleaders.com/{trackleaders_event_name}f.php"
        )
        riders_list_text = await get(
            session,
            f"http://trackleaders.com/spot/{trackleaders_event_name}/sortlist.php",
        )
        route_text = await get(
            session, f"http://trackleaders.com/spot/{trackleaders_event_name}/route.js"
        )
        markers_text = await get(
            session,
            f"http://trackleaders.com/spot/{trackleaders_event_name}/checkgen.js",
        )

    logger.info("Scraping.")
    config = event.config
    race_page = bs4.BeautifulSoup(race_page_text, "html.parser")
    config["title"] = race_page.find("title").string.partition(" live")[0]
    # TODO Start time

    riders_list = bs4.BeautifulSoup(riders_list_text, "html.parser")

    riders_links = riders_list.find_all("a", title=re.compile("Open .* full <b>history</b>"))

    config["riders"] = [
        {
            "name": rider_link.string.strip(),
            "name_short": rider_link.string.partition(" ")[0],
            "tracker": {
                "type": "trackleaders",
                "event": str(args.event_name),
                "name": parse_qs(urlparse(rider_link["href"]).query)["name"][0],
            },
        }
        for rider_link in riders_links
    ]

    route_js = es5(route_text)
    route_point_nodes = route_js.children()[0].elements[0].expr.right
    route_points = [
        [float(str(subnode)) for subnode in point_node] for point_node in route_point_nodes.items
    ]
    event.routes = [
        {"original_points": route_points},
    ]

    markers_js = es5(markers_text)
    markers_details = defaultdict(dict)
    for node in markers_js.children()[0].elements:
        if isinstance(node, ExprStatement):
            if isinstance(node.expr, Assign):
                id = node.expr.left.value
                if id.startswith("markercp"):
                    markers_details[id]["location"] = [
                        float(str(loc_node))
                        for loc_node in node.expr.right.identifier.node.args.items[0].items
                    ]
            if isinstance(node.expr, FunctionCall):
                id = node.expr.identifier.node.value
                if (
                    id.startswith("markercp")
                    and node.expr.identifier.identifier.value == "bindTooltip"
                ):
                    markers_details[id]["text"] = node.expr.args.items[0].value[4:-5]
    config["markers"] = [
        {
            "marker_text": details["text"],
            "position": {
                "lat": details["location"][0],
                "lng": details["location"][1],
            },
        }
        for details in markers_details.values()
    ]

    logger.info("Processing route.")
    await process_route(settings, event.routes[0])
    process_secondary_route_details(event.routes)

    logger.info("Saving.")

    update_bounds_inner(event)
    assign_rider_colors_inner(event)

    await event.save(
        f"{event_name}: load config from trackleaders",
        tree_writer=tree_writer,
        save_routes=True,
    )


async def start_event_tracker(app, event, rider_name, tracker_data, start, end):
    return await start_tracker(app, rider_name, tracker_data["name"], tracker_data["event"], end)


async def start_tracker(app, tracker_name, name, event, end):
    tracker = Tracker(f"trackleaders.{tracker_name}")
    monitor_task = asyncio.ensure_future(monitor_feed(app, tracker, name, event, end))
    tracker.stop = monitor_task.cancel
    tracker.completed = monitor_task
    return tracker


recived_at_re = re.compile("received at: (.*?) <br />")


def datetime_parse(str):
    dt_parse_tzinfos = {
        "BST": 3600,
        "CET": 7200,
        "SAST": dateutil.tz.gettz("Africa/Johannesburg"),
    }
    brackets_removed = str.replace("(", "").replace(")", "")
    try:
        localized = dateutil.parser.parse(brackets_removed, tzinfos=dt_parse_tzinfos)
    except ValueError as e:
        raise ValueError("{}: {}".format(str(e), brackets_removed))
    return localized.replace(tzinfo=None)


position_re = re.compile(r"imarker(?P<id>\d+) = L.marker\(\[(?P<lat>.+?),(?P<lng>.+?)\]")
time_re = re.compile(
    r"imarker(?P<id>\d+)\.bindPopup\(\'.+<br />Point #\d+? received at: (?P<time>.*?) <br />"
)


async def get_points(logger, session, name, event):
    logger.debug(f"Getting http://trackleaders.com/spot/{event}/{name}.js")
    text = await get(session, f"http://trackleaders.com/spot/{event}/{name}.js")
    points = defaultdict(dict)
    for position_m in position_re.finditer(text):
        points[position_m.group("id")]["position"] = (
            float(position_m.group("lat")),
            float(position_m.group("lng")),
        )
    for time_m in time_re.finditer(text):
        if "Your SPOT Trace has been powered off" in time_m.group(0):
            del points[time_m.group("id")]
        else:
            points[time_m.group("id")]["time"] = datetime_parse(time_m.group("time"))
    sorted_points = list(sorted(points.values(), key=lambda point: point["time"]))
    logger.debug(f"Done http://trackleaders.com/spot/{event}/{name}.js")

    return sorted_points


async def monitor_feed(app, tracker, name, event, end):
    try:
        while True:
            try:
                new_all_points = await get_points(
                    tracker.logger, app["trackleaders.session"], name, event
                )
                if tracker.points == new_all_points[: len(tracker.points)]:
                    new_points = new_all_points[len(tracker.points) :]
                    tracker.logger.debug(f"New points: {len(new_points)}")
                    await tracker.new_points(new_points)
                else:
                    tracker.logger.debug("Reset points")
                    await tracker.reset_points()
                    await tracker.new_points(new_all_points)
                if datetime.now() > end:
                    break
            except asyncio.CancelledError:
                raise
            except (aiohttp.client_exceptions.ClientError, RuntimeError) as e:
                tracker.logger.error("Error in monitor_feed: {!r}".format(e))
            except Exception:
                tracker.logger.exception("Error in monitor_feed:")

            now = datetime.now()
            if tracker.points:
                next_check_on_last_point_time = tracker.points[-1]["time"] + timedelta(
                    minutes=5, seconds=30
                )
            else:
                next_check_on_last_point_time = datetime(year=1980, month=1, day=1)

            next_check_on_now = now + timedelta(minutes=1)
            next_check = max(next_check_on_now, next_check_on_last_point_time)
            next_check_sec = (next_check - now).total_seconds()
            tracker.logger.debug(f"Next check: {next_check_sec} sec -- {next_check}")
            await asyncio.sleep(next_check_sec)

    except asyncio.CancelledError:
        raise
    except Exception:
        tracker.logger.exception("Error in monitor_feed:")


async def main():
    async with aiohttp.ClientSession() as session:
        print(await get_points(logger, session, "Fietie_Rocher", "munga18"))

    # import signal
    # from trackers.base import print_tracker
    # app = {}
    # settings = {}
    # async with config(app, settings):
    #     tracker = await start_tracker(
    #         app, 'foobar', 'Rob_Walker', 'munga18')
    #     print_tracker(tracker)
    #
    #     run_fut = asyncio.Future()
    #     for signame in ('SIGINT', 'SIGTERM'):
    #         loop.add_signal_handler(getattr(signal, signame), run_fut.set_result, None)
    #     try:
    #         await run_fut
    #     finally:
    #         for signame in ('SIGINT', 'SIGTERM'):
    #             loop.remove_signal_handler(getattr(signal, signame))
    #     tracker.stop()
    #     await tracker.complete()


if __name__ == "__main__":
    logging.basicConfig(level=logging.DEBUG)
    loop = asyncio.get_event_loop()
    loop.run_until_complete(main())
