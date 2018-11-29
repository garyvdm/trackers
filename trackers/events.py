import asyncio
import copy
import datetime
import logging
import os
from bisect import bisect
from collections import defaultdict
from contextlib import closing, suppress
from functools import partial
from itertools import chain

import aionotify
import attr
import msgpack
import yaml

from trackers.analyse import AnalyseTracker, get_analyse_routes
from trackers.base import BlockedList, cancel_and_wait_task, Observable, Tracker
from trackers.combined import Combined
from trackers.dulwich_helpers import TreeReader, TreeWriter
from trackers.general import hash_bytes, index_and_hash_tracker, start_replay_tracker
from trackers.persisted_func_cache import PersistedFuncCache

logger = logging.getLogger(__name__)


async def load_events_with_watcher(app, ref=b'HEAD', **kwargs):
    try:
        await load_events(app, ref=ref, **kwargs)

        repo = app['trackers.data_repo']

        if hasattr(repo.refs, 'refpath'):
            while True:
                refnames, sha = repo.refs.follow(ref)
                paths = [repo.refs.refpath(ref) for ref in refnames]
                logger.debug(f'Watching paths {paths}')

                try:
                    with closing(aionotify.Watcher()) as watcher:
                        await watcher.setup(asyncio.get_event_loop())
                        for path in paths:
                            watcher.watch(path.decode(), flags=aionotify.Flags.MODIFY + aionotify.Flags.DELETE_SELF + aionotify.Flags.MOVE_SELF)
                        await watcher.get_event()
                except OSError as e:
                    logger.error(e)
                    break

                await asyncio.sleep(0.1)

                new_sha = repo.refs[ref]
                if sha != new_sha:
                    logger.info('Ref {} changed {} -> {}. Reloading.'.format(ref.decode(), sha.decode()[:6], new_sha.decode()[:6]))
                    await load_events(app, ref=ref, **kwargs)
        else:
            logger.debug('No inotify reload on memory repo')
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
        load_event_fs = [load_event(name, app, events, tree_reader, new_event_observable) for name in names]
        if load_event_fs:
            await asyncio.wait(load_event_fs)
        logger.info('Events loaded.')


async def load_event(name, app, events, tree_reader, new_event_observable):
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


