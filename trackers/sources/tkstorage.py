import asyncio
import datetime
import logging
import re
from collections import defaultdict
from copy import copy
from functools import partial
from itertools import groupby

import aiomsgpack
import more_itertools
from aiocontext import async_contextmanager
from aiohttp.web import Application as WebApplication

from trackers.base import cancel_and_wait_task, Observable, print_tracker, Tracker


logger = logging.getLogger(__name__)


@async_contextmanager
async def config(app, settings):
    app['tkstorage.points_received_observables'] = points_received_observables = defaultdict(partial(Observable, logger))
    app['tkstorage.send_queue'] = send_queue = asyncio.Queue()
    app['tkstorage.all_points'] = all_points = []
    app['tkstorage.initial_download'] = initial_download_done = asyncio.Event()
    connection_task = asyncio.ensure_future(connection(app, settings, all_points, points_received_observables, send_queue, initial_download_done))

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


tk_id_key = lambda point: point['tk_id']
time_key = lambda point: point.get('time') or point.get('server_time')


async def connection(app, settings, all_points, points_received_observables, send_queue, initial_download_done):
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
                            logging.debug(f'Downloaded {len(msg)} points.')
                            new_points = []
                            for item in msg:
                                try:
                                    point = msg_item_to_point(item)
                                except Exception:
                                    logger.exception('Error in msg_item_to_point: ')
                                    point = None
                                all_points.append((item, point))

                                if point and point.get('tk_id'):
                                    new_points.append(point)

                            new_points.sort(key=time_key)
                            for tk_id, points in groupby(sorted(new_points, key=tk_id_key), key=tk_id_key):
                                observable = points_received_observables.get(tk_id)
                                if observable:
                                    await observable(list(points))
                            initial_download_done.set()

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


def msg_item_to_point(msg_item):
    correct_len = more_itertools.first(more_itertools.windowed(msg_item, 5))
    connection_id, server_time, type, data, id = correct_len
    # print(msg_item)

    if not id:
        return

    if type == MESSAGE_RECEIVED and data:
        id = id.decode()
        server_time = datetime.datetime.fromtimestamp(server_time)
        point = {
            'server_time': server_time,
            'tk_id': id,
        }
        data = data.decode('latin1')
        assert data[0] == '('
        assert data[-1] == ')'
        data = data_split(data[1:-1])
        msg_code = data[1]
        # if msg_code == 'ZC20':
        #     point['time'] = parse_date_time(data[2], data[3])
        if msg_code[:3] == 'DW3' and data[3] == 'A':
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
            point.update(ZC03_parse(msg))

        # HACK Some trackers are out by this time sometimes.
        if 'time' in point and (server_time - point['time']).total_seconds() // 100 == 713664:
            point['time'] = point['time'] + datetime.timedelta(days=826)
        return point


status_battery_re = re.compile('Power: (\d*)%')
check_re = re.compile('Check interval is set to (\d*) minute\(s\).')
routetrack_re = re.compile('Routetrack (data is uploading|is on), Period is set to (\d*)')
routetrack_time_re = re.compile('Notice: System has entered routetrack function for (\d*) hour\(s\).')
rsampling_re = re.compile('Notice: Track sampling interval is (\d*) second\(s\).')
rupload_re = re.compile('Notice: Upload time interval is set to (\d*) second\(s\)')
check_on_re = re.compile('Notice: Check interval is set to (\d*) minute\(s\).')


def ZC03_parse(msg):
    status_battery_match = status_battery_re.search(msg)

    if status_battery_match:
        yield 'battery', int(status_battery_match.group(1))
        yield 'tk_status', msg
        check_match = check_re.search(msg)
        if check_match:
            yield 'tk_check', int(check_match.group(1))
        else:
            yield 'tk_check', False

        routetrack_match = routetrack_re.search(msg)
        if routetrack_match:
            routetrack = int(routetrack_match.group(2))
            if routetrack == 99:
                routetrack = True
            yield 'tk_routetrack', routetrack
        else:
            yield 'tk_routetrack', False
    else:
        if msg == 'Notice: System has ended routetrack function.':
            yield 'tk_routetrack', False
            return
        if msg == 'Notice: Routetrack function is set to always on':
            yield 'tk_routetrack', True
            return
        if msg == 'Notice: System has ended check function.':
            yield 'tk_check', False
            return

        routetrack_time_match = routetrack_time_re.match(msg)
        if routetrack_time_match:
            yield 'tk_routetrack', int(routetrack_time_match.group(1))
            return
        rsampling_match = rsampling_re.match(msg)
        if rsampling_match:
            yield 'tk_rsampling', int(rsampling_match.group(1))
            return
        rupload_match = rupload_re.match(msg)
        if rupload_match:
            yield 'tk_rupload', int(rupload_match.group(1))
            return
        check_on_match = check_on_re.match(msg)
        if check_on_match:
            yield 'tk_check', int(check_on_match.group(1))
            return


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
    split_index = raw.index('.') - 2
    deg = int(raw[0:split_index])
    min = float(raw[split_index:-1])
    hem = -1 if raw[-1] == neg else 1
    return (deg + min / 60) * hem


async def start_event_tracker(app, event, rider_name, tracker_data):
    return await TKStorageTracker.start(
        app, rider_name, tracker_data['id'],
        tracker_data.get('start') or event.config['tracker_start'],
        tracker_data.get('end') or event.config['tracker_end'],
        tracker_data.get('config') or event.config.get('tk_config'),
    )


def time_between(time, start, end):
    return time and (not start or start < time) and (not end or time < end)


def get_individual_key(request):
    return "tkstorage-{id}".format_map(request.match_info)


