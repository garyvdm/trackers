import asyncio
import contextlib
import json
import logging
import re
from collections import Counter, defaultdict
from contextlib import asynccontextmanager
from copy import copy
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from functools import partial
from itertools import groupby
from typing import Dict, List, Optional

import aiomsgpack
import more_itertools
from aiohttp import web, WSMsgType

from trackers.analyse import distance, Point
from trackers.base import cancel_and_wait_task, list_register, Observable, print_tracker, run_forget_task, Tracker

logger = logging.getLogger(__name__)

web_app = None


@asynccontextmanager
async def config(app, settings):
    app['tkstorage.points_received_observables'] = points_received_observables = defaultdict(partial(Observable, 'tkstorage.points_received'))
    app['tkstorage.send_queue'] = send_queue = asyncio.Queue()
    app['tkstorage.all_points_len'] = 0
    app['tkstorage.initial_download'] = initial_download_done = asyncio.Event()
    app['tkstorage.trackers_objects'] = trackers_objects = dict()
    app['tkstorage.values_changed'] = values_changed = Observable('tkstorage.values_changed')
    app['tkstorage.trackers'] = {}
    app['tkstorage.trackers_changed'] = trackers_changed = Observable('tkstorage.trackers_changed')
    app['tkstorage.sms_gateway_status'] = {}
    app['tkstorage.sms_gateway_status_changed'] = sms_gateway_status_changed = Observable('tkstorage.sms_gateway_status_changed')
    app['tkstorage.config'] = settings.get('tkstorage_config', True)

    connection_task = asyncio.ensure_future(connection(app, settings, points_received_observables, send_queue,
                                                       initial_download_done, trackers_objects, values_changed))

    if isinstance(app, web.Application):
        import trackers.web_app
        global web_app
        web_app = trackers.web_app

        app['tkstorage.admin_ws_sessions'] = []
        values_changed.subscribe(partial(send_values_changed_to_admin_ws, app))
        trackers_changed.subscribe(partial(send_trackers_changed_to_admin_ws, app))
        sms_gateway_status_changed.subscribe(partial(send_sms_gateway_status_changed_to_admin_ws, app))

        app.router.add_route('GET', '/tkstorage_admin/tkstorage_websocket', handler=admin_ws, name='tkstorage_admin_ws')
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
        for tracker_objects in trackers_objects.values():
            await tracker_objects.stop_config_apply_loop()


def get_tracker_objects(app, tk_id):
    trackers_objects = app['tkstorage.trackers_objects']
    tracker_objects = trackers_objects.get(tk_id)
    if not tracker_objects:
        tracker_objects = TrackerObjects(app, tk_id)
        trackers_objects[tk_id] = tracker_objects
        tracker_objects.start_config_apply_loop()
    return tracker_objects


