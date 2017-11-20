import asyncio
import functools
import hashlib
import json
import os
from base64 import urlsafe_b64encode
from copy import copy
from datetime import datetime

import msgpack

from trackers.base import cancel_and_wait_task, log_error_callback, Tracker


def json_encode(obj):
    if isinstance(obj, datetime):
        return obj.timestamp()


json_dumps = functools.partial(json.dumps, default=json_encode, sort_keys=True)


async def static_start_event_tracker(app, event, rider_name, tracker_data):
    tracker = Tracker('static.{}'.format(tracker_data['name']))
    format = tracker_data.get('format', 'json')
    path = os.path.join(event.base_path, tracker_data['name'])
    data = event.tree_reader.get(path).data
    if format == 'json':
        points = json.loads(data.decode())
    if format == 'msgpack':
        points = msgpack.loads(data, encoding='utf-8')

    for point in points:
        point['time'] = datetime.fromtimestamp(point['time'])
    tracker.is_finished = True
    await tracker.new_points(points)
    return tracker


async def start_replay_tracker(org_tracker, event_start_time, replay_start, speed_multiply=2000):
    replay_tracker = Tracker('replay.{}'.format(org_tracker.name))
    replay_task = asyncio.ensure_future(
        static_replay(replay_tracker, org_tracker, event_start_time, replay_start, speed_multiply))
    replay_tracker.stop_specific = functools.partial(cancel_and_wait_task, replay_task)
    replay_task.add_done_callback(functools.partial(log_error_callback, replay_tracker.logger, 'Error in static_replay:'))
    return replay_tracker


async def static_replay(replay_tracker, org_tracker, event_start_time, replay_start, speed_multiply):
    point_i = 0
    while not org_tracker.is_finished or point_i < len(org_tracker.points):
        now = datetime.now()
        new_points = []
        while point_i < len(org_tracker.points):
            point = org_tracker.points[point_i]
            new_time = replay_start + ((point['time'] - event_start_time) / speed_multiply)
            if new_time <= now:
                point_i += 1
                point['time'] = new_time
                new_points.append(point)
            else:
                break

        if new_points:
            await replay_tracker.new_points(new_points)
        await asyncio.sleep((new_time - now).total_seconds())


async def cropped_tracker_start_event(app, event, rider_name, tracker_data):
    import trackers.modules
    start_tracker = trackers.modules.start_event_trackers[tracker_data['tracker']['type']]
    org_tracker = await start_tracker(app, event, rider_name, tracker_data['tracker'])
    return await cropped_tracker_start(org_tracker, tracker_data)


async def cropped_tracker_start(org_tracker, tracker_data):
    cropped_tracker = Tracker('cropped.{}'.format(org_tracker.name))
    cropped_tracker.stop_specific = org_tracker.stop
    cropped_tracker.finish_specific = org_tracker.finish
    cropped_tracker.org_tracker = org_tracker

    await cropped_tracker_newpoints(cropped_tracker, tracker_data.get('start'), tracker_data.get('end'), org_tracker, org_tracker.points)
    org_tracker.new_points_callbacks.append(
        functools.partial(cropped_tracker_newpoints, cropped_tracker, tracker_data.get('start'), tracker_data.get('end')))
    return cropped_tracker


async def cropped_tracker_newpoints(cropped_tracker, start, end, org_tracker, new_points):
    if org_tracker.is_finished:
        cropped_tracker.is_finished = True
    points = [point for point in new_points if (not end or point['time'] < end) and (not start or point['time'] > start)]
    if points:
        await cropped_tracker.new_points(points)


async def index_and_hash_tracker(org_tracker, hasher=None):
    ih_tracker = Tracker('indexed_and_hashed.{}'.format(org_tracker.name))
    ih_tracker.stop_specific = org_tracker.stop
    ih_tracker.finish_specific = org_tracker.finish
    ih_tracker.org_tracker = org_tracker
    if hasher is None:
        hasher = hashlib.sha1()
    ih_tracker.hasher = hasher

    await index_and_hash_tracker_newpoints(ih_tracker, org_tracker, org_tracker.points)
    org_tracker.new_points_callbacks.append(
        functools.partial(index_and_hash_tracker_newpoints, ih_tracker))
    return ih_tracker


def index_and_hash_list(points, start, hasher):
    ih_points = [copy(point) for point in points]
    for i, (ih_point, org_point) in enumerate(zip(ih_points, points), start=start):
        ih_point['index'] = i
        hasher.update(json_dumps(org_point).encode())
        ih_point['hash'] = urlsafe_b64encode(hasher.digest()[:3]).decode('ascii')
    return ih_points


async def index_and_hash_tracker_newpoints(ih_tracker, org_tracker, new_points):
    await ih_tracker.new_points(index_and_hash_list(new_points, len(ih_tracker.points), ih_tracker.hasher))