async def start_individual_tracker(app, settings, request):
    id = request.match_info['id']
    start = datetime.datetime.now() - datetime.timedelta(days=2)
    return await TKStorageTracker.start(app, id, id, start, None)


class TKStorageTracker(Tracker):

    @classmethod
    async def start(cls, app, tracker_name, id, start, end, config=None):
        tracker = cls(f'tkstorage.{id}-{tracker_name}')
        tracker.app = app
        tracker.id = id
        tracker.start = start
        tracker.end = end
        tracker.config_read_start = start - datetime.timedelta(days=1)
        tracker.current_config = {}
        tracker.send_queue = app['tkstorage.send_queue']

        # await app['tkstorage.initial_download'].wait()
        try:
            await asyncio.wait_for(app['tkstorage.initial_download'].wait(), timeout=5)
        except asyncio.TimeoutError:
            tracker.logger.error('Timeout waiting for initial download.')

        all_points = app['tkstorage.all_points']
        filtered_points = [point for _, point in all_points if point and point['tk_id'] == id]
        tracker.points_received_observables = app['tkstorage.points_received_observables'][id]
        tracker.points_received_observables.subscribe(tracker.points_received)
        tracker.completed.add_done_callback(tracker.on_completed)
        await tracker.points_received(filtered_points)

        now = datetime.datetime.now()
        tracker.initial_config_handle = None
        if config:
            if now < start:
                tracker.initial_config_handle = asyncio.get_event_loop().call_later(
                    (start - now).total_seconds(),
                    tracker.set_config_sync, config, True)
            # else:
            #     await tracker.set_config(config)

        if end:
            asyncio.get_event_loop().call_later((end - now).total_seconds(), tracker.completed.set_result, None)

        return tracker

    config_keys = {'tk_routetrack', 'tk_rupload', 'tk_rsampling', 'tk_check', }

    def consume_config_from_point(self, point):
        config_items = [(key[3:], value) for key, value in point.items() if key in self.config_keys]
        if config_items:
            c = self.current_config
            c.update(config_items)
            # Do we want to rm the config keys
            point = copy(point)
            config_texts = []
            routetrack = c.get('routetrack')
            if routetrack:
                t = 'on' if routetrack == True else f'on for {routetrack} hrs'  # NOQA
                if 'rupload' in c and 'rsampling' in c:
                    if c["rupload"] == c["rsampling"]:
                        config_texts.append(f'Routetrack {t} {c["rupload"]} sec upload')
                    else:
                        config_texts.append(f'Routetrack {t} {c["rupload"]} sec upload, {c["rsampling"]} sec sample')
                else:
                    config_texts.append(f'Routetrack {t}')
            if c.get('check'):
                config_texts.append(f'Check on {c["check"]} min upload')
            if not config_texts:
                config_texts.append(f'Off')
            point['tk_config'] = '\n'.join(config_texts)
            # print(repr(point['tk_config']))
        return point

    def use_point(self, point):
        time = point.get('time')
        server_time = point.get('server_time')
        if time_between(time, self.start, self.end) or time_between(server_time, self.start, self.end):
            return True
        if time_between(time, self.config_read_start, self.end) or time_between(server_time, self.config_read_start, self.end):
            if any((key in point for key in self.config_keys)):
                return True
        return False

    async def points_received(self, points):
        points = [self.consume_config_from_point(point) for point in points if self.use_point(point)]
        await self.new_points(points)

    def stop(self):
        self.completed.set_result(None)
        if self.initial_config_handle:
            self.initial_config_handle.cancel()

    def on_completed(self, fut):
        self.points_received_observables.unsubscribe(self.points_received)

    def set_config_sync(self, config, urgent):
        loop = asyncio.get_event_loop()
        loop.create_task(self.set_config(config, urgent=urgent))

    async def set_config(self, config, urgent=False):
        commands = []
        if 'routetrack' in config:
            routetrack = config['routetrack']
            if routetrack == True:  # NOQA
                routetrack = 99
            if routetrack:
                commands.extend((
                    f'*rupload*{config["rupload"]}*',
                    f'*rsampling*{config["rsampling"]}*',
                    f'*routetrack*{routetrack}*',
                ))
            else:
                commands.append('*routetrackoff*')
        if 'check' in config:
            check = config['check']
            if check:
                commands.append(f'*checkm*{check}*')
            else:
                commands.append(f'*checkoff*')

        await self.send_queue.put({'id': self.id, 'commands': commands, 'urgent': urgent})


async def main():
    import os.path

    app = {}
    settings = {
        'tkstorage_path': os.path.expanduser('~/dev/trackers/tkstorage_watcher'),
    }
    async with config(app, settings):
        tracker = await TKStorageTracker.start(
            app, 'gary', 'TK03', datetime.datetime(2018, 6, 5, 17), None)
        print_tracker(tracker)
        # await tracker.set_config({'check': 5})
        # await tracker.set_config({'routetrack': False, 'check': False})
        # await tracker.set_config({'routetrack': True, 'rupload': 60, 'rsampling': 60})
        # await tracker.set_config({'routetrack': 10, 'rupload': 60, 'rsampling': 60})
        # await asyncio.sleep(2)

        import signal
        run_fut = asyncio.Future()
        for signame in ('SIGINT', 'SIGTERM'):
            loop.add_signal_handler(getattr(signal, signame), run_fut.set_result, None)
        try:
            await asyncio.wait((run_fut, tracker.completed), return_when=asyncio.FIRST_COMPLETED)
        finally:
            for signame in ('SIGINT', 'SIGTERM'):
                loop.remove_signal_handler(getattr(signal, signame))
        tracker.stop()
        await tracker.complete()

if __name__ == "__main__":
    logging.basicConfig(level=logging.DEBUG)
    loop = asyncio.get_event_loop()
    loop.run_until_complete(main())