@dataclass
class TrackerObjects(object):
    app: any
    tk_id: str

    points: List[Dict] = field(default_factory=list)
    config: Dict = field(default_factory=dict)
    values: Dict = field(default_factory=dict)
    active_trackers: Dict = field(default_factory=Counter)

    desired_configs: Dict = field(default_factory=dict)
    config_apply_loop_fut: Optional[asyncio.Future] = field(default=None)
    desired_configs_changed: asyncio.Event = field(default_factory=asyncio.Event)

    def get_highest_rank_desired_config(self):
        sorted_configs = sorted(self.desired_configs.keys(),
                                key=lambda config_id: self.desired_configs[config_id].rank,
                                reverse=True)
        return more_itertools.first(sorted_configs, None)

    # TODO only allow one tracker to apply config.

    def add_desired_config(self, config_id, config, rank=0):
        self.desired_configs[config_id] = DesiredConfig(config_id, config, rank)
        self.desired_configs_changed.set()

    def del_desired_config(self, config_id):
        if config_id in self.desired_configs:
            del self.desired_configs[config_id]
        self.desired_configs_changed.set()

    def _desired_changed(self):
        if self.desired_configs:
            highest_rank_config_id = self.get_highest_rank_desired_config()
            desired_config = self.desired_configs[highest_rank_config_id].config
            self.values['desired_config_text'] = config_text_from_config(desired_config)
        else:
            self.values['desired_config_text'] = ''

        self.values['desired_configs'] = self.desired_configs
        run_forget_task(self.app['tkstorage.values_changed']({self.tk_id: self.values}))

    def add_active_tracker(self, name):
        self.active_trackers.update((name, ))
        self._update_active_tracker()

    def remove_active_tracker(self, name):
        self.active_trackers.subtract((name, ))
        self._update_active_tracker()

    def _update_active_tracker(self):
        self.values['active'] = [tracker for tracker, count in self.active_trackers.items() if count]
        self.values['prev_active'] = [tracker for tracker, count in self.active_trackers.items() if not count]

        run_forget_task(self.app['tkstorage.values_changed']({self.tk_id: self.values}))

    def start_config_apply_loop(self):
        self.config_apply_loop_fut = asyncio.ensure_future(self.config_apply_loop())

    async def stop_config_apply_loop(self):
        await cancel_and_wait_task(self.config_apply_loop_fut)

    async def config_apply_loop(self):
        delay = 10
        apply_count = -1
        await asyncio.sleep(5)
        while True:
            try:
                await asyncio.sleep(5)
                if self.desired_configs_changed.is_set():
                    apply_count = -1
                    delay = 10
                    self.desired_configs_changed.clear()
                else:
                    delay = min(delay * 4, 1800)
                    apply_count += 1

                commands = await self.apply_config('first' if apply_count % 4 == 0 else False)

                self._desired_changed()

                waits = (self.desired_configs_changed.wait(), )
                if commands:
                    waits = waits + (asyncio.sleep(delay), )
                waits = tuple(asyncio.ensure_future(fut) for fut in waits)
                try:
                    await asyncio.wait(waits, return_when=asyncio.FIRST_COMPLETED)
                except asyncio.CancelledError:
                    for fut in waits:
                        await cancel_and_wait_task(fut)
                    raise
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception('')

    async def apply_config(self, urgent):
        if self.desired_configs:
            highest_rank_config_id = self.get_highest_rank_desired_config()
            desired_config = self.desired_configs[highest_rank_config_id].config

            commands = []
            desired_routetrack = desired_config.get('routetrack', False)
            if desired_routetrack != self.config.get('routetrack', False):
                if desired_routetrack == True:  # NOQA
                    desired_routetrack = 99
                if desired_routetrack:
                    commands.append(f'*routetrack*{desired_routetrack}*')
                else:
                    commands.append('*routetrackoff*')

            if desired_routetrack:
                desired_rupload = desired_config['rupload']
                if desired_rupload != self.config.get('rupload'):
                    commands.append(f'*rupload*{desired_rupload}*')
                desired_rsampling = desired_config['rsampling']
                if desired_rsampling != self.config.get('rsampling'):
                    commands.append(f'*rsampling*{desired_rsampling}*')

            desired_check = desired_config.get('check', False)
            if desired_check != self.config.get('check', False):
                if desired_check:
                    commands.append(f'*checkm*{desired_check}*')
                else:
                    commands.append('*checkoff*')

            if commands:
                verb = 'Sending' if self.app['tkstorage.config'] else 'Would send'
                action = f'{verb} commands {commands} Urgent: {urgent}'
            else:
                action = 'No action'
            logger.debug(f'{self.tk_id}: Desired: {desired_config} Current: {self.config} {action}')
            if commands and self.app['tkstorage.config']:
                await self.app['tkstorage.send_queue'].put({'id': self.tk_id, 'commands': commands, 'urgent': urgent})
            return commands


@dataclass
class DesiredConfig(object):
    config_id: str
    config: Dict
    rank: int = field(default=0)


tk_id_key = lambda point: point['tk_id']
time_key = lambda point: point.get('time') or point.get('server_time')


