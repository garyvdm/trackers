import asyncio
import datetime
import logging
import re
from collections import defaultdict
from functools import partial

import aiomsgpack
import more_itertools
from aiocontext import async_contextmanager
from aiohttp.web import Application as WebApplication

from trackers.base import cancel_and_wait_task, Observable, print_tracker, Tracker


logger = logging.getLogger(__name__)


@async_contextmanager
async def config(app, settings):
    app['tkstorage.position_received_observables'] = position_received_observables = defaultdict(partial(Observable, logger))
    app['tkstorage.send_queue'] = send_queue = asyncio.Queue()
    app['tkstorage.all_points'] = all_points = []
    connection_task = asyncio.ensure_future(connection(app, settings, all_points, position_received_observables, send_queue))

    if isinstance(app, WebApplication):
        import trackers.web_app
        app.router.add_route('GET', '/tk/{id}',
                             handler=trackers.web_app.individual_page,
                             name='tkstorage_individual_page')
        app.router.add_route('GET', '/tk/{id}/websocket',
                             handler=partial(trackers.web_app.individual_ws, get_individual_key,
                                             partial(start_individual_tracker, app, settings)),
                             name='tkstorage_individual_ws')

    try:
        yield
    finally:
        logger.debug('Shutdown.')
        await cancel_and_wait_task(connection_task)


async def connection(app, settings, all_points, position_received_observables, send_queue):
    try:
        reconnect_sleep_time = 5
        path = settings['tkstorage_path']
        loop = asyncio.get_event_loop()
        while True:
            try:
                logger.debug(f'Connecting to {path}')
                _, proto = await loop.create_unix_connection(
                    aiomsgpack.make_msgpack_protocol_factory(loop=loop),
                    path=path,
                )
                try:
                    reconnect_sleep_time = 1
                    proto.write({'start': len(all_points)})
                    write_fut = asyncio.ensure_future(write(app, proto, send_queue))
                    try:
                        async for msg in proto:
                            for item in msg:
                                try:
                                    point = msg_item_to_point(item)
                                except Exception:
                                    logger.exception('Error in msg_item_to_point: ')
                                    point = None
                                all_points.append((item, point))

                                if point and point.get('id'):
                                    observable = position_received_observables.get(point['tk_id'])
                                    if observable:
                                        await observable(point)
                    finally:
                        await cancel_and_wait_task(write_fut)
                finally:
                    proto.close()
            except asyncio.CancelledError:
                raise
            except ConnectionRefusedError as e:
                logger.error(f'Error in connection task: {e}')
            except Exception:
                logger.exception('Error in connection task: ')
            logger.debug('Reconnecting in {} sec'.format(reconnect_sleep_time))
            await asyncio.sleep(reconnect_sleep_time)
            reconnect_sleep_time = min((reconnect_sleep_time * 2, 30))
    except asyncio.CancelledError:
        raise
    except Exception:
        logger.exception('Error in connection task: ')


async def write(app, proto, send_queue):
    while True:
        msg = await send_queue.get()
        proto.write(msg)

CONNECT = 0
MESSAGE_RECEIVED = 1
MESSAGE_SENT = 1
DISCONNECT = 2
CANCELED = 3

status_battery_re = re.compile('Power: (\d*)%')


def msg_item_to_point(msg_item):

    correct_len = more_itertools.first(more_itertools.windowed(msg_item, 5))
    connection_id, server_time, type, data, id = correct_len
    # print(msg_item)

    if not id:
        return

    id = id.decode()
    server_time = datetime.datetime.fromtimestamp(server_time)
    point = {
        'server_time': server_time,
        'tk_id': id,
    }
    if data:
        data = data.decode('latin1')
        assert data[0] == '('
        assert data[-1] == ')'
        data = data_split(data[1:-1])
        if type == MESSAGE_RECEIVED:
            msg_code = data[1]
            # if msg_code == 'ZC20':
            #     point['time'] = parse_date_time(data[2], data[3])
            if msg_code[:3] == 'DW3':
                assert data[3] == 'A'
                lat = parse_coordinate(data[4], 'S')
                lng = parse_coordinate(data[5], 'W')
                alt = float(data[9])
                point['time'] = parse_date_time(data[2], data[7])
                point['position'] = (lat, lng, alt)
                point['num_sat'] = int(data[10])
            if msg_code == 'ZC03':
                point['time'] = parse_date_time(data[2], data[3])
                msg = data[4]
                # print(msg)
                status_battery_match = status_battery_re.search(msg)
                if status_battery_match:
                    point['battery'] = int(status_battery_match.group(1))
                    point['tk_status'] = msg
    return point


