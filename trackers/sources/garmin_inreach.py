import asyncio
import datetime
import functools
import logging
from contextlib import asynccontextmanager

import aiohttp
from aiohttp.web import Application as WebApplication
from jsonpointer import resolve_pointer

from trackers.base import print_tracker, Tracker

logger = logging.getLogger(__name__)


@asynccontextmanager
async def config(app, settings):
    app['spot.session'] = session = aiohttp.ClientSession(connector=aiohttp.TCPConnector(limit=1))
    app['spot.rate_limit_sem'] = asyncio.Semaphore()

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
    start = datetime.datetime.now() - datetime.timedelta(days=7)
    return await start_tracker(app, 'individual', feed_id, start, None)


async def api_call_inner(app, feed_id, params, fut):
    url = f'https://api.findmespot.com/spot-main-web/consumer/rest-api/2.0/public/feed/{feed_id}/message.json'
    session = app['spot.session']
    rate_limit_sem = app['spot.rate_limit_sem']
    async with rate_limit_sem:
        async with session.get(url, params=params) as response:
            response.raise_for_status()
            data = await response.json()

        error = resolve_pointer(data, '/response/errors/error', None)
        if error and error.get('code') != 'E-0195':
            fut.set_exception(RuntimeError('{text} ({code}): {description}'.format_map(error)))
        else:
            fut.set_result(data)

        await asyncio.sleep(2)  # Rate limit


def api_call(app, feed_id, params):
    fut = asyncio.Future()
    task = asyncio.ensure_future(api_call_inner(app, feed_id, params, fut))
    task.add_done_callback(lambda fut: fut.result())
    return fut


async def start_event_tracker(app, event, rider_name, tracker_data, start, end):
    return await start_tracker(app, rider_name, tracker_data['feed_id'], start, end)


async def start_tracker(app, tracker_name, feed_id, start, end):
    tracker = Tracker('spot.{}-{}'.format(feed_id, tracker_name))
    monitor_task = asyncio.ensure_future(monitor_feed(app, tracker, feed_id, start, end))
    tracker.stop = monitor_task.cancel
    tracker.completed = monitor_task
    return tracker


async def monitor_feed(app, tracker, feed_id, start, end):
    try:
        seen_ids = set()
        if not start:
            start = datetime.datetime.utcnow()
        else:
            start = start.astimezone(datetime.timezone.utc).replace(tzinfo=None)
        if end:
            end = end.astimezone(datetime.timezone.utc).replace(tzinfo=None)
        last = start
        # From this point on now, last, start, and end are all utc and tz naive.

        while True:
            try:
                now = datetime.datetime.utcnow()
                if end and now > end:
                    now = end
                if now > start:
                    params = {'startDate': last.isoformat(timespec='seconds') + '-0000', 'endDate': now.isoformat(timespec='seconds') + '-0000'}
                    last = now
                    data = await api_call(app, feed_id, params)
                    await process_data(tracker, data, now, seen_ids)

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


async def process_data(tracker, data, now, seen_ids):
    error = resolve_pointer(data, '/response/errors/error')
    if error:
        tracker.logger.debug(f'{error["description"]}')

    messages = resolve_pointer(data, '/response/feedMessageResponse/messages/message', ())
    new_points = []
    for message in messages:
        if message['id'] not in seen_ids:
            seen_ids.add(message['id'])
            if message['altitude']:
                p = [message['latitude'], message['longitude'], message['altitude']]
            else:
                p = [message['latitude'], message['longitude']]
            new_points.append({
                'position': p,
                'battery': message['batteryState'],
                'time': datetime.datetime.utcfromtimestamp(message['unixTime']),
                'server_time': now,
                'message_type': message['messageType'],  # TODO translate into tracker status
            })
    if new_points:
        await tracker.new_points(new_points)


async def wait_for_next_check(tracker):
    now = datetime.datetime.now()
    if tracker.points:
        next_check_on_last_point_time = tracker.points[-1]['time'] + datetime.timedelta(minutes=5, seconds=15)
    else:
        next_check_on_last_point_time = datetime.datetime(year=1980, month=1, day=1)

    next_check_on_now = now + datetime.timedelta(minutes=2, seconds=30)
    next_check = max(next_check_on_now, next_check_on_last_point_time)
    next_check_sec = (next_check - now).total_seconds()
    tracker.logger.debug(f'Next check: {next_check_sec} sec -- {next_check}')
    await asyncio.sleep(next_check_sec)


async def main():
    import signal

    app = {}
    settings = {}
    async with config(app, settings):
        tracker = await start_tracker(
            app, 'foobar', '0RrHLaqkrQHCYYj52QMZEP6fhOLd8g6E5', datetime.datetime(2019, 1, 16), None)
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
