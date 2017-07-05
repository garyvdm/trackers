import asyncio
import json
import os
import datetime
import collections
import functools

import yaml

import trackers
import trackers.garmin_livetrack
import trackers.map_my_tracks
from trackers.async_exit_stack import AsyncExitStack


async def config_modules(app, settings):
    exit_stack = AsyncExitStack()

    modules = (
        trackers.map_my_tracks.config,
        # trackers.garmin_livetrack.config,
    )

    for module in modules:
        await exit_stack.enter_context(await module(app, settings))
    return exit_stack


async def static_start_event_tracker(app, settings, event_name, event_data, tracker_data):
    tracker = trackers.Tracker('static.{}'.format(tracker_data['name']))
    with open(os.path.join(settings['data_path'], event_name, tracker_data['name'])) as f:
        points = json.load(f)
    for point in points:
        point['time'] = datetime.datetime.fromtimestamp(point['time'])
    await tracker.new_points(points)
    return tracker


async def static_replay_start_event_tracker(app, settings, event_name, event_data, tracker_data):
    tracker = trackers.Tracker('static.{}'.format(tracker_data['name']))
    monitor_task = asyncio.ensure_future(static_replay(
        tracker, os.path.join(settings['data_path'], event_name, tracker_data['name']), event_data['start'], 2000))
    tracker.stop = functools.partial(trackers.cancel_and_wait_task, monitor_task)
    monitor_task.add_done_callback(functools.partial(trackers.callback_done_callback, 'Error in static_replay:', tracker.logger))
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
    cropped_tracker = trackers.Tracker('croped.{}'.format(org_tracker.name))
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



start_event_trackers = {
    'mapmytracks': trackers.map_my_tracks.start_event_tracker,
    'garmin_livetrack': trackers.garmin_livetrack.start_event_tracker,
    'static': static_start_event_tracker,
    'static_replay': static_replay_start_event_tracker,
    'cropped': start_cropped_tracker,
}