class Event(object):
    def __init__(self, app, name, config=None, routes=None):
        self.name = name
        self.app = app
        self.logger = logging.getLogger(f'event.{name}')

        self.trackers_started = False
        self.starting_fut = None
        self.predicted_task = None

        self.config_routes_change_observable = Observable(self.logger)
        self.rider_new_values_observable = Observable(self.logger)
        self.rider_blocked_list_update_observable = Observable(self.logger)
        self.rider_off_route_blocked_list_update_observable = Observable(self.logger)
        self.rider_predicted_updated_observable = Observable(self.logger)
        self.new_points = asyncio.Event()

        self.path = os.path.join('events', name)
        self.git_hash = None

        self.config_path = os.path.join(self.path, 'data.yaml')
        self.config = config

        self.routes_msgpack_path = os.path.join(self.path, 'routes')
        self.routes_yaml_path = os.path.join(self.path, 'routes.yaml')

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
            self.logger.info('Reloading')
            await self.stop_and_complete_trackers()
            await self._load(tree_reader)

    async def _load(self, tree_reader):
        if self.starting_fut or self.trackers_started:
            raise Exception("Can't load while starting or started.")

        _, self.git_hash = tree_reader.lookup(self.path)
        config_bytes = tree_reader.get(self.config_path).data
        self.config = yaml.load(config_bytes.decode())

        if tree_reader.exists(self.routes_yaml_path):
            self.routes = yaml.load(tree_reader.get(self.routes_yaml_path).data)
            for route in self.routes:
                if route.get('data_hash'):
                    route_data_path = os.path.join(self.path, 'routes_data', route.get('data_hash'))
                    route_data = msgpack.loads(tree_reader.get(route_data_path).data, raw=False)
                    route.update(route_data)
                    del route['data_hash']
            self.routes_hash = hash_bytes(msgpack.dumps(self.routes))
        elif tree_reader.exists(self.routes_msgpack_path):
            routes_bytes = tree_reader.get(self.routes_msgpack_path).data
            self.routes = msgpack.loads(routes_bytes, raw=False)
            self.routes_hash = hash_bytes(routes_bytes)
        else:
            self.routes = []
            self.routes_hash = None

        await self.config_routes_change_observable(self)

    async def save(self, message, author=None, tree_writer=None, save_routes=False):
        if tree_writer is None:
            tree_writer = TreeWriter(self.app['trackers.data_repo'])
        config_bytes = yaml.dump(self.config, default_flow_style=False, Dumper=YamlEventDumper).encode()
        tree_writer.set_data(self.config_path, config_bytes)

        if save_routes:
            if tree_writer.exists(self.routes_msgpack_path):
                tree_writer.remove(self.routes_msgpack_path)

            routes_data_path = os.path.join(self.path, 'routes_data')
            if tree_writer.exists(routes_data_path):
                tree_writer.remove(routes_data_path)

            routes = [copy.copy(route) for route in self.routes]
            for route in routes:
                route_data = {}
                for key in ('original_points', 'points', 'simplified_points_indexes', 'elevation'):
                    if key in route:
                        route_data[key] = route[key]
                        del route[key]
                route_data_bytes = msgpack.dumps(route_data)
                route_data_hash = hash_bytes(route_data_bytes)
                route_data_path = os.path.join(routes_data_path, route_data_hash)
                tree_writer.set_data(route_data_path, route_data_bytes)
                route['data_hash'] = route_data_hash

            routes_bytes = yaml.dump(routes, default_flow_style=False, Dumper=YamlEventDumper).encode()
            tree_writer.set_data(self.routes_yaml_path, routes_bytes)

        tree_writer.commit(message, author=author)
        _, self.git_hash = tree_writer.lookup(self.path)
        await self.config_routes_change_observable(self)

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
        self.logger.info('Starting {}'.format(self.name))

        # analyse = self.config.get('analyse', False)
        replay = self.config.get('replay', False)
        is_live = self.config.get('live', False)
        self.event_start = self.config.get('event_start')

        self.riders_objects = {}
        self.riders_current_values = {}
        self.riders_predicted_points = {}

        if analyse:
            self.rider_analyse_trackers = {}

            loop = asyncio.get_event_loop()
            analyse_routes = await loop.run_in_executor(None, get_analyse_routes, self.routes)

            find_closest_cache_dir = os.path.join(self.app['trackers.settings']['cache_path'], 'find_closest')
            os.makedirs(find_closest_cache_dir, exist_ok=True)
            if self.routes:
                find_closest_cache = PersistedFuncCache(os.path.join(find_closest_cache_dir, self.routes_hash))
                logger.info(f'find_closest_cache: {find_closest_cache.path}')
            else:
                find_closest_cache = None

        if replay:
            replay_config = replay if isinstance(replay, dict) else {}
            replay_kwargs = {
                'replay_start': datetime.datetime.now(),
                'speed_multiply': replay_config.get('speed_multiply', 2),
                'offset': datetime.timedelta(**replay_config.get('offset', {})),
                'event_start_time': self.event_start,
            }
            self.event_start = replay_kwargs['replay_start'] + replay_kwargs['offset']

        rider_tracker_start_fs = defaultdict(list)
        for rider in self.config['riders']:
            rider_name = rider['name']

            if 'tracker' in rider and rider['tracker']:
                start_tracker = self.app['start_event_trackers'][rider['tracker']['type']]
                start_fut = asyncio.ensure_future(start_tracker(self.app, self, rider_name, rider['tracker']))
                rider_tracker_start_fs[rider_name].append(start_fut)

            for tracker in rider.get('trackers', ()):
                start_tracker = self.app['start_event_trackers'][tracker['type']]
                start_fut = asyncio.ensure_future(start_tracker(self.app, self, rider_name, tracker))
                rider_tracker_start_fs[rider_name].append(start_fut)

        all_start_fs = list(chain.from_iterable(rider_tracker_start_fs.values()))
        if all_start_fs:
            await asyncio.wait(all_start_fs)

        for rider in self.config['riders']:
            rider_name = rider['name']
            self.riders_objects[rider_name] = objects = RiderObjects(rider_name, self)
            objects.data_tracker = await DataTracker.start(rider)

            for start_fut in rider_tracker_start_fs[rider_name]:
                objects.source_trackers.append(start_fut.result())

            tracker = await Combined.start(f'combined.{rider_name}', [objects.data_tracker] + objects.source_trackers)
            if replay:
                tracker = await start_replay_tracker(tracker, **replay_kwargs)
            if analyse and rider.get('type', 'rider') == 'rider':
                objects.analyse_tracker = tracker = await AnalyseTracker.start(
                    tracker, self.event_start, analyse_routes, find_closest_cache=find_closest_cache)
                objects.off_route_tracker = off_route_tracker = await index_and_hash_tracker(tracker.off_route_tracker)
                objects.off_route_blocked_list = BlockedList.from_tracker(
                    off_route_tracker, entire_block=not is_live,
                    new_update_callbacks=(partial(self.rider_off_route_blocked_list_update_observable, self, rider['name']), ))

            tracker = await index_and_hash_tracker(tracker)
            self.riders_current_values[rider['name']] = {}
            await self.on_rider_new_points(rider['name'], tracker, tracker.points)
            tracker.new_points_observable.subscribe(partial(self.on_rider_new_points, rider['name']))
            tracker.reset_points_observable.subscribe(partial(self.on_rider_reset_points, rider['name']))

            objects.tracker = tracker
            objects.blocked_list = BlockedList.from_tracker(
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

            for riders_objects in self.riders_objects.values():
                if riders_objects.tracker:
                    riders_objects.tracker.stop()
            for riders_objects in self.riders_objects.values():
                try:
                    if riders_objects.tracker:
                        await riders_objects.tracker.complete()
                except Exception:
                    self.logger.exception('Unhandled tracker error: ')

            del self.riders_objects
            del self.riders_current_values
            del self.riders_predicted_points

            self.trackers_started = False

    async def on_rider_new_points(self, rider_name, tracker, new_points):
        if new_points:
            values = self.riders_current_values[rider_name]
            for point in new_points:
                values.update(point)
                if 'position' in point:
                    values['position_time'] = point['time']
            await self.rider_new_values_observable(self, rider_name, values)
        self.new_points.set()

    async def on_rider_reset_points(self, rider_name, tracker):
        values = self.riders_current_values[rider_name]
        values.clear()
        await self.rider_new_values_observable(self, rider_name, values)
        self.new_points.set()

    def rider_sort_key_func(self, riders_predicted_points, rider_name):
        rider_values = self.riders_current_values.get(rider_name, {})
        finished = 'finished_time' in rider_values
        time_to_finish = rider_values['finished_time'] - self.event_start if finished else None
        has_dist_on_route = 'dist_route' in rider_values
        dist_on_route = riders_predicted_points.get(rider_name, {}).get('dist_route') or rider_values.get('dist_route', 0)
        return not finished, time_to_finish, not has_dist_on_route, 0 - dist_on_route

    async def predicted(self):
        inactive_time = datetime.timedelta(minutes=30)

        while True:
            # Sleep at least 5 secs
            await asyncio.sleep(5)
            # Sleep another 15 sec or when new points are available.
            with suppress(asyncio.TimeoutError):
                await asyncio.wait_for(self.new_points.wait(), 15)

            try:
                time = datetime.datetime.now()
                riders_predicted_points = {rider_objects.rider_name: rider_objects.analyse_tracker.get_predicted_position(time) or {}
                                           for rider_objects in self.riders_objects.values() if rider_objects.analyse_tracker}
                if not riders_predicted_points:
                    break

                sort_key_func = partial(self.rider_sort_key_func, riders_predicted_points)
                rider_names_sorted = list(sorted(riders_predicted_points.keys(), key=sort_key_func))

                leader = rider_names_sorted[0]
                leader_objects = self.riders_objects[leader]
                leader_points = []
                last_point = None
                for point in leader_objects.analyse_tracker.points:
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
                        rider_values = self.riders_current_values.get(rider_name)
                        if rider_values and 'position_time' in rider_values:
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
    'time_show_days': -3,
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


@attr.s()
class RiderObjects(object):
    rider_name = attr.ib()
    event = attr.ib()
    data_tracker = attr.ib(default=None)
    source_trackers = attr.ib(default=attr.Factory(list))
    analyse_tracker = attr.ib(default=None)
    tracker = attr.ib(default=None)
    off_route_tracker = attr.ib(default=None)
    blocked_list = attr.ib(default=None)
    off_route_blocked_list = attr.ib(default=None)


class DataTracker(Tracker):

    @classmethod
    async def start(cls, rider_data):
        self = cls(f'data.{rider_data["name"]}')
        self.rider_data = rider_data
        await self.new_points(rider_data.get('points', ()))
        self.completed.set_result(None)
        return self

    async def add_points(self, new_points):
        await self.new_points(new_points)
        self.rider_data['points'] = self.points
        # Caller is responsible to call event.save
