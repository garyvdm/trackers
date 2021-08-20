import asyncio
import functools
import logging
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from itertools import chain
from pathlib import Path
from xml.etree import ElementTree

import aiohttp
from aiohttp.web import Application as WebApplication

from trackers.base import print_tracker, stream_store, Tracker, RateLimitingSemaphore

logger = logging.getLogger(__name__)


# https://www.findmespot.com/en-us/support/spot-trace/get-help/general/spot-api-support

@asynccontextmanager
async def config(app, settings):
    app['spot.session'] = session = aiohttp.ClientSession(connector=aiohttp.TCPConnector(limit=1), raise_for_status=True)
    app['spot.rate_limit_sem'] = RateLimitingSemaphore(0.5)

    if isinstance(app, WebApplication):
        import trackers.web_app
        app.router.add_route('GET', '/spot/{feed_id}',
                             handler=trackers.web_app.individual_page,
                             name='spot_individual_page')
        app.router.add_route('GET', '/spot/{feed_id}/websocket',
                             handler=functools.partial(trackers.web_app.individual_ws, get_individual_key,
                                                       functools.partial(start_individual_tracker, app, settings)),
                             name='spot_individual_ws')

    try:
        yield
    finally:
        await session.close()


def get_individual_key(request):
    return "spot-{feed_id}".format_map(request.match_info)


async def start_individual_tracker(app, settings, request):
    feed_id = request.match_info['feed_id']
    start = datetime.now() - timedelta(days=7)
    return await start_tracker(app, 'individual', feed_id, start, None)


async def start_event_tracker(app, event, rider_name, tracker_data, start, end):
    return await start_tracker(app, rider_name, tracker_data['feed_id'], start, end)


async def start_tracker(app, tracker_name, feed_id, start, end):
    tracker = Tracker('spot.{}-{}'.format(feed_id, tracker_name))
    monitor_task = asyncio.ensure_future(monitor_feed(app, tracker, feed_id, start, end))
    tracker.stop = monitor_task.cancel
    tracker.completed = monitor_task
    return tracker


async def monitor_feed(app, tracker, feed_id, start: datetime, end: datetime):
    try:
        if not start:
            start = datetime.utcnow()
        else:
            start = start.astimezone(timezone.utc).replace(tzinfo=None)
        if end:
            end = end.astimezone(timezone.utc).replace(tzinfo=None)
        last = start
        # From this point on now, last, start, and end are all utc and tz naive.

        store_path = Path(app['trackers.settings']['cache_path'], 'spot', f'{feed_id}-{start.isoformat()}')

        with stream_store(store_path, tracker.logger) as (loaded_messages, write_messages):
            seen_ids = {message['id'] for message in loaded_messages}
            await messages_to_tracker(tracker, loaded_messages)
            del loaded_messages

            while True:
                try:
                    now = datetime.utcnow()
                    if now > start:
                        new_messages = await get_new_messages(app, tracker, feed_id, start, end, last, seen_ids)
                        last = now - timedelta(minutes=1)
                        if new_messages:
                            await messages_to_tracker(tracker, new_messages)
                            write_messages(new_messages)

                    if end and now >= end:
                        break
                except asyncio.CancelledError:
                    raise
                except (aiohttp.client_exceptions.ClientError, RuntimeError) as e:
                    tracker.logger.error('Error in monitor_feed: {!r}'.format(e))
                except Exception:
                    tracker.logger.exception('Error in monitor_feed:')

                await wait_for_next_check(tracker)

    except asyncio.CancelledError:
        raise
    except Exception:
        tracker.logger.exception('Error in monitor_feed:')


async def api_call(app, feed_id, params):
    url = f'https://api.findmespot.com/spot-main-web/consumer/rest-api/2.0/public/feed/{feed_id}/message.xml'
    session = app['spot.session']
    rate_limit_sem = app['spot.rate_limit_sem']
    async with rate_limit_sem:
        async with session.get(url, params=params) as response:
            text = await response.text()
    return ElementTree.fromstring(text)


