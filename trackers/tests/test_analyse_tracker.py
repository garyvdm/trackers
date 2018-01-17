import asyncio
import datetime
import pprint
import unittest
from contextlib import suppress

import asynctest

from trackers.analyse import AnalyseTracker, get_analyse_routes, route_elevation
from trackers.base import Tracker
from trackers.bin_utils import process_secondary_route_details


def d(date_string):
    return datetime.datetime.strptime(date_string, '%Y/%m/%d %H:%M:%S')


class TestAnalyseTracker(asynctest.TestCase):
    maxDiff = None

    async def test_break_tracks_and_inactive(self):
        tracker = Tracker('test')
        await tracker.new_points((
            {'time': d('2017/01/01 05:00:00'), 'position': (-26.300822, 28.049444, 1800)},
            {'time': d('2017/01/01 05:01:00'), 'position': (-26.302245, 28.051139, 1800)},
            {'time': d('2017/01/01 05:30:00'), 'position': (-27.280315, 27.969365, 1800)},
            {'time': d('2017/01/01 05:31:00'), 'position': (-27.282870, 27.970620, 1800)},
        ))
        tracker.completed.set_result(None)
        analyse_tracker = await AnalyseTracker.start(tracker, d('2017/01/01 05:00:00'), [])
        pprint.pprint(analyse_tracker.points)
        self.assertSequenceEqual(analyse_tracker.points, (
            {'time': d('2017/01/01 05:00:00'), 'position': (-26.300822, 28.049444, 1800), 'track_id': 0, 'status': 'Active'},
            {'time': d('2017/01/01 05:01:00'), 'position': (-26.302245, 28.051139, 1800), 'track_id': 0, 'dist_from_last': 231.0, 'speed_from_last': 13.9},
            {'time': d('2017/01/01 05:21:00'), 'status': 'Inactive'},
            {'time': d('2017/01/01 05:30:00'), 'position': (-27.280315, 27.969365, 1800), 'track_id': 1, 'status': 'Active', 'dist_from_last': 108674.0, 'speed_from_last': 224.8},
            {'time': d('2017/01/01 05:31:00'), 'position': (-27.282870, 27.970620, 1800), 'track_id': 1, 'dist_from_last': 309.0, 'speed_from_last': 18.600000000000001},
        ))
        await analyse_tracker.complete()

    async def test_break_inactive_current(self):
        tracker = Tracker('test')

        t1 = datetime.datetime.now()
        await tracker.new_points((
            {'time': t1, 'position': (-26.300822, 28.049444, 1800)},
        ))
        break_time = datetime.timedelta(seconds=0.1)
        analyse_tracker = await AnalyseTracker.start(tracker, d('2017/01/01 05:00:00'), [], track_break_time=break_time)

        await asyncio.sleep(0.05)
        # an inactive status should not be added here, as it's too soon.

        t2 = datetime.datetime.now()
        await tracker.new_points((
            {'time': t2, 'position': (-26.302245, 28.051139, 1800)},
        ))
        await asyncio.sleep(0.15)
        tracker.completed.set_result(None)

        # Speed changes for each run, so remove it from assert.
        for point in analyse_tracker.points:
            with suppress(KeyError):
                del point['speed_from_last']

        pprint.pprint(analyse_tracker.points)
        self.assertSequenceEqual(analyse_tracker.points, (
            {'time': t1, 'position': (-26.300822, 28.049444, 1800), 'track_id': 0, 'status': 'Active'},
            {'time': t2, 'position': (-26.302245, 28.051139, 1800), 'track_id': 0, 'dist_from_last': 231},
            {'time': t2 + break_time, 'status': 'Inactive'},
        ))
        await analyse_tracker.complete()

    async def test_break_inactive_old(self):
        tracker = Tracker('test')
        await tracker.new_points((
            {'time': d('2017/01/01 05:00:00'), 'position': (-26.300822, 28.049444, 1800)},
        ))
        tracker.completed.set_result(None)
        analyse_tracker = await AnalyseTracker.start(tracker, d('2017/01/01 05:00:00'), [])
        await analyse_tracker.make_inactive_fut
        self.assertSequenceEqual(analyse_tracker.points, (
            {'time': d('2017/01/01 05:00:00'), 'position': (-26.300822, 28.049444, 1800), 'track_id': 0, 'status': 'Active'},
            {'time': d('2017/01/01 05:20:00'), 'status': 'Inactive'},
        ))
        await analyse_tracker.complete()

    async def test_with_route(self):
        tracker = Tracker('test')
        routes = [
            {
                'main': True,
                'points': [
                    [-26.300420, 28.049410],
                    [-26.315691, 28.062354],
                    [-26.322250, 28.042440],
                ]
            },
        ]
        event_routes = get_analyse_routes(routes)

        await tracker.new_points((
            {'time': d('2017/01/01 05:00:00'), 'position': (-26.300824, 28.050185, 1800)},
            {'time': d('2017/01/01 05:01:00'), 'position': (-26.322167, 28.042920, 1800)},
        ))
        tracker.completed.set_result(None)
        analyse_tracker = await AnalyseTracker.start(tracker, d('2017/01/01 05:00:00'), event_routes)
        await analyse_tracker.complete()

        pprint.pprint(analyse_tracker.points)
        self.assertSequenceEqual(analyse_tracker.points, (
            {'time': d('2017/01/01 05:00:00'), 'position': (-26.300824, 28.050185, 1800), 'track_id': 0, 'status': 'Active', 'dist_route': 82.0},
            {'time': d('2017/01/01 05:01:00'), 'position': (-26.322167, 28.042920, 1800), 'track_id': 0, 'dist_from_last': 4116.0, 'dist_route': 4198.0, 'speed_from_last': 247.0, 'finished_time': d('2017/01/01 05:01:00'), 'rider_status': 'Finished'},
            {'status': 'Inactive', 'time': datetime.datetime(2017, 1, 1, 5, 21)},
        ))

    async def test_with_route_points_same_time(self):
        # test to make sure we don't do division by zero when doing speed calcs.

        tracker = Tracker('test')
        routes = [
            {
                'main': True,
                'points': [
                    [-26.300420, 28.049410],
                    [-26.315691, 28.062354],
                    [-26.322250, 28.042440],
                ]
            },
        ]
        event_routes = get_analyse_routes(routes)

        await tracker.new_points((
            {'time': d('2017/01/01 05:00:00'), 'position': (-26.300824, 28.050185, 1800)},
            {'time': d('2017/01/01 05:00:00'), 'position': (-26.322167, 28.042920, 1800)},
        ))
        # Time is the same for both points.

        tracker.completed.set_result(None)
        analyse_tracker = await AnalyseTracker.start(tracker, d('2017/01/01 05:00:00'), event_routes)
        await analyse_tracker.complete()

        pprint.pprint(analyse_tracker.points)
        self.assertSequenceEqual(analyse_tracker.points, (
            {'time': d('2017/01/01 05:00:00'), 'position': (-26.300824, 28.050185, 1800), 'track_id': 0, 'status': 'Active', 'dist_route': 82.0},
            {'time': d('2017/01/01 05:00:00'), 'position': (-26.322167, 28.042920, 1800), 'track_id': 0, 'dist_from_last': 4116.0, 'dist_route': 4198.0, 'finished_time': d('2017/01/01 05:00:00'), 'rider_status': 'Finished'},
            {'status': 'Inactive', 'time': datetime.datetime(2017, 1, 1, 5, 20)},
        ))

    async def test_with_route_alt(self):
        tracker = Tracker('test')
        routes = [
            {
                'points': [
                    [-26.300420, 28.049410],
                    [-26.315685, 28.062377],
                    [-26.381378, 28.067689],
                    [-26.417153, 28.072707],
                ],
            },
            {
                'points': [
                    [-26.315685, 28.062377],
                    [-26.324918, 27.985781],
                    [-26.381378, 28.067689],
                ],
            },
        ]

        process_secondary_route_details(routes)
        event_routes = get_analyse_routes(routes)
        pprint.pprint(event_routes)

        await tracker.new_points((
            {'time': d('2017/01/01 05:00:00'), 'position': (-26.300824, 28.050185, 1800)},
            {'time': d('2017/01/01 05:01:00'), 'position': (-26.325051, 27.985600, 1800)},
            {'time': d('2017/01/01 05:02:00'), 'position': (-26.417149, 28.073087, 1800)},
        ))
        tracker.completed.set_result(None)
        analyse_tracker = await AnalyseTracker.start(tracker, d('2017/01/01 05:00:00'), event_routes)
        await analyse_tracker.complete()

        pprint.pprint(analyse_tracker.points)
        self.assertSequenceEqual(analyse_tracker.points, [
            {'time': d('2017/01/01 05:00:00'), 'position': (-26.300824, 28.050185, 1800), 'track_id': 0, 'status': 'Active', 'dist_route': 82.0},
            {'time': d('2017/01/01 05:01:00'), 'position': (-26.325051, 27.9856, 1800), 'track_id': 0, 'dist_from_last': 5174.0, 'dist_route': 5256.0, 'speed_from_last': 310.39999999999998, },
            {'time': d('2017/01/01 05:02:00'), 'position': (-26.417149, 28.073087, 1800), 'track_id': 0, 'dist_from_last': 8167.0, 'dist_route': 13423.0, 'speed_from_last': 490.0, 'finished_time': d('2017/01/01 05:02:00'), 'rider_status': 'Finished'},
            {'status': 'Inactive', 'time': datetime.datetime(2017, 1, 1, 5, 22)},
        ])

    async def test_stop(self):
        tracker = Tracker('test')
        tracker.stop = lambda: tracker.completed.cancel()

        t1 = datetime.datetime.now()
        await tracker.new_points((
            {'time': t1, 'position': (-26.300822, 28.049444, 1800)},
        ))
        break_time = datetime.timedelta(seconds=1)
        analyse_tracker = await AnalyseTracker.start(tracker, d('2017/01/01 05:00:00'), [], track_break_time=break_time)

        await asyncio.sleep(0.05)
        analyse_tracker.stop()
        await analyse_tracker.complete()

    async def test_with_circular_route(self):
        tracker = Tracker('test')
        routes = [
            {
                'main': True,
                'points': [
                    [-27.881250000, 27.919840000],
                    [-27.862210000, 27.917000000],
                    [-27.743550000, 27.942480000],
                    [-27.843790000, 28.164510000],
                    [-27.945580000, 28.044930000],
                    [-27.880490000, 27.917450000],
                    [-27.860440000, 27.918080000],
                    [-27.779830000, 27.746380000],
                    [-27.900190000, 27.668620000],
                    [-28.043810000, 27.969710000],
                    [-27.933350000, 28.028700000],
                    [-27.881250000, 27.919840000],
                ],
                'split_at_dist': [35000, 115000],
                'split_point_range': 10000,
            },
        ]
        event_routes = get_analyse_routes(routes)

        await tracker.new_points((
            {'time': d('2017/01/01 05:00:00'), 'position': (-27.880490000, 27.917450000, 1800)},
            {'time': d('2017/01/01 05:01:00'), 'position': (-27.843790000, 28.164510000, 1800)},
            {'time': d('2017/01/01 05:02:00'), 'position': (-27.945580000, 28.044930000, 1800)},
            {'time': d('2017/01/01 05:03:00'), 'position': (-27.881250000, 27.919840000, 1800)},
            {'time': d('2017/01/01 05:04:00'), 'position': (-27.779830000, 27.746380000, 1800)},
            {'time': d('2017/01/01 05:05:00'), 'position': (-28.043810000, 27.969710000, 1800)},
            {'time': d('2017/01/01 05:06:00'), 'position': (-27.880490000, 27.917450000, 1800)},
        ))
        tracker.completed.set_result(None)
        analyse_tracker = await AnalyseTracker.start(tracker, d('2017/01/01 05:00:00'), event_routes)
        await analyse_tracker.complete()

        pprint.pprint(analyse_tracker.points)
        self.assertSequenceEqual(analyse_tracker.points, [
            {'time': d('2017/01/01 05:00:00'), 'position': (-27.880490000, 27.917450000, 1800), 'track_id': 0, 'dist_route': 114.0, 'status': 'Active', },
            {'time': d('2017/01/01 05:01:00'), 'position': (-27.843790000, 28.164510000, 1800), 'track_id': 0, 'dist_from_last': 39940.0, 'dist_route': 40054.0, 'speed_from_last': 2396.4, },
            {'time': d('2017/01/01 05:02:00'), 'position': (-27.945580000, 28.044930000, 1800), 'track_id': 0, 'dist_from_last': 16305.0, 'dist_route': 56359.0, 'speed_from_last': 978.3, },
            {'time': d('2017/01/01 05:03:00'), 'position': (-27.881250000, 27.919840000, 1800), 'track_id': 0, 'dist_from_last': 14229.0, 'dist_route': 70588.0, 'speed_from_last': 853.7, },
            {'time': d('2017/01/01 05:04:00'), 'position': (-27.779830000, 27.746380000, 1800), 'track_id': 0, 'dist_from_last': 21599.0, 'dist_route': 92187.0, 'speed_from_last': 1295.9, },
            {'time': d('2017/01/01 05:05:00'), 'position': (-28.043810000, 27.969710000, 1800), 'track_id': 0, 'dist_from_last': 49009.0, 'dist_route': 141196.0, 'speed_from_last': 2940.5, },
            {'time': d('2017/01/01 05:06:00'), 'position': (-27.880490000, 27.917450000, 1800), 'track_id': 0, 'dist_from_last': 25720.0, 'dist_route': 166916.0, 'speed_from_last': 1543.2, 'finished_time': d('2017/01/01 05:06:00'), 'rider_status': 'Finished', },
            {'time': d('2017/01/01 05:26:00'), 'status': 'Inactive'},
        ])

    async def test_get_predicted_position(self):
        tracker = Tracker('test')
        routes = [
            {
                'main': True,
                'points': [
                    [-27.881250000, 27.919840000],
                    [-27.862210000, 27.917000000],
                    [-27.743550000, 27.942480000],
                    [-27.843790000, 28.164510000],
                    [-27.945580000, 28.044930000],
                ]
            },
        ]
        event_routes = get_analyse_routes(routes)

        await tracker.new_points((
            {'time': d('2017/01/01 05:00:00'), 'position': (-27.880490000, 27.917450000, 1800)},
            {'time': d('2017/01/01 05:01:00'), 'position': (-27.843790000, 28.164510000, 1800)},
        ))
        analyse_tracker = await AnalyseTracker.start(tracker, d('2017/01/01 05:00:00'), event_routes)
        pprint.pprint(analyse_tracker.points)

        predicted_point = analyse_tracker.get_predicted_position(d('2017/01/01 05:01:30'))
        pprint.pprint(predicted_point)

        tracker.completed.set_result(None)
        await analyse_tracker.complete()


class TestRouteElevation(unittest.TestCase):

    def test_route_elevation(self):
        routes = [
            {
                'main': True,
                'elevation': [
                    [0, 0.01, 100, 0],
                    [0, 0.02, 200, 1113],
                    [0, 0.03, 300, 2226],
                ]
            },
        ]

        self.assertEqual(route_elevation(routes[0], 0), 100)
        self.assertEqual(route_elevation(routes[0], 1113), 200)
        self.assertEqual(route_elevation(routes[0], 1669.5), 250)
        self.assertEqual(route_elevation(routes[0], 2226), 300)
