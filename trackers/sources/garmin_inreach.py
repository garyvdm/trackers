import asyncio
import logging
import xml.etree.ElementTree as xml
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone

import aiohttp
from yarl import URL

from trackers.base import Tracker, print_tracker

logger = logging.getLogger(__name__)


@asynccontextmanager
async def config(app, settings):
    app["garmin_inreach.session"] = session = aiohttp.ClientSession(
        connector=aiohttp.TCPConnector(limit=1),
        raise_for_status=True,
    )

    try:
        yield
    finally:
        await session.close()


async def start_event_tracker(app, event, rider_name, tracker_data, start, end):
    return await start_tracker(
        app,
        rider_name,
        tracker_data["feed_id"],
        tracker_data.get("password"),
        start,
        end,
    )


async def start_tracker(app, tracker_name, feed_id, password, start, end):
    tracker = Tracker("garmin_inreach.{}-{}".format(feed_id, tracker_name))
    monitor_task = asyncio.ensure_future(monitor_feed(app, tracker, feed_id, password, start, end))
    tracker.stop = monitor_task.cancel
    tracker.completed = monitor_task
    return tracker


async def monitor_feed(app, tracker, feed_id, password, start, end):
    try:
        seen_ids = set()
        if password:
            auth = aiohttp.BasicAuth(feed_id, password)
        else:
            auth = None
        session: aiohttp.ClientSession = app["garmin_inreach.session"]
        url = URL(f"https://share.garmin.com/Feed/Share/{feed_id}")
        if not start:
            start = datetime.utcnow()
        else:
            start = start.astimezone(timezone.utc).replace(tzinfo=None)
        if end:
            end = end.astimezone(timezone.utc).replace(tzinfo=None)
        # From this point on now, last, start, and end are all utc and tz naive.

        while True:
            try:
                now = datetime.utcnow()
                if now > start:
                    last = tracker.points[-1]["time"] if tracker.points else start
                    last = last.astimezone(timezone.utc)
                    params = {"d1": last.isoformat(timespec="seconds") + "z"}
                    if end and now > end:
                        params["d2"] = end.isoformat(timespec="seconds") + "z"
                    url_with_params = url.update_query(params)
                    tracker.logger.debug(f"Getting data. {url_with_params}")
                    async with session.get(url_with_params, auth=auth) as response:
                        kml_text = await response.text()
                    # tracker.logger.debug(f"Response: \n {kml_text}")
                    await process_data(tracker, kml_text, now, seen_ids)

                if end and now >= end:
                    break
            except asyncio.CancelledError:
                raise
            except (aiohttp.client_exceptions.ClientError, RuntimeError) as e:
                tracker.logger.error("Error in monitor_feed: {!r}".format(e))
            except Exception:
                tracker.logger.exception("Error in monitor_feed:")

            await wait_for_next_check(tracker)

    except asyncio.CancelledError:
        raise
    except Exception:
        tracker.logger.exception("Error in monitor_feed:")


async def process_data(tracker, kml_text, now, seen_ids):
    xml_doc = xml.fromstring(kml_text)

    kml_ns = {
        "kml": "http://www.opengis.net/kml/2.2",
    }

    placemarks = xml_doc.findall("./kml:Document/kml:Folder/kml:Placemark", kml_ns)

    new_points = []
    for placemark in placemarks:
        extended_data = {
            data_el.attrib["name"]: data_el.find("kml:value", kml_ns).text
            for data_el in placemark.findall("kml:ExtendedData/kml:Data", kml_ns)
        }
        if extended_data and extended_data["Id"] not in seen_ids:
            seen_ids.add(extended_data["Id"])
            lat = float(extended_data["Latitude"])
            lng = float(extended_data["Longitude"])
            elevation = float(extended_data["Elevation"].partition(" ")[0])
            time_utc = datetime.fromisoformat(
                placemark.find("kml:TimeStamp/kml:when", kml_ns).text[:-1]
            ).replace(tzinfo=timezone.utc)
            time = time_utc.astimezone().replace(tzinfo=None)
            point = {
                "position": [lat, lng, elevation],
                "time": time,
                "battery": None,
            }
            if extended_data["Event"] == "Tracking turned off from device.":
                point["tk_config"] = "Off"
            if extended_data["Event"] == "Tracking turned on from device.":
                point["tk_config"] = "On"
            new_points.append(point)
    tracker.logger.debug(f"Got {len(new_points)} new points. {len(placemarks)} placemarks.")
    if new_points:
        await tracker.new_points(new_points)


async def wait_for_next_check(tracker):
    now = datetime.now()
    if tracker.points:
        next_check_on_last_point_time = tracker.points[-1]["time"] + timedelta(minutes=11)
    else:
        next_check_on_last_point_time = datetime(year=1980, month=1, day=1)

    next_check_on_now = now + timedelta(minutes=1)
    next_check = max(next_check_on_now, next_check_on_last_point_time)
    next_check_sec = (next_check - now).total_seconds()
    tracker.logger.debug(f"Next check: {next_check_sec} sec -- {next_check}")
    await asyncio.sleep(next_check_sec)


async def main():
    import signal

    app = {}
    settings = {}
    async with config(app, settings):
        tracker = await start_tracker(app, "JanV", "JanV", "", datetime(2019, 6, 17), None)
        print_tracker(tracker)

        run_fut = asyncio.Future()
        for signame in ("SIGINT", "SIGTERM"):
            loop.add_signal_handler(getattr(signal, signame), run_fut.set_result, None)
        try:
            await run_fut
        finally:
            for signame in ("SIGINT", "SIGTERM"):
                loop.remove_signal_handler(getattr(signal, signame))
        tracker.stop()
        await tracker.complete()


if __name__ == "__main__":
    logging.basicConfig(level=logging.DEBUG)
    loop = asyncio.get_event_loop()
    loop.run_until_complete(main())
