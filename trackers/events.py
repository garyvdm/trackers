import asyncio
import copy
import logging
import os
from bisect import bisect
from collections import defaultdict
from contextlib import closing, suppress
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from functools import partial
from itertools import chain
from typing import List

import aionotify
import msgpack
import yaml
from more_itertools import spy

from trackers.analyse import AnalyseTracker, get_analyse_routes
from trackers.base import BlockedList, cancel_and_wait_task, general_fut_done_callback, Observable, Tracker
from trackers.combined import Combined
from trackers.dulwich_helpers import TreeReader, TreeWriter
from trackers.general import hash_bytes, index_and_hash_tracker, json_encode, start_replay_tracker
from trackers.persisted_func_cache import PersistedFuncCache

logger = logging.getLogger(__name__)

min_time = datetime.fromtimestamp(0)

# TODO: this is no longer specific to events. Move to somewhere


async def load_with_watcher(app, ref=b'HEAD', **kwargs):
    try:
        await load(app, ref=ref, **kwargs)

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
                    await load(app, ref=ref, **kwargs)
        else:
            logger.debug('No inotify reload on memory repo')
    except asyncio.CancelledError:
        raise
    except Exception:
        logger.exception('Error in load_with_watcher: ')


async def load(app, ref=b'HEAD', **kwargs):
    try:
        tree_reader = TreeReader(app['trackers.data_repo'], treeish=ref)
    except KeyError:
        pass
    else:
        try:
            app['config'] = yaml.safe_load(tree_reader.get('config.yaml').data)
        except Exception:
            logger.exception('')
            app['config'] = {}

        await load_events(app, tree_reader, **kwargs)


