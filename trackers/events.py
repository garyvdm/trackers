import asyncio
import base64
import datetime
import hashlib
import logging
import os
from bisect import bisect
from contextlib import closing, suppress
from functools import partial

import aionotify
import msgpack
import yaml

from trackers.analyse import AnalyseTracker, get_analyse_routes
from trackers.base import BlockedList, cancel_and_wait_task, Observable
from trackers.dulwich_helpers import TreeReader, TreeWriter
from trackers.general import index_and_hash_tracker, start_replay_tracker
from trackers.persisted_func_cache import PersistedFuncCache

logger = logging.getLogger(__name__)


async def load_events_with_watcher(app, ref=b'HEAD', **kwargs):
    try:
        await load_events(app, ref=ref, **kwargs)

        repo = app['trackers.data_repo']

        while True:
            refnames, sha = repo.refs.follow(ref)
            paths = [repo.refs.refpath(ref) for ref in refnames]
            logger.debug(f'Watching paths {paths}')

            try:
                with closing(aionotify.Watcher()) as watcher:
                    await watcher.setup(asyncio.get_event_loop())
                    for path in paths:
                        watcher.watch(path, flags=aionotify.Flags.MODIFY + aionotify.Flags.DELETE_SELF + aionotify.Flags.MOVE_SELF)
                    await watcher.get_event()
            except OSError as e:
                logger.error(e)
                break

            await asyncio.sleep(0.1)

            new_sha = repo.refs[ref]
            if sha != new_sha:
                logger.info('Ref {} changed {} -> {}. Reloading.'.format(ref.decode(), sha.decode()[:6], new_sha.decode()[:6]))
                await load_events(app, ref=ref, **kwargs)
    except asyncio.CancelledError:
        raise
    except Exception:
        logger.exception('Error in load_events_with_watcher: ')


async def load_events(app, ref=b'HEAD', new_event_observable=Observable(logger), removed_event_observable=Observable(logger)):
    events = app['trackers.events']
    try:
        tree_reader = TreeReader(app['trackers.data_repo'], treeish=ref)
    except KeyError:
        pass
    else:
        names = set(tree_reader.tree_items('events'))
        for name in events.keys() - names:
            await events[name].stop_and_complete_trackers()
            await removed_event_observable(events.pop(name))
        for name in names:
            try:
                if name in events:
                    await events[name].reload(tree_reader)
                else:
                    events[name] = event = await Event.load(app, name, tree_reader)
                    await new_event_observable(event)
            except yaml.YAMLError as e:
                logger.error(f'Error loading {name!r}: {e}')
            except Exception:
                logger.exception(f'Error loading {name!r}: ')
        logger.info('Events loaded.')


def hash_bytes(b):
    return base64.urlsafe_b64encode(hashlib.sha1(b).digest()).decode('ascii')