async def connection(app, settings, points_received_observables, send_queue,
                     initial_download_done, trackers_objects, values_changed):
    try:
        reconnect_sleep_time = 5
        connect_error_shown = False
        path = settings['tkstorage_path']
        loop = asyncio.get_event_loop()
        while True:
            try:
                logger.debug(f'Connecting to {path}')
                try:
                    _, proto = await loop.create_unix_connection(
                        aiomsgpack.make_msgpack_protocol_factory(loop=loop, unpacker_args={'raw': False},),
                        path=path,
                    )
                except FileNotFoundError as e:
                    logger.error(f'{e}: {path}')
                    break
                try:
                    connect_error_shown = False
                    reconnect_sleep_time = 1
                    proto.write({'start': app['tkstorage.all_points_len']})
                    write_fut = asyncio.ensure_future(write(app, proto, send_queue))
                    try:
                        async for msg in proto:
                            if isinstance(msg, dict):
                                if 'trackers' in msg:
                                    app['tkstorage.trackers'] = msg['trackers']
                                    await app['tkstorage.trackers_changed'](msg['trackers'])
                                    for tracker in app['tkstorage.trackers']:
                                        app['tkstorage.points_received_observables'][tracker] = Observable(f'tkstorage.points_received.{tracker}')
                                if 'sms_gateway_status' in msg:
                                    app['tkstorage.sms_gateway_status'] = msg['sms_gateway_status']
                                    await app['tkstorage.sms_gateway_status_changed'](msg['sms_gateway_status'])
                            if isinstance(msg, list):
                                logger.debug(f'Downloaded {len(msg)} points.')
                                new_points = []
                                for item in msg:
                                    try:
                                        point = msg_item_to_point(item)
                                    except Exception as e:
                                        msg = copy(item)
                                        msg[1] = datetime.fromtimestamp(msg[1])
                                        logger.error(f'Error in msg_item_to_point: msg={msg!r} \n {e}')
                                        point = None

                                        # 2019-01-26 16:20:25,339 ERROR [trackers.sources.tkstorage] Error in msg_item_to_point: msg=[2916, 1548434791.840158, 1, '(864768011468128,DW3B,000000,A,2751.53042S,02740.57318E,5.931,164603,190.00,1470.40,11,0)', 'TK13']  month must be in 1..12
                                        # 2019-01-26 16:20:26,874 ERROR [trackers.sources.tkstorage] Error in msg_item_to_point: msg=[11115, 1548481354.80168, 1, '(864768011468284,DW3B,000000,A,2738.40946S,02758.44679E,3.529,054234,113.02,1602.00,13,0)', 'TK28'] month must be in 1..12
                                        # 2019-01-26 16:20:27,112 ERROR [trackers.sources.tkstorage] Error in msg_item_to_point: msg=[12537, 1548493482.802952, 1, '(864768011468169,DW30,000000,A,2751.23359S,02800.88446E,16.810,090045,228.83,1628.60,12,0)', 'TK10'] month must be in 1..12

                                    if point and point.get('tk_id'):
                                        new_points.append(point)

                                new_points.sort(key=time_key)
                                new_points = [consume_config_from_point(app, point) for point in new_points]

                                trackers_values_changed = set()
                                for point in new_points:
                                    tk_id = point['tk_id']
                                    trackers_values_changed.add(tk_id)
                                    tracker_values = get_tracker_objects(app, point['tk_id']).values
                                    tracker_values['last_connection'] = point['server_time']
                                    for key in ('tk_status', 'tk_config', 'position', 'battery'):
                                        if key in point:
                                            tracker_values[key] = {'value': point[key], 'time': point['time']}
                                await values_changed({tk_id: trackers_objects[tk_id].values for tk_id in trackers_values_changed})

                                for tk_id, tracker_points in groupby(sorted(new_points, key=tk_id_key), key=tk_id_key):
                                    observable = points_received_observables.get(tk_id)
                                    tracker_points = list(tracker_points)
                                    trackers_objects[tk_id].points.extend(tracker_points)

                                    if observable:
                                        await observable(tracker_points)

                                initial_download_done.set()

                    finally:
                        await cancel_and_wait_task(write_fut)
                finally:
                    proto.close()
            except asyncio.CancelledError:
                raise
            except ConnectionRefusedError as e:
                if not connect_error_shown:
                    logger.error(f'Error in connection task: {e}')
                    connect_error_shown = True
                else:
                    logger.debug(f'Error in connection task: {e}')
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

zc20_battery_levels = {
    '6': 100,
    '5': 80,
    '4': 50,
    '3': 20,
    '2': 10,
    '1': 0,
}