def xml_to_dict(item: ElementTree):
    return {child.tag: child.text for child in item}


async def get_new_messages(app, tracker, feed_id, start: datetime, end: datetime, last: datetime, seen_ids):
    if last:
        base_params = {'startDate': last.isoformat(timespec='seconds') + '-0000'}
    else:
        base_params = {'startDate': start.isoformat(timespec='seconds') + '-0000'}
    if end:
        base_params['endDate'] = end.isoformat(timespec='seconds') + '-0000'

    need_to_query_more = True
    start = 0
    query_results = []
    while need_to_query_more:
        params = {**base_params, 'start': start}

        tree: ElementTree = await api_call(app, feed_id, params)

        for error in tree.findall('errors/error'):
            error = xml_to_dict(error)
            if error['code'] == 'E-0195':
                level = logging.DEBUG
            else:
                level = logging.ERROR
            tracker.logger.log(msg=f"{error['text']} ({error['code']}): {error['description']}", level=level)

        count_el = tree.find('feedMessageResponse/count')
        if count_el is not None:
            count = int(count_el.text)
        else:
            count = 0
        if count < 51:
            need_to_query_more = False
        start += count
        now = datetime.now(timezone.utc)
        messages = [xml_to_dict(item) for item in tree.findall('feedMessageResponse/messages/message')]
        index = 0
        for index, message in enumerate(messages):
            id = message['id']
            if id in seen_ids:
                need_to_query_more = False
                break
            seen_ids.add(id)
            message['sever_time'] = now
            message['unixTime'] = int(message['unixTime'])
            message['latitude'] = float(message['latitude'])
            message['longitude'] = float(message['longitude'])
            message['altitude'] = float(message['altitude'])

        else:
            index += 1

        messages = messages[:index]
        query_results.append(messages)

    return list(sorted(chain.from_iterable(query_results), key=lambda message: message['unixTime']))


async def messages_to_tracker(tracker, messages):
    new_points = []
    for message in messages:
        if message['altitude']:
            p = [message['latitude'], message['longitude'], message['altitude']]
        else:
            p = [message['latitude'], message['longitude']]
        new_points.append({
            'position': p,
            'battery': message['batteryState'],
            # TODO We need to be able to specify the timezone in the config, as it seems to vary.
            'time': datetime.fromtimestamp(message['unixTime']),
            'server_time': message['sever_time'],
            'message_type': message['messageType'],  # TODO translate into tracker status
        })
    if new_points:
        await tracker.new_points(new_points)


async def wait_for_next_check(tracker):
    now = datetime.now()
    if tracker.points:
        next_check_on_last_point_time = tracker.points[-1]['time'] + timedelta(minutes=5, seconds=15)
    else:
        next_check_on_last_point_time = datetime(year=1980, month=1, day=1)

    next_check_on_now = now + timedelta(minutes=2, seconds=30)
    next_check = max(next_check_on_now, next_check_on_last_point_time)
    next_check_sec = (next_check - now).total_seconds()
    tracker.logger.debug(f'Next check: {next_check_sec} sec -- {next_check}')
    await asyncio.sleep(next_check_sec)


async def main():
    import signal

    settings = {
        'cache_path': 'cache',
    }
    app = {
        'trackers.settings': settings
    }
    async with config(app, settings):
        tracker = await start_tracker(
            app, 'foobar', '09tTtcmfhXSAkVisZezCoD8RSrINdEezx', datetime(2019, 1, 16), None)
        print_tracker(tracker)

        run_fut = asyncio.Future()
        for signame in ('SIGINT', 'SIGTERM'):
            loop.add_signal_handler(getattr(signal, signame), run_fut.set_result, None)
        try:
            await run_fut
        finally:
            for signame in ('SIGINT', 'SIGTERM'):
                loop.remove_signal_handler(getattr(signal, signame))
        tracker.stop()
        await tracker.complete()

if __name__ == "__main__":
    logging.basicConfig(level=logging.DEBUG)
    loop = asyncio.get_event_loop()
    loop.run_until_complete(main())
