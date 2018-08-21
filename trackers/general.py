import asyncio
import functools
import hashlib
import json
import os
from base64 import urlsafe_b64encode
from copy import copy
from datetime import datetime, timedelta

import attr
import msgpack
from google.protobuf.internal import type_checkers as pb_type_checkers

from trackers import trackers_pb2
from trackers.base import Tracker
from trackers.dulwich_helpers import TreeReader


def json_encode(obj):
    if isinstance(obj, datetime):
        return obj.timestamp()
    if isinstance(obj, timedelta):
        return obj.total_seconds()
    if hasattr(type(obj), '__attrs_attrs__'):
        return attr.asdict(obj)


json_dumps = functools.partial(json.dumps, default=json_encode, sort_keys=True)


async def static_start_event_tracker(app, event, rider_name, tracker_data):
    tracker = Tracker('static.{}'.format(tracker_data['name']))
    path = os.path.join('events', event.name, tracker_data['name'])
    data = TreeReader(app['trackers.data_repo']).get(path).data
    points = {
        'json': lambda data: json.loads(data.decode()),
        'msgpack': lambda data: msgpack.loads(data, raw=False)
    }[tracker_data.get('format', 'json')](data)
    for point in points:
        if 'time' in point:
            point['time'] = datetime.fromtimestamp(point['time'])
        if 'server_time' in point:
            point['server_time'] = datetime.fromtimestamp(point['server_time'])

    await tracker.new_points(points)
    tracker.completed.set_result(None)
    return tracker


async def start_replay_tracker(org_tracker, event_start_time, replay_start, offset=timedelta(0), speed_multiply=2000):
    replay_tracker = Tracker('replay.{}'.format(org_tracker.name))
    replay_task = asyncio.ensure_future(
        replay(replay_tracker, org_tracker, event_start_time, replay_start, offset, speed_multiply))
    replay_tracker.stop = replay_task.cancel
    replay_tracker.completed = replay_task
    return replay_tracker


async def replay(replay_tracker, org_tracker, event_start_time, replay_start, offset, speed_multiply):
    point_i = 0
    while not org_tracker.completed.done() or point_i < len(org_tracker.points):
        now = datetime.now()
        new_points = []
        new_time = None
        while point_i < len(org_tracker.points):
            try:
                point = org_tracker.points[point_i]
                time = point.get('time') or point.get('server_time')
                new_time = replay_start + ((time - event_start_time + offset) / speed_multiply)
                if new_time <= now:
                    point_i += 1
                    point['time'] = new_time
                    new_points.append(point)
                else:
                    break
            except Exception:
                replay_tracker.logger.exception('Error in replay:')

        if new_points:
            await replay_tracker.new_points(new_points)
        if new_time:
            await asyncio.sleep((new_time - now).total_seconds())
        else:
            await asyncio.sleep(1)


async def wrapped_tracker_start_event(start_wraped, app, event, rider_name, tracker_data):
    start_tracker = app['start_event_trackers'][tracker_data['tracker']['type']]
    org_tracker = await start_tracker(app, event, rider_name, tracker_data['tracker'])
    return await start_wraped(org_tracker, tracker_data)


async def cropped_tracker_start(org_tracker, tracker_data):
    cropped_tracker = Tracker('cropped.{}'.format(org_tracker.name), org_tracker.completed)
    cropped_tracker.stop = org_tracker.stop
    cropped_tracker.org_tracker = org_tracker

    await cropped_tracker_newpoints(cropped_tracker, tracker_data.get('start'), tracker_data.get('end'), org_tracker, org_tracker.points)
    org_tracker.new_points_observable.subscribe(
        functools.partial(cropped_tracker_newpoints, cropped_tracker, tracker_data.get('start'), tracker_data.get('end')))
    return cropped_tracker


async def cropped_tracker_newpoints(cropped_tracker, start, end, org_tracker, new_points):
    points = [point for point in new_points if (not end or point['time'] < end) and (not start or point['time'] > start)]
    if points:
        await cropped_tracker.new_points(points)


def points_2_pb(points):
    pb_points = trackers_pb2.Points()
    for point in points:
        pb_point = pb_points.points.add()
        point_2_pb_point(point, pb_point)
    return pb_points.SerializeToString()


def point_2_pb_point(point, pb_point):
    for field in pb_point.DESCRIPTOR.fields:
        if field.name == 'position':
            position = point.get('position')
            if position:
                pb_point.position.lat = int(position[0] * 1000000)
                pb_point.position.lng = int(position[1] * 1000000)
                if len(position) > 2:
                    pb_point.position.elevation = int(position[2])
        else:
            value = point.get(field.name)
            if value is not None:
                if isinstance(value, datetime):
                    value = value.timestamp()
                if isinstance(value, timedelta):
                    value = value.total_seconds()
                type_checker = pb_type_checkers.GetTypeChecker(field)
                if isinstance(type_checker, pb_type_checkers.IntValueChecker):
                    value = int(value)
                try:
                    setattr(pb_point, field.name, value)
                except Exception as e:
                    raise Exception(f'Error setting {field.name}') from e
    return pb_point


async def index_and_hash_tracker(org_tracker, hasher=None):
    ih_tracker = Tracker('indexed_and_hashed.{}'.format(org_tracker.name), org_tracker.completed)
    ih_tracker.stop = org_tracker.stop
    ih_tracker.org_tracker = org_tracker
    if hasher is None:
        hasher = hashlib.sha1()
    ih_tracker.hasher = hasher

    await index_and_hash_tracker_org_newpoints(ih_tracker, org_tracker, org_tracker.points)
    org_tracker.new_points_observable.subscribe(
        functools.partial(index_and_hash_tracker_org_newpoints, ih_tracker))
    org_tracker.reset_points_observable.subscribe(
        functools.partial(index_and_hash_tracker_org_reset_points, ih_tracker))

    return ih_tracker


def index_and_hash_list(points, start, hasher):
    ih_points = [copy(point) for point in points]
    for i, ih_point in enumerate(ih_points, start=start):
        ih_point['index'] = i
        hasher.update(point_2_pb_point(ih_point, trackers_pb2.Point()).SerializeToString())
        ih_point['hash'] = urlsafe_b64encode(hasher.digest()[:3]).decode('ascii')
    return ih_points


async def index_and_hash_tracker_org_newpoints(ih_tracker, org_tracker, new_points):
    await ih_tracker.new_points(index_and_hash_list(new_points, len(ih_tracker.points), ih_tracker.hasher))


async def index_and_hash_tracker_org_reset_points(ih_tracker, org_tracker):
    await ih_tracker.reset_points()


async def filter_inaccurate_tracker_start(org_tracker, tracker_data):
    filtered_tracker = Tracker('filter_inaccurate.{}'.format(org_tracker.name), org_tracker.completed)
    filtered_tracker.stop = org_tracker.stop
    filtered_tracker.org_tracker = org_tracker

    await filter_inaccurate_tracker_newpoints(filtered_tracker, org_tracker, org_tracker.points)
    org_tracker.new_points_observable.subscribe(
        functools.partial(filter_inaccurate_tracker_newpoints, filtered_tracker))
    return filtered_tracker


async def filter_inaccurate_tracker_newpoints(filtered_tracker, org_tracker, new_points):
    points = []
    for point in new_points:
        if point.get('accuracy', 0) >= 500:
            point = copy(point)
            del point['position']
        points.append(point)
    if points:
        await filtered_tracker.new_points(points)


def hash_bytes(b):
    return urlsafe_b64encode(hashlib.sha1(b).digest()).decode('ascii')