def msg_item_to_point(msg_item):
    correct_len = more_itertools.first(more_itertools.windowed(msg_item, 5))
    connection_id, server_time, type, data, id = correct_len
    if isinstance(id, bytes):
        id = id.decode()
    # print(msg_item)

    if not id:
        return

    if type == MESSAGE_RECEIVED and data:
        server_time = datetime.fromtimestamp(server_time)
        point = {
            'server_time': server_time,
            'tk_id': id,
        }
        assert data[0] == '('
        assert data[-1] == ')'
        data = data_split(data[1:-1])
        msg_code = data[1]
        if msg_code == 'ZC20':
            point['time'] = parse_date_time(data[2], data[3], server_time)
            # battery_level = zc20_battery_levels.get(data[4])
            battery_voltage = int(data[5])
            # power_voltage = int(data[6])
            # installed = int(data[7])
            # if battery_level is not None:
            #     point['battery'] = battery_level
            if battery_voltage != 65535:
                point['battery_voltage'] = battery_voltage * 0.01
                point['battery'] = ((battery_voltage * 0.01) - 3.4) / 0.75 * 100

        if msg_code[:3] == 'DW3' and data[3] == 'A':
            lat = parse_coordinate(data[4], 'S')
            lng = parse_coordinate(data[5], 'W')
            alt = float(data[9])
            point['time'] = parse_date_time(data[2], data[7], server_time)
            point['position'] = (lat, lng, alt) if alt else (lat, lng)

            point['num_sat'] = int(data[10])
        if msg_code == 'ZC03':
            point['time'] = parse_date_time(data[2], data[3], server_time)
            msg = data[4]
            # print(msg)
            point.update(ZC03_parse(msg))

        # HACK Some trackers are out by this time sometimes.
        if 'time' in point and (server_time - point['time']).total_seconds() // 100 == 713664:
            point['time'] = point['time'] + timedelta(days=826)

        # HACK Some trackers are out by one week.
        if 'time' in point and (server_time.date() - point['time'].date()) == timedelta(weeks=1):
            point['time'] = point['time'] + timedelta(weeks=1)

        return point


status_battery_re = re.compile(r'Power: (\d*)%')
check_re = re.compile(r'Check interval is set to (\d*) minute\(s\).')
routetrack_re = re.compile(r'Routetrack (data is uploading|is on), Period is set to (\d*)')
routetrack_time_re = re.compile(r'Notice: System has entered routetrack function for (\d*) hour\(s\).')
rsampling_re = re.compile(r'Notice: Track sampling interval is (\d*) second\(s\).')
rupload_re = re.compile(r'Notice: Upload time interval is set to (\d*) second\(s\)')
check_on_re = re.compile(r'Notice: Check interval is set to (\d*) minute\(s\).')


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


def parse_date_time(date_raw: str, time_raw: str, server_time: datetime):
    # TODO pay attention to time zone.
    date_raw = date_raw
    time_raw = time_raw

    hour = int(time_raw[0:2])
    minutes = int(time_raw[2:4])
    sec = int(time_raw[4:6])

    if date_raw == '000000':
        utc_server_time = server_time.astimezone(timezone.utc)
        utc_time00 = datetime(utc_server_time.year, utc_server_time.month, utc_server_time.day,
                              hour, minutes, sec, tzinfo=timezone.utc)
        utc_timeP1 = utc_time00 + timedelta(days=1)
        utc_timeM1 = utc_time00 + timedelta(days=-1)

        utc_time = min(utc_time00, utc_timeP1, utc_timeM1, key=lambda t: abs((utc_server_time - t).total_seconds()))
    else:
        day = int(date_raw[0:2])
        month = int(date_raw[2:4])
        year = int(date_raw[4:6]) + 2000
        utc_time = datetime(year, month, day, hour, minutes, sec, tzinfo=timezone.utc)

    return utc_time.astimezone().replace(tzinfo=None)


def parse_coordinate(raw, neg):
    split_index = raw.index('.') - 2
    deg = int(raw[0:split_index])
    min = float(raw[split_index:-1])
    hem = -1 if raw[-1] == neg else 1
    return (deg + min / 60) * hem