async def load_events(app, tree_reader, new_event_observable=Observable('new_event'), removed_event_observable=Observable('removed_event')):
    events = app['trackers.events']
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
        self.batch_update_task = None
        self.not_live_complete_trackers_task = None

        self.config_routes_change_observable = Observable(f'event.{name}.config_routes_change')
        self.rider_new_values_observable = Observable(f'event.{name}.rider_new_values')
        self.rider_pre_post_new_values_observable = Observable(f'event.{name}.rider_pre_post_new_values')
        self.rider_blocked_list_update_observable = Observable(f'event.{name}.rider_blocked_list_update')
        self.rider_off_route_blocked_list_update_observable = Observable(f'event.{name}.rider_off_route_blocked_list_update')
        self.rider_pre_post_blocked_list_update_observable = Observable(f'event.{name}.rider_pre_post_blocked_list_update')
        self.batch_update_observable = Observable(f'event.{name}.batch_update')
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
        self.config = yaml.safe_load(config_bytes.decode())

        if tree_reader.exists(self.routes_yaml_path):
            self.routes = yaml.safe_load(tree_reader.get(self.routes_yaml_path).data)
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

    async def save(self, message, author=None, tree_writer=None, save_routes=False, prevent_reload=True):
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
        if prevent_reload:
            _, self.git_hash = tree_writer.lookup(self.path)
        else:
            await self.config_routes_change_observable(self)

    @property
    def admin_allowed_principals(self):
        return tuple(chain(
            self.app['config'].get('admin', ()),
            self.config.get('admin', ())
        ))

    async def start_trackers(self, analyse=True):
        self.start_trackers_without_wait(analyse)
        if self.starting_fut:
            await self.starting_fut

    def start_trackers_without_wait(self, analyse=True):
        if not self.trackers_started and not self.starting_fut:
            self.starting_fut = asyncio.ensure_future(self._start_trackers(analyse))
            self.starting_fut.add_done_callback(self._start_done)

    def _start_done(self, fut):
        self.starting_fut = None

    async def _start_trackers(self, analyse):
        self.logger.info('Starting.')

        # analyse = self.config.get('analyse', False)
        replay = self.config.get('replay', False)
        is_live = self.config.get('live', False)
        self.event_start = self.config.get('event_start')

        self.riders_objects = {}
        self.riders_current_values = {}
        self.riders_pre_post_values = {}
        self.riders_predicted_points = {}
        self.riders_updated = set()
        self.riders_off_route_updated = set()
        self.riders_pre_post_updated = set()

        tree_reader = TreeReader(self.app['trackers.data_repo'], treeish=self.git_hash) if self.git_hash else None
        has_static = tree_reader.exists(os.path.join('static')) if tree_reader else False
        has_static_analyse = has_static and self.config.get('static_analyse', False) and not replay

        if analyse and not has_static_analyse:
            loop = asyncio.get_event_loop()
            analyse_routes = await loop.run_in_executor(None, get_analyse_routes, self.routes)

            find_closest_cache_dir = os.path.join(self.app['trackers.settings']['cache_path'], 'find_closest')
            os.makedirs(find_closest_cache_dir, exist_ok=True)
            if self.routes:
                find_closest_cache = PersistedFuncCache(os.path.join(find_closest_cache_dir, f'2-{self.routes_hash}'))
                logger.info(f'find_closest_cache: {find_closest_cache.path}')
            else:
                find_closest_cache = None

        if replay:
            replay_config = replay if isinstance(replay, dict) else {}
            replay_kwargs = {
                'replay_start': datetime.now(),
                'speed_multiply': replay_config.get('speed_multiply', 2),
                'offset': timedelta(**replay_config.get('offset', {})),
                'event_start_time': self.event_start,
            }
            self.event_start = replay_kwargs['replay_start'] + replay_kwargs['offset']

        for rider in self.config['riders']:
            rider_name = rider['name']
            self.riders_objects[rider_name] = objects = RiderObjects(rider_name, self)
            objects.data_tracker = await DataTracker.start(rider, self.config.get('tracker_end'))

        if has_static:
            for rider in self.config['riders']:
                rider_name = rider['name']
                tracker = await start_implicit_static_event_tracker(self, rider_name, 'source', tree_reader)
                self.riders_objects[rider_name].source_trackers.append(tracker)
        else:
            rider_tracker_start_fs = defaultdict(list)
            for rider in self.config['riders']:
                rider_name = rider['name']
                rider_trackers = tuple(chain(
                    (rider['tracker'], ) if 'tracker' in rider and rider['tracker'] else (),
                    rider.get('trackers', ())
                ))
                for tracker in rider_trackers:
                    start_tracker = self.app['start_event_trackers'][tracker['type']]
                    start = tracker.get('start') or self.config.get('tracker_start')
                    end = tracker.get('end') or self.config.get('tracker_end')
                    start_fut = asyncio.ensure_future(start_tracker(self.app, self, rider_name, tracker, start, end))
                    rider_tracker_start_fs[rider_name].append(start_fut)

            all_start_fs = list(chain.from_iterable(rider_tracker_start_fs.values()))
            if all_start_fs:
                await asyncio.wait(all_start_fs)
            for rider in self.config['riders']:
                for start_fut in rider_tracker_start_fs[rider['name']]:
                    tracker = start_fut.result()
                    self.riders_objects[rider['name']].source_trackers.append(tracker)

        for rider in self.config['riders']:
            rider_name = rider['name']
            objects = self.riders_objects[rider_name]

            # Should these not be a part of objects?
            self.riders_current_values[rider['name']] = {}
            self.riders_pre_post_values[rider['name']] = {}

            objects.combined_tracker = tracker = await Combined.start(f'combined.{rider_name}', tuple(chain((objects.data_tracker, ), objects.source_trackers)))
            if replay:
                tracker = await start_replay_tracker(tracker, **replay_kwargs)

            if analyse and rider.get('type', 'rider') == 'rider':
                if has_static_analyse:
                    objects.analyse_tracker = tracker = await start_implicit_static_event_tracker(
                        self, rider_name, 'analyse', tree_reader)
                    objects.off_route_tracker = await start_implicit_static_event_tracker(
                        self, rider_name, 'off_route', tree_reader)

                    # As we intentionally don't store pre_post, just create a blank tracker.
                    objects.pre_post_tracker = Tracker('null')

                else:
                    objects.analyse_tracker = tracker = await AnalyseTracker.start(
                        tracker, self.event_start, analyse_routes, find_closest_cache=find_closest_cache,
                        processing_lock=self.app['analyse_processing_lock'])
                    objects.off_route_tracker = await index_and_hash_tracker(tracker.off_route_tracker)
                    objects.pre_post_tracker = await index_and_hash_tracker(tracker.pre_post_tracker)
                    await self.on_rider_pre_post_new_points(rider['name'], objects.pre_post_tracker, objects.pre_post_tracker.points)
                    objects.pre_post_tracker.new_points_observable.subscribe(partial(self.on_rider_pre_post_new_points, rider['name']))
                    tracker.not_pre_post_observable.subscribe(partial(self.on_rider_not_pre_post, rider['name']))

                objects.off_route_blocked_list = BlockedList.from_tracker(
                    objects.off_route_tracker, entire_block=not is_live,
                    new_update_callbacks=(partial(self.on_rider_off_route_update, rider['name']), ))

                objects.pre_post_blocked_list = BlockedList.from_tracker(
                    objects.pre_post_tracker, entire_block=not is_live,
                    new_update_callbacks=(partial(self.rider_pre_post_blocked_list_update_observable, self, rider['name']), ))

            tracker = await index_and_hash_tracker(tracker)
            await self.on_rider_new_points(rider['name'], tracker, tracker.points)
            tracker.new_points_observable.subscribe(partial(self.on_rider_new_points, rider['name']))
            tracker.reset_points_observable.subscribe(partial(self.on_rider_reset_points, rider['name']))

            objects.tracker = tracker
            objects.blocked_list = BlockedList.from_tracker(
                tracker, entire_block=not is_live,
                new_update_callbacks=(partial(self.rider_blocked_list_update_observable, self, rider['name']), ))

        if is_live:
            self.batch_update_task = asyncio.ensure_future(self.batch_update_loop())
        else:
            self.not_live_complete_trackers_task = asyncio.ensure_future(self.not_live_complete_trackers())
            self.not_live_complete_trackers_task.add_done_callback(general_fut_done_callback)

        self.trackers_started = True
        self.logger.info('Started.')

    async def stop_and_complete_trackers(self):
        if self.starting_fut:
            await cancel_and_wait_task(self.starting_fut)

        if self.trackers_started:
            if self.batch_update_task:
                await cancel_and_wait_task(self.batch_update_task)
                self.batch_update_task = None

            for riders_objects in self.riders_objects.values():
                if riders_objects.tracker:
                    riders_objects.tracker.stop()
            for riders_objects in self.riders_objects.values():
                try:
                    if riders_objects.tracker:
                        try:
                            await riders_objects.tracker.complete()
                        except asyncio.CancelledError:
                            pass
                except Exception:
                    self.logger.exception('Unhandled tracker error: ')
            if self.not_live_complete_trackers_task:
                await self.not_live_complete_trackers_task

            del self.riders_objects
            del self.riders_current_values
            del self.riders_predicted_points
            del self.riders_updated
            del self.riders_off_route_updated
            del self.riders_pre_post_updated

            self.trackers_started = False

    async def not_live_complete_trackers(self):
        await asyncio.wait([rider_objs.tracker.completed for rider_objs in self.riders_objects.values()])
        riders = set(self.riders_objects.keys())
        await self.batch_update(riders, riders, riders)

    async def on_rider_new_points(self, rider_name, tracker, new_points):
        if new_points:
            values = self.riders_current_values[rider_name]
            for point in new_points:
                values.update(point)
                if 'position' in point:
                    values['position_time'] = point['time']
            # if 'rider_status' in values:
            #     with suppress(KeyError):
            #         del values['position']
            self.riders_updated.add(rider_name)
            self.new_points.set()
            await self.rider_new_values_observable(self, rider_name, values)

    async def on_rider_off_route_update(self, rider_name, blocked_list, update):
        self.riders_off_route_updated.add(rider_name)
        self.new_points.set()
        await self.rider_off_route_blocked_list_update_observable(self, rider_name, blocked_list, update)

    pre_post_update_main_keys = {'time', 'battery', 'tk_status', 'tk_config'}

    async def on_rider_pre_post_new_points(self, rider_name, tracker, new_points):
        if new_points:
            values_updated = False

            pre_post_values = self.riders_pre_post_values[rider_name]
            values = self.riders_current_values[rider_name]
            for point in new_points:
                pre_post_values.update(point)

                # TODO, rather than do this check, we should make sure the values come in in the right order.
                point_is_newer = point['time'] > values.get('time', min_time)

                if point_is_newer:
                    has_values_update, values_update = spy(((k, v) for k, v in point.items() if k in self.pre_post_update_main_keys))
                    if has_values_update:
                        values_update = list(values_update)
                        values.update(values_update)
                        values_updated = True
                if 'position' in point:
                    pre_post_values['position_time'] = point['time']
                    if point_is_newer:
                        values['position_time'] = point['time']
                        values_updated = True

            if values_updated:
                self.riders_updated.add(rider_name)
                await self.rider_new_values_observable(self, rider_name, values)

            self.riders_pre_post_updated.add(rider_name)
            self.new_points.set()
            await self.rider_pre_post_new_values_observable(self, rider_name, pre_post_values)

    async def on_rider_not_pre_post(self, rider_name):
        self.riders_pre_post_values[rider_name] = values = {}
        await self.rider_pre_post_new_values_observable(self, rider_name, values)
        self.riders_pre_post_updated.add(rider_name)
        self.new_points.set()

    async def on_rider_reset_points(self, rider_name, tracker):
        self.riders_current_values[rider_name].clear()
        self.riders_pre_post_values[rider_name].clear()
        if rider_name in self.riders_predicted_points:
            self.riders_predicted_points[rider_name].clear()

        self.riders_updated.add(rider_name)
        self.riders_pre_post_updated.add(rider_name)
        self.riders_off_route_updated.add(rider_name)
        self.new_points.set()

        await self.rider_new_values_observable(self, rider_name, {})
        await self.rider_pre_post_new_values_observable(self, rider_name, {})

    def rider_sort_key_func(self, riders_predicted_points, rider_name):
        rider_values = self.riders_current_values.get(rider_name, {})
        finished = 'finished_time' in rider_values
        time_to_finish = rider_values['finished_time'] - self.event_start if finished else None
        has_dist_on_route = 'dist_route' in rider_values
        dist_on_route = riders_predicted_points.get(rider_name, {}).get('dist_route') or rider_values.get('dist_route', 0)
        return not finished, time_to_finish, not has_dist_on_route, 0 - dist_on_route

    async def batch_update_loop(self):
        batch_update_interval = self.config.get('batch_update_interval') or 2
        predicted_update_interval = self.config.get('predicted_update_interval') or 10
        while True:
            await asyncio.sleep(batch_update_interval)
            with suppress(asyncio.TimeoutError):
                await asyncio.wait_for(self.new_points.wait(), predicted_update_interval - batch_update_interval)
            try:
                await self.batch_update(self.riders_updated, self.riders_off_route_updated, self.riders_pre_post_updated)
            except asyncio.CancelledError:
                raise
            except Exception:
                self.logger.exception('Error in batch_update:')
            self.riders_updated = set()
            self.riders_off_route_updated = set()
            self.riders_pre_post_updated = set()
            self.new_points.clear()

    async def batch_update(self, riders_updated, riders_off_route_updated, riders_pre_post_updated):
        if not self.riders_objects:
            return
        time = datetime.now()
        riders_predicted_points = {
            rider_objects.rider_name: rider_objects.analyse_tracker.get_predicted_position(time) or {}
            for rider_objects in self.riders_objects.values() if isinstance(rider_objects.analyse_tracker, AnalyseTracker)}

        sort_key_func = partial(self.rider_sort_key_func, riders_predicted_points)
        rider_names_sorted = list(sorted(self.riders_objects.keys(), key=sort_key_func))

        leader = rider_names_sorted[0]
        leader_objects = self.riders_objects[leader]
        leader_points = []
        last_point = None
        if leader_objects.analyse_tracker:
            for point in leader_objects.analyse_tracker.points:
                if 'dist_route' in point:
                    going_forward = point['dist_route'] > last_point['dist_route'] if last_point else True
                    if going_forward:
                        leader_points.append((point['dist_route'], point['time']))
                        last_point = point
        if 'dist_route' in riders_predicted_points.get(leader, ()):
            leader_points.append((riders_predicted_points[leader]['dist_route'], time))

        if leader_points:
            for rider_name in rider_names_sorted[1:]:
                leader_time_diff = self.get_leader_time_diff(rider_name, riders_predicted_points,
                                                             leader, leader_points, time)
                if leader_time_diff is not None:
                    if rider_name in riders_predicted_points:
                        riders_predicted_points[rider_name]['leader_time_diff'] = leader_time_diff
                    self.riders_current_values[rider_name]['leader_time_diff'] = leader_time_diff
                else:
                    with suppress(KeyError):
                        del self.riders_current_values[rider_name]['leader_time_diff']

        self.riders_predicted_points = {key: value for key, value in riders_predicted_points.items() if value}
        await self.batch_update_observable(self, time, riders_updated, riders_off_route_updated, riders_pre_post_updated)

    def get_leader_time_diff(self, rider_name, riders_predicted_points, leader, leader_points, time):
        rider_predicted_points = riders_predicted_points.get(rider_name)
        rider_values = self.riders_current_values.get(rider_name)
        if rider_values and 'finished_time' in rider_values:
            leader_values = self.riders_current_values.get(leader)
            if 'finished_time' in leader_values:
                return rider_values['finished_time'] - leader_values['finished_time']

        if rider_values and 'position_time' in rider_values:
            rider_dist_route = None
            rider_time = None
            if rider_predicted_points and 'dist_route' in rider_predicted_points:
                rider_dist_route = rider_predicted_points['dist_route']
                rider_time = rider_predicted_points['time']
            elif rider_values and 'dist_route' in rider_values:
                rider_dist_route = rider_values['dist_route']
                rider_time = rider_values['position_time']
            if rider_dist_route:
                i = bisect(leader_points, (rider_dist_route,))
                if i < len(leader_points):
                    point1 = leader_points[i - 1]
                    point2 = leader_points[i]
                    try:
                        interpolate = (rider_dist_route - point1[0]) / (point2[0] - point1[0])
                    except FloatingPointError:
                        pass
                    else:
                        interpolated_time = ((point2[1] - point1[1]) * interpolate) + point1[1]
                        return rider_time - interpolated_time

    async def convert_to_static(self, tree_writer):
        try:
            await self.start_trackers(analyse=False)
            for rider in self.config['riders']:
                rider_name = rider['name']
                tracker = await Combined.start(rider_name, self.riders_objects[rider_name].source_trackers)
                await tracker.complete()

                with suppress(KeyError):
                    del rider['trackers']
                with suppress(KeyError):
                    del rider['tracker']
                # Remove old static file.
                with suppress(KeyError):
                    tree_writer.remove(os.path.join('events', self.name, rider_name))

                path = os.path.join('events', self.name, 'static', rider_name, 'source')
                tree_writer.set_data(path, msgpack.dumps(tracker.points, default=json_encode))

            self.config['live'] = False
            await self.save(f'{self.name}: convert_to_static', tree_writer=tree_writer)
        finally:
            await self.stop_and_complete_trackers()

    async def store_analyse(self, tree_writer):
        assert tree_writer.exists(os.path.join('events', self.name, 'static'))
        try:
            self.config['static_analyse'] = False
            await self.start_trackers(analyse=True)
            for rider in self.config['riders']:
                rider_name = rider['name']
                trackers = (
                    (self.riders_objects[rider_name].analyse_tracker, 'analyse'),
                    (self.riders_objects[rider_name].off_route_tracker, 'off_route'),
                )
                for tracker, sub_tracker_type in trackers:
                    if tracker:
                        await tracker.complete()
                        path = os.path.join('events', self.name, 'static', rider_name, sub_tracker_type)
                        tree_writer.set_data(path, msgpack.dumps(tracker.points, default=json_encode))

            self.config['static_analyse'] = True
            await self.save(f'{self.name}: store static analyse', tree_writer=tree_writer)
        finally:
            await self.stop_and_complete_trackers()