class Event(object):
    def __init__(self, app, name, config=None, routes=None):
        self.name = name
        self.app = app
        self.logger = logging.getLogger(f'event.{name}')

        self.trackers_started = False
        self.starting_fut = None
        self.predicted_task = None

        self.config_routes_change_observable = Observable(self.logger)
        self.rider_new_points_observable = Observable(self.logger)
        self.rider_blocked_list_update_observable = Observable(self.logger)
        self.rider_off_route_blocked_list_update_observable = Observable(self.logger)
        self.rider_predicted_updated_observable = Observable(self.logger)
        self.new_points = asyncio.Event()

        self.path = os.path.join('events', name)
        self.routes_path = os.path.join(self.path, 'routes')
        self.git_hash = None

        self.config_path = os.path.join(self.path, 'data.yaml')
        self.config = config
        self.config_hash = hash_bytes(yaml.dump(config).encode()) if config else None

        self.routes_path = os.path.join(self.path, 'routes')
        self.routes = routes
        self.routes_hash = hash_bytes(msgpack.dumps(routes)) if routes else None

    @classmethod
    async def load(cls, app, name, tree_reader):
        event = Event(app, name)
        await event._load(tree_reader)
        return event

    async def reload(self, tree_reader):
        _, git_hash = tree_reader.lookup(self.path)
        if self.git_hash != git_hash:
            await self.stop_and_complete_trackers()
            await self._load(tree_reader)

    async def _load(self, tree_reader):
        if self.starting_fut or self.trackers_started:
            raise Exception("Can't load while starting or started.")

        _, self.git_hash = tree_reader.lookup(self.path)
        config_bytes = tree_reader.get(self.config_path).data
        self.config = yaml.load(config_bytes.decode())
        self.config_hash = hash_bytes(config_bytes)

        if tree_reader.exists(self.routes_path):
            routes_bytes = tree_reader.get(self.routes_path).data
            self.routes = msgpack.loads(routes_bytes, raw=False)
            self.routes_hash = hash_bytes(routes_bytes)
        else:
            self.routes = []
            self.routes_hash = None

        await self.config_routes_change_observable(self)

    def save(self, message, author=None, tree_writer=None):
        if tree_writer is None:
            tree_writer = TreeWriter(self.app['trackers.data_repo'])
        config_text = yaml.dump(self.config, default_flow_style=False, Dumper=YamlEventDumper)
        tree_writer.set_data(self.config_path, config_text.encode())

        if self.routes:
            routes_bytes = msgpack.dumps(self.routes)
            tree_writer.set_data(self.routes_path, routes_bytes)
        else:
            if tree_writer.exists(self.routes_path):
                tree_writer.remove(self.routes_path)
        tree_writer.commit(message, author=author)

    async def start_trackers(self, analyse=True):
        if self.starting_fut:
            await self.starting_fut

        if not self.trackers_started:
            self.starting_fut = asyncio.ensure_future(self._start_trackers(analyse))
            try:
                await self.starting_fut
            finally:
                self.starting_fut = None

    async def _start_trackers(self, analyse):
        await asyncio.sleep(1)
        self.logger.info('Starting {}'.format(self.name))

        # analyse = self.config.get('analyse', False)
        replay = self.config.get('replay', False)
        is_live = self.config.get('live', False)
        self.event_start = self.config.get('event_start')

        self.rider_trackers = {}
        self.rider_trackers_blocked_list = {}
        self.rider_current_values = {}
        self.rider_off_route_trackers = {}
        self.rider_off_route_blocked_list = {}
        self.riders_predicted_points = {}

        if analyse:
            self.rider_analyse_trackers = {}

            loop = asyncio.get_event_loop()
            analyse_routes = await loop.run_in_executor(None, get_analyse_routes, self.routes)

            find_closest_cache_dir = os.path.join(self.app['trackers.settings']['cache_path'], 'find_closest')
            os.makedirs(find_closest_cache_dir, exist_ok=True)
            if self.routes:
                find_closest_cache = PersistedFuncCache(os.path.join(find_closest_cache_dir, self.routes_hash))
            else:
                find_closest_cache = None

        if replay:
            replay_config = replay if isinstance(replay, dict) else {}
            replay_kwargs = {
                'replay_start': datetime.datetime.now(),
                'speed_multiply': replay_config.get('speed_multiply', 50),
                'offset': datetime.timedelta(**replay_config.get('offset', {})),
                'event_start_time': self.event_start,
            }
            self.event_start = replay_kwargs['replay_start'] + replay_kwargs['offset']
            self.config_hash = hash_bytes(yaml.dump(self.config).encode())

        for rider in self.config['riders']:
            if rider['tracker']:
                start_tracker = self.app['start_event_trackers'][rider['tracker']['type']]
                tracker = await start_tracker(self.app, self, rider['name'], rider['tracker'])
                if replay:
                    tracker = await start_replay_tracker(tracker, **replay_kwargs)
                if analyse:
                    tracker = await AnalyseTracker.start(tracker, self.event_start, analyse_routes, find_closest_cache=find_closest_cache)
                    self.rider_analyse_trackers[rider['name']] = tracker
                    self.rider_off_route_trackers[rider['name']] = off_route_tracker = await index_and_hash_tracker(tracker.off_route_tracker)
                    self.rider_off_route_blocked_list[rider['name']] = BlockedList.from_tracker(
                        off_route_tracker, entire_block=not is_live,
                        new_update_callbacks=(partial(self.rider_off_route_blocked_list_update_observable, self, rider['name']), ))

                tracker = await index_and_hash_tracker(tracker)
                self.rider_current_values[rider['name']] = {}
                await self.on_rider_new_points(rider['name'], tracker, tracker.points)
                tracker.new_points_observable.subscribe(partial(self.on_rider_new_points, rider['name']))

                self.rider_trackers[rider['name']] = tracker
                self.rider_trackers_blocked_list[rider['name']] = BlockedList.from_tracker(
                    tracker, entire_block=not is_live,
                    new_update_callbacks=(partial(self.rider_blocked_list_update_observable, self, rider['name']), ))
        if analyse and is_live:
            self.predicted_task = asyncio.ensure_future(self.predicted())
        self.trackers_started = True

    async def stop_and_complete_trackers(self):
        if self.starting_fut:
            await cancel_and_wait_task(self.starting_fut)

        if self.trackers_started:
            if self.predicted_task:
                await cancel_and_wait_task(self.predicted_task)
                self.predicted_task = None

            for tracker in self.rider_trackers.values():
                tracker.stop()
            for tracker in self.rider_trackers.values():
                try:
                    await tracker.complete()
                except Exception:
                    self.logger.exception('Unhandled tracker error: ')

            del self.rider_trackers
            del self.rider_trackers_blocked_list
            del self.rider_current_values
            with suppress(AttributeError):
                del self.rider_analyse_trackers

            self.trackers_started = False

    async def on_rider_new_points(self, rider_name, tracker, new_points):
        if new_points:
            values = self.rider_current_values[rider_name]
            for point in new_points:
                values.update(point)
                if 'position' in point:
                    values['position_time'] = point['time']
            await self.rider_new_points_observable(self, rider_name, tracker, new_points)
        self.new_points.set()

    def rider_sort_key_func(self, riders_predicted_points, rider_name):
        rider_values = self.rider_current_values.get(rider_name, {})
        finished = 'finished_time' in rider_values
        time_to_finish = rider_values['finished_time'] - self.event_start if finished else None
        has_dist_on_route = 'dist_route' in rider_values
        dist_on_route = riders_predicted_points.get(rider_name, {}).get('dist_route') or rider_values.get('dist_route', 0)
        return not finished, time_to_finish, not has_dist_on_route, 0 - dist_on_route

    async def predicted(self):
        inactive_time = datetime.timedelta(minutes=15)
        if not self.rider_analyse_trackers:
            return
        while True:
            with suppress(asyncio.TimeoutError):
                await asyncio.wait_for(self.new_points.wait(), 10)

            try:
                time = datetime.datetime.now()
                riders_predicted_points = {rider_name: tracker.get_predicted_position(time) or {}
                                           for rider_name, tracker in self.rider_analyse_trackers.items()}
                sort_key_func = partial(self.rider_sort_key_func, riders_predicted_points)
                rider_names_sorted = list(sorted(riders_predicted_points.keys(), key=sort_key_func))

                leader = rider_names_sorted[0]
                leader_points = []
                last_point = None
                for point in self.rider_trackers[leader].points:
                    if 'dist_route' in point:
                        going_forward = point['dist_route'] > last_point['dist_route'] if last_point else True
                        if going_forward:
                            leader_points.append((point['dist_route'], point['time']))
                            last_point = point
                if 'dist_route' in riders_predicted_points[leader]:
                    leader_points.append((riders_predicted_points[leader]['dist_route'], time))

                if leader_points:
                    for rider_name in rider_names_sorted[1:]:
                        rider_predicted_points = riders_predicted_points.get(rider_name)
                        rider_values = self.rider_current_values.get(rider_name)
                        if rider_values and time - rider_values.get('position_time') < inactive_time:
                            rider_dist_route = None
                            rider_time = None
                            if rider_predicted_points and 'dist_route' in rider_predicted_points:
                                rider_dist_route = rider_predicted_points['dist_route']
                                rider_time = time
                            elif rider_values and 'dist_route' in rider_values:
                                rider_dist_route = rider_values['dist_route']
                                rider_time = time
                            if rider_dist_route:
                                i = bisect(leader_points, (rider_dist_route, ))
                                if i < len(leader_points):
                                    point1 = leader_points[i - 1]
                                    point2 = leader_points[i]
                                    try:
                                        interpolate = (rider_dist_route - point1[0]) / (point2[0] - point1[0])
                                    except FloatingPointError:
                                        pass
                                    else:
                                        interpolated_time = ((point2[1] - point1[1]) * interpolate) + point1[1]
                                        time_diff = rider_time - interpolated_time
                                        rider_predicted_points['leader_time_diff'] = time_diff.total_seconds()

                self.riders_predicted_points = {key: value for key, value in riders_predicted_points.items() if value}
                await self.rider_predicted_updated_observable(self, self.riders_predicted_points, time)
            except asyncio.CancelledError:
                raise
            except Exception:
                self.logger.exception('Error in predicted:')
            self.new_points.clear()


dict_key_order = {
    # Event
    'title': -10,
    'event_start': -9,
    'tracker_start': -8,
    'tracker_end': -7,
    'live': -6,
    'analyse': -5,
    'replay': -4,
    'riders': 10,

    # Rider
    'name': -5,
    'name_short': -4,

    # Tracker
    'type': -10,
}


def dict_key_order_key(item):
    key, value = item
    order = dict_key_order.get(key, 0)
    return order, key


def yaml_represent_dict(self, dict):
    # Control the order of the key
    mapping = sorted(dict.items(), key=dict_key_order_key)
    return self.represent_mapping('tag:yaml.org,2002:map', mapping)


class YamlEventDumper(yaml.Dumper):
    pass


YamlEventDumper.add_representer(dict, yaml_represent_dict)