config_keys = {'tk_routetrack', 'tk_rupload', 'tk_rsampling', 'tk_check', }


def config_text_from_config(c):
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
        config_texts.append('Off')
    return '\n'.join(config_texts)


# TODO make this a method of TrackerObjects
def consume_config_from_point(app, point):
    config_items = [(key[3:], value) for key, value in point.items() if key in config_keys]
    if config_items:
        c = get_tracker_objects(app, point['tk_id']).config
        c.update(config_items)
        # Do we want to rm the config keys
        point = copy(point)
        point['tk_config'] = config_text_from_config(c)
        # print(repr(point['tk_config']))
    return point


async def start_event_tracker(app, event, rider_name, tracker_data, start, end):
    return await TKStorageTracker.start(
        app, rider_name, tracker_data['id'], start, end,
        tracker_data.get('config') or event.config.get('tk_config') or {},
    )


def time_between(time, start, end):
    return time and (not start or start < time) and (not end or time < end)


def get_individual_key(request):
    return "tkstorage-{id}".format_map(request.match_info)


async def start_individual_tracker(app, settings, request):
    id = request.match_info['id']
    start = datetime.now() - timedelta(days=2)
    return await TKStorageTracker.start(app, id, id, start, None)


class TKStorageTracker(Tracker):

    @classmethod
    async def start(cls, app, tracker_name, id, start, end, config={}):
        tracker = cls(f'tkstorage.{id}-{tracker_name}')
        tracker.tracker_name = tracker_name
        tracker.app = app
        tracker.id = id
        tracker.start = start
        tracker.end = end
        tracker.config_read_start = start - timedelta(days=14)
        tracker.send_queue = app['tkstorage.send_queue']
        tracker.config = config or {}
        tracker.config_rules = tracker.config.get('rules', [])

        tracker.objects = tracker_objects = get_tracker_objects(app, id)
        tracker.objects.add_active_tracker(tracker_name)

        # await app['tkstorage.initial_download'].wait()
        try:
            await asyncio.wait_for(app['tkstorage.initial_download'].wait(), timeout=5)
        except asyncio.TimeoutError:
            tracker.logger.error('Timeout waiting for initial download.')

        tracker.points_received_observables = app['tkstorage.points_received_observables'][id]
        tracker.points_received_observables.subscribe(tracker.points_received)
        tracker.completed.add_done_callback(tracker.on_completed)
        await tracker.points_received(tracker_objects.points)

        now = datetime.now()
        tracker.initial_config_task = None

        base_start = config.get('base', {}).get('start', start)
        if base_start and now < end:
            if now > start:
                base_start = base_start + timedelta(seconds=60)
            delay = (now - base_start).total_seconds()
            tracker.initial_config_task = run_forget_task(tracker.set_base_config(delay))

        if end:
            tracker.complete_on_end_time_reached_task = run_forget_task(tracker.complete_on_end_time_reached())

        return tracker

    def use_point(self, point):
        time = point.get('time')
        server_time = point.get('server_time')
        if point['tk_id'] == 'TK24' and time == datetime(2019, 6, 23, 2, 0, 2):
            return False
        if time_between(time, self.start, self.end):
            return True
        if time_between(time, self.config_read_start, self.end) or time_between(server_time, self.config_read_start, self.end):
            if any((key in point for key in config_keys)):
                return True
        # print(point)
        return False

    async def points_received(self, points):
        points = [point for point in points if self.use_point(point)]
        await self.new_points(points)

        if self.config_rules:
            points_with_position = [point for point in points if 'position' in point]
            if points_with_position:
                last_poistion = points_with_position[-1]['position']

                for i, rule in enumerate(self.config_rules):
                    if rule['type'] == 'within_dist_to_point':
                        dist = distance(Point(*rule['point']), Point(*last_poistion[:2]))
                        if dist < rule['dist']:
                            self.logger.debug(f'Using config rule {i}')
                            self.objects.add_desired_config('rules', rule['config'], rank=20)
                            break
                else:
                    self.logger.debug('Clear config rule.')
                    self.objects.del_desired_config('rules')

    def stop(self):
        if not self.completed.done():
            self.completed.set_result(None)

    def set_finished(self):
        super().set_finished()
        if not self.completed.done():
            for rule in self.config_rules:
                if rule['type'] == 'finished':
                    self.objects.add_desired_config('finished', rule['config'], rank=30)

    def reset_points(self):
        super().reset_points()
        self.objects.del_desired_config('rules')
        self.objects.del_desired_config('finished')

    def on_completed(self, fut):
        if self.initial_config_task:
            self.initial_config_task.cancel()

        if self.complete_on_end_time_reached_task:
            self.complete_on_end_time_reached_task.cancel()

        self.points_received_observables.unsubscribe(self.points_received)

        self.objects.del_desired_config('base_config')
        self.objects.del_desired_config('rules')
        self.objects.del_desired_config('finished')

        self.objects.remove_active_tracker(self.tracker_name)

        # TODO maybe do this if finished but not stopped
        # asyncio.ensure_future(self.objects.del_desired_config('base_config'))

    async def complete_on_end_time_reached(self):
        await asyncio.sleep((self.end - datetime.now()).total_seconds())
        await self.app['tkstorage.initial_download'].wait()
        self.completed.set_result(None)

    async def set_base_config(self, delay):
        await asyncio.sleep(delay)
        if self.config and 'base' in self.config:
            self.logger.debug('Setting base config')
            self.objects.add_desired_config('base_config', self.config['base'], rank=0)