async def start_implicit_static_event_tracker(event, rider_name, sub_type, tree_reader):
    tracker = Tracker(f'static.{rider_name}.{sub_type}')
    path = os.path.join('static', rider_name, sub_type)
    points = msgpack.loads(tree_reader.get(path).data, raw=False)
    for point in points:
        if 'time' in point:
            point['time'] = datetime.fromtimestamp(point['time'])
        if 'server_time' in point:
            point['server_time'] = datetime.fromtimestamp(point['server_time'])
        if 'finished_time' in point:
            point['finished_time'] = datetime.fromtimestamp(point['finished_time'])
    # print(event.name, rider_name, sub_type, len(points))

    await tracker.new_points(points)
    tracker.completed.set_result(None)
    return tracker


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


@dataclass
class RiderObjects(object):
    rider_name: str
    event: Event
    data_tracker: Tracker = field(default=None)
    source_trackers: List[Tracker] = field(default_factory=list)
    combined_tracker: Tracker = field(default=None)
    analyse_tracker: Tracker = field(default=None)
    tracker: Tracker = field(default=None)
    off_route_tracker: Tracker = field(default=None)
    pre_post_tracker: Tracker = field(default=None)

    blocked_list: BlockedList = field(default=None)
    off_route_blocked_list: BlockedList = field(default=None)
    pre_post_blocked_list: BlockedList = field(default=None)


class DataTracker(Tracker):

    @classmethod
    async def start(cls, rider_data, end):
        self = cls(f'data.{rider_data["name"]}')
        self.rider_data = rider_data
        await self.new_points(rider_data.get('points', ()))

        if end:
            now = datetime.now()
            delay = (end - now).total_seconds()
            if delay > 0:
                asyncio.get_event_loop().call_later(delay, self.completed.set_result, None)
            else:
                self.completed.set_result(None)
        return self

    async def add_points(self, new_points):
        await self.new_points(new_points)
        self.rider_data['points'] = self.points
        # Caller is responsible to call event.save
