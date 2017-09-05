import asyncio
import datetime
import json
import logging
import sys

import yaml

from trackers.analyse import get_expanded_routes, start_analyse_tracker
from trackers.base import Tracker


tracker = None
event_routes = None


async def setup():
    global tracker, event_routes

    with open('test_analyse_tracker_routes.yaml') as f:
        routes = yaml.load(f)
    event_routes = get_expanded_routes(routes)

    tracker = Tracker('perf_test_source')
    with open('test_analyse_tracker_perf_data.json') as f:
        points = json.load(f)
    for point in points:
        point['time'] = datetime.datetime.fromtimestamp(point['time'])
    await tracker.new_points(points)


async def test():
    await start_analyse_tracker(tracker, None, event_routes)


logging.basicConfig(stream=sys.stderr, level=logging.DEBUG)

loop = asyncio.get_event_loop()
loop.run_until_complete(setup())
loop.run_until_complete(test())