async def admin_ws(request):
    app = request.app
    send_queue = app['tkstorage.send_queue']
    trackers_objects = app['tkstorage.trackers_objects']
    ws = web.WebSocketResponse()
    ws.subscriptions = set()
    await ws.prepare(request)
    with contextlib.ExitStack() as exit_stack:
        try:
            exit_stack.enter_context(list_register(app['trackers.ws_sessions'], ws))
            await web_app.message_to_multiple_wss(app, [ws], {
                'values': {objects.tk_id: objects.values for objects in trackers_objects.values()},
                'trackers': app['tkstorage.trackers'],
                'sms_gateway_status': app['tkstorage.sms_gateway_status'],
            })
            exit_stack.enter_context(list_register(app['tkstorage.admin_ws_sessions'], ws))
            async for msg in ws:
                if msg.type == WSMsgType.text:
                    try:
                        logger.debug('receive: {}'.format(msg.data))
                        data = json.loads(msg.data)
                        if 'commands' in data:
                            await send_queue.put(data)
                        if 'config' in data:
                            tracker_objects = get_tracker_objects(app, data['id'])
                            tracker_objects.add_desired_config('admin_console', data['config'], rank=100)
                        if 'del_config' in data:
                            tracker_objects = get_tracker_objects(app, data['id'])
                            tracker_objects.del_desired_config('admin_console')

                    except Exception:
                        request.app['exception_recorder']()
                        logger.exception('Error in receive ws msg:')

                if msg.type == WSMsgType.close:
                    await ws.close()
                if msg.type == WSMsgType.error:
                    raise ws.exception()
        except asyncio.CancelledError:
            pass
        except Exception as e:
            request.app['exception_recorder']()
            await ws.close(message='Server Error: {}'.format(e))
            logger.exception('Error in tkstorage_admin_ws: ')
        finally:
            return ws


async def send_values_changed_to_admin_ws(app, values_changed):
    await web_app.message_to_multiple_wss(app, app['tkstorage.admin_ws_sessions'], {'changed_values': values_changed})


async def send_trackers_changed_to_admin_ws(app, trackers):
    await web_app.message_to_multiple_wss(app, app['tkstorage.admin_ws_sessions'], {'trackers': trackers})


async def send_sms_gateway_status_changed_to_admin_ws(app, sms_gateway_status):
    await web_app.message_to_multiple_wss(app, app['tkstorage.admin_ws_sessions'], {'sms_gateway_status': sms_gateway_status})


async def main():
    import os.path

    app = {}
    settings = {
        'tkstorage_path': os.path.expanduser('~/dev/trackers/tkstorage_watcher'),
    }
    async with config(app, settings):
        tracker = await TKStorageTracker.start(
            app, 'gary', 'TK03', datetime(2018, 6, 5, 17), None)
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
