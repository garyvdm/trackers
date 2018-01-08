import asyncio
import datetime
import json
import logging
import sys

from trackers.analyse import AnalyseTracker, get_analyse_routes
from trackers.base import Tracker


tracker = None
event_routes = None


async def setup():
    global tracker, event_routes

    with open('test_analyse_tracker_routes.json') as f:
        routes = json.load(f)

    event_routes = get_analyse_routes(routes)

    tracker = Tracker('perf_test_source')
    with open('test_analyse_tracker_perf_data.json') as f:
        points = json.load(f)
    for point in points:
        point['time'] = datetime.datetime.fromtimestamp(point['time'])
    await tracker.new_points(points)


async def test():
    await AnalyseTracker.start(tracker, None, event_routes)


if __name__ == '__main__':
    logging.basicConfig(stream=sys.stderr, level=logging.DEBUG)

    loop = asyncio.get_event_loop()
    loop.run_until_complete(setup())
    loop.run_until_complete(test())
