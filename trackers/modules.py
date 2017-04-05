import asyncio
import os

import yaml

import trackers.garmin_livetrack
import trackers.map_my_tracks
from trackers.async_exit_stack import AsyncExitStack


async def config_modules(app, settings):
    exit_stack = AsyncExitStack()

    modules = (
        trackers.map_my_tracks.config,
        trackers.garmin_livetrack.config,
    )

    for module in modules:
        await exit_stack.enter_context(await module(app, settings))
    return exit_stack


async def static_start_event_tracker(app, settings, event_name, event_data, tracker_data):
    tracker = trackers.Tracker('static.{}'.format(tracker_data['name']))
    monitor_task = asyncio.ensure_future(static_load(
        tracker, os.path.join(settings['data_path'], event_name, tracker_data['name'])))
    return tracker, monitor_task


async def static_load(tracker, path):
    with open(path) as f:
        points = yaml.load(f)
    await tracker.new_points(points)

start_event_trackers = {
    'mapmytracks': trackers.map_my_tracks.start_event_tracker,
    'garmin_livetrack': trackers.garmin_livetrack.start_event_tracker,
    'static': static_start_event_tracker,
}
