import asyncio
import collections
import datetime
import functools
import json
import os

import yaml
import msgpack

import trackers.modules
from trackers.base import callback_done_callback, cancel_and_wait_task, Tracker


async def static_start_event_tracker(app, settings, event_name, event_data, rider_name, tracker_data):
    tracker = Tracker('static.{}'.format(tracker_data['name']))
    format = tracker_data.get('format', 'json')
    path = os.path.join(settings['data_path'], event_name, tracker_data['name'])
    if format == 'json':
        with open(path) as f:
            points = json.load(f)
    if format == 'msgpack':
        with open(path, 'rb') as f:
            points = msgpack.load(f, encoding='utf-8')

    for point in points:
        point['time'] = datetime.datetime.fromtimestamp(point['time'])
    await tracker.new_points(points)
    return tracker


async def static_replay_start_event_tracker(app, settings, event_name, event_data, tracker_data):
    tracker = Tracker('static.{}'.format(tracker_data['name']))
    monitor_task = asyncio.ensure_future(static_replay(
        tracker, os.path.join(settings['data_path'], event_name, tracker_data['name']), event_data['start'], 2000))
    tracker.stop = functools.partial(cancel_and_wait_task, monitor_task)
    monitor_task.add_done_callback(functools.partial(callback_done_callback, 'Error in static_replay:', tracker.logger))
    return tracker


async def static_replay(tracker, path, event_start_time, speed_multiply):
    replay_start = datetime.datetime.now()

    with open(path) as f:
        points = collections.deque(yaml.load(f))

    while points:
        now = datetime.datetime.now()
        new_points = []
        while points:
            point = points[0]
            new_time = replay_start + ((point['time'] - event_start_time) / speed_multiply)
            if new_time <= now:
                points.popleft()
                point['time'] = new_time
                new_points.append(point)
            else:
                break
        if new_points:
            await tracker.new_points(new_points)
        await asyncio.sleep((new_time - now).total_seconds())


async def start_cropped_tracker(app, settings, event_name, event_data, tracker_data):
    org_tracker = await trackers.modules.start_event_trackers[tracker_data['tracker']['type']](app, settings, event_name, event_data, tracker_data['tracker'])
    cropped_tracker = Tracker('croped.{}'.format(org_tracker.name))
    cropped_tracker.stop_specific = org_tracker.stop
    cropped_tracker.finish_specific = org_tracker.finish
    cropped_tracker.org_tracker = org_tracker

    await cropped_tracker_newpoints(cropped_tracker, tracker_data['end'], org_tracker, org_tracker.points)
    org_tracker.new_points_callbacks.append(
        functools.partial(cropped_tracker_newpoints, cropped_tracker, tracker_data['end']))
    return cropped_tracker


async def cropped_tracker_newpoints(cropped_tracker, end, org_tracker, new_points):
    points = [point for point in new_points if point['time'] < end]
    if points:
        await cropped_tracker.new_points(points)
