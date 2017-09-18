import asyncio
import datetime
import functools
import json
import os

import msgpack

from trackers.base import callback_done_callback, cancel_and_wait_task, Tracker


async def static_start_event_tracker(app, event, rider_name, tracker_data):
    tracker = Tracker('static.{}'.format(tracker_data['name']))
    format = tracker_data.get('format', 'json')
    path = os.path.join(event.base_path, tracker_data['name'])
    if format == 'json':
        with open(path) as f:
            points = json.load(f)
    if format == 'msgpack':
        with open(path, 'rb') as f:
            points = msgpack.load(f, encoding='utf-8')

    for point in points:
        point['time'] = datetime.datetime.fromtimestamp(point['time'])
    tracker.is_finished = True
    await tracker.new_points(points)
    return tracker


async def start_replay_tracker(org_tracker, event_start_time, replay_start, speed_multiply=2000):
    replay_tracker = Tracker('replay.{}'.format(org_tracker.name))
    replay_task = asyncio.ensure_future(
        static_replay(replay_tracker, org_tracker, event_start_time, replay_start, speed_multiply))
    replay_tracker.stop = functools.partial(cancel_and_wait_task, replay_task)
    replay_task.add_done_callback(functools.partial(callback_done_callback, 'Error in static_replay:', replay_tracker.logger))
    return replay_tracker


async def static_replay(replay_tracker, org_tracker, event_start_time, replay_start, speed_multiply):
    point_i = 0
    while not org_tracker.is_finished or point_i < len(org_tracker.points):
        now = datetime.datetime.now()
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


async def start_cropped_tracker(app, event, tracker_data):
    import trackers.modules
    org_tracker = await trackers.modules.start_trackers[tracker_data['tracker']['type']](app, event, tracker_data['tracker'])
    cropped_tracker = Tracker('croped.{}'.format(org_tracker.name))
    cropped_tracker.stop_specific = org_tracker.stop
    cropped_tracker.finish_specific = org_tracker.finish
    cropped_tracker.org_tracker = org_tracker

    await cropped_tracker_newpoints(cropped_tracker, tracker_data['end'], org_tracker, org_tracker.points)
    org_tracker.new_points_callbacks.append(
        functools.partial(cropped_tracker_newpoints, cropped_tracker, tracker_data['end']))
    return cropped_tracker


async def cropped_tracker_newpoints(cropped_tracker, end, org_tracker, new_points):
    if org_tracker.is_finished:
        cropped_tracker.is_finished = True
    points = [point for point in new_points if point['time'] < end]
    if points:
        await cropped_tracker.new_points(points)