def data_split(data):
    split = []
    in_quote = False
    last_i = 0
    for i, c in enumerate(data):
        if c == '$':
            in_quote = not in_quote
        if c == ',' and not in_quote:
            split.append(data[last_i:i].strip('$'))
            last_i = i + 1
    split.append(data[last_i:].strip('$'))
    return split


def parse_date_time(date_raw, time_raw):
    # TODO pay attention to time zone.
    date_raw = date_raw
    time_raw = time_raw
    day = int(date_raw[0:2])
    month = int(date_raw[2:4])
    year = int(date_raw[4:6]) + 2000
    hour = int(time_raw[0:2])
    min = int(time_raw[2:4])
    sec = int(time_raw[4:6])
    return datetime.datetime(year, month, day, hour, min, sec, tzinfo=datetime.timezone.utc).astimezone().replace(tzinfo=None)


def parse_coordinate(raw, neg):
    deg = int(raw[0:-8])
    min = float(raw[-8:-1])
    hem = -1 if raw[-1] == neg else 1
    return (deg + min / 60) * hem


async def start_event_tracker(app, event, rider_name, tracker_data):
    return await start_tracker(app, rider_name, tracker_data['id'],
                               event.config['tracker_start'], event.config['tracker_end'])


def time_between(time, start, end):
    return time and (not start or start < time) and (not end or time < end)


def get_individual_key(request):
    return "tkstorage-{id}".format_map(request.match_info)


async def start_individual_tracker(app, settings, request):
    id = request.match_info['id']
    start = datetime.datetime.now() - datetime.timedelta(days=2)
    print(start)
    return await start_tracker(app, id, id, start, None)


async def start_tracker(app, tracker_name, id, start, end):
    tracker = Tracker(f'tkstorage.{id}-{tracker_name}')
    tracker.id = id
    tracker.start = start
    tracker.end = end

    tracker.finished = asyncio.Event()
    tracker.stop = partial(tracker_stop, tracker)
    tracker.completed = asyncio.ensure_future(tracker.finished.wait())
    tracker.completed.add_done_callback(partial(tracker_on_completed, tracker))

    all_points = app['tkstorage.all_points']
    filtered_points = [point for _, point in all_points if point and (point['tk_id'] == id and time_between(point.get('time'), tracker.start, tracker.end) or time_between(point.get('server_time'), tracker.start, tracker.end))]
    await tracker.new_points(filtered_points)
    tracker.position_recived = partial(tracker_point_received, tracker)
    tracker.position_received_observables = app['tkstorage.position_received_observables'][id]
    tracker.position_received_observables.subscribe(tracker.position_recived)

    return tracker


async def tracker_point_received(tracker, point):
    if time_between(point.get('time'), tracker.start, tracker.end) or time_between(point.get('server_time'), tracker.start, tracker.end):
        await tracker.new_points([point])


def tracker_stop(tracker):
    tracker.finished.set()


def tracker_on_completed(tracker, fut):
    tracker.position_received_observables.unsubscribe(tracker.position_recived)


async def main():
    import signal
    import os.path

    app = {}
    settings = {
        'tkstorage_path': os.path.expanduser('tkstorage_watcher'),
    }
    async with config(app, settings):
        tracker = await start_tracker(
            app, 'gary', 'TK01', datetime.datetime(2018, 4, 25), None)
        # await tracker.finish()
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
