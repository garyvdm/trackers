import asyncio
import datetime
import pprint
import unittest
from datetime import timedelta

import asynctest

from trackers.analyse import AnalyseTracker, get_analyse_routes, route_elevation
from trackers.base import Tracker
from trackers.bin_utils import process_secondary_route_details


def d(date_string):
    return datetime.datetime.strptime(date_string, '%Y/%m/%d %H:%M:%S')


def repr_point_value(value):
    if isinstance(value, timedelta):
        return repr(value)[9:]
    if isinstance(value, datetime.datetime):
        return f"d('{value:%Y/%m/%d %H:%M:%S}')"
    return repr(value)


def print_points(points):
    order_dict = {
        'time': 0,
        'position': 1,
        'track_id': 2,
    }
    print('[')
    for point in points:
        items = sorted(point.items(), key=lambda item: (order_dict.get(item[0], 10), item[0]))
        items_formated = [f'{key!r}: {repr_point_value(value)}' for key, value in items]
        print('            {{{}}}, '.format(', '.join(items_formated)))
    print(']')


def filter_keys(items, keys_to_keep=None, keys_to_remove=None):
    if (not keys_to_remove and not keys_to_keep) or (keys_to_remove and keys_to_keep):
        raise ValueError('Must provide keys_to_keep or keys_to_remove.')
    if keys_to_keep:
        return [{key: value for key, value in item.items() if key in keys_to_keep} for item in items]
    if keys_to_remove:
        return [{key: value for key, value in item.items() if key not in keys_to_remove} for item in items]


class TestAnalyseTracker(asynctest.TestCase):
    maxDiff = None

    async def test_break_tracks(self):
        tracker = Tracker('test')
        await tracker.new_points((
            {'time': d('2017/01/01 05:00:00'), 'position': (-26.300822, 28.049444, 1800)},
            {'time': d('2017/01/01 05:01:00'), 'position': (-26.302245, 28.051139, 1800)},
            {'time': d('2017/01/01 05:30:00'), 'position': (-27.280315, 27.969365, 1800)},
            {'time': d('2017/01/01 05:31:00'), 'position': (-27.282870, 27.970620, 1800)},
        ))
        tracker.completed.set_result(None)
        analyse_tracker = await AnalyseTracker.start(tracker, d('2017/01/01 05:00:00'), [], track_break_time=timedelta(minutes=20))
        await analyse_tracker.complete()

        points = filter_keys(analyse_tracker.points, keys_to_keep=('track_id',))
        print_points(points)
        self.assertSequenceEqual(points, [
            {'track_id': 0},
            {'track_id': 0},
            {'track_id': 1},
            {'track_id': 1},
        ])

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

        points = filter_keys(analyse_tracker.points, keys_to_remove=('time', 'position'))
        print_points(points)
        self.assertSequenceEqual(points, [
            {'track_id': 0, 'dist': 82.0, 'dist_from_last': 82.0, 'dist_route': 82.0},
            {'track_id': 0, 'dist': 4198.0, 'dist_from_last': 4116.0, 'dist_route': 4198.0,
             'speed_from_last': 247.0, 'time_from_last': timedelta(0, 60),
             'finished_time': d('2017/01/01 05:01:00'), 'rider_status': 'Finished', },
        ])

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
            {'time': d('2017/01/01 05:00:00'), 'position': (-26.300824, 28.050200, 1800)},
        ))
        # Time is the same for both points.

        tracker.completed.set_result(None)
        analyse_tracker = await AnalyseTracker.start(tracker, d('2017/01/01 05:00:00'), event_routes)
        await analyse_tracker.complete()

        print_points(analyse_tracker.points)
        # No assert, just check for no errors

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
        # pprint.pprint(event_routes)

        await tracker.new_points((
            {'time': d('2017/01/01 05:00:00'), 'position': (-26.300824, 28.050185, 1800)},
            {'time': d('2017/01/01 06:00:00'), 'position': (-26.325051, 27.985600, 1800)},
            {'time': d('2017/01/01 07:00:00'), 'position': (-26.417149, 28.073087, 1800)},
        ))
        tracker.completed.set_result(None)
        analyse_tracker = await AnalyseTracker.start(tracker, d('2017/01/01 05:00:00'), event_routes)
        await analyse_tracker.complete()

        # TODO: does this actually test that we are getting the dist from the alt route?
        points = filter_keys(analyse_tracker.points, keys_to_keep=('dist_route', ))
        print_points(points)
        self.assertSequenceEqual(points, [
            {'dist_route': 82.0},
            {'dist_route': 5256.0},
            {'dist_route': 13423.0},
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
        try:
            await analyse_tracker.complete()
        except asyncio.CancelledError:
            pass

    async def test_with_circular_route(self):
        tracker = Tracker('test')
        routes = [
            {
                'main': True,
                'points': [
                    [-27.88125, 27.91984],
                    [-27.86221, 27.91700],
                    [-27.74355, 27.94248],
                    [-27.84379, 28.16451],
                    [-27.94558, 28.04493],
                    [-27.88049, 27.91745],
                    [-27.86044, 27.91808],
                    [-27.77983, 27.74638],
                    [-27.90019, 27.66862],
                    [-28.04381, 27.96971],
                    [-27.93335, 28.02870],
                    [-27.88125, 27.91984],
                ],
                'split_at_dist': [35000, 115000],
                'split_point_range': 10000,
                'circular_range': 50000,
            },
        ]
        event_routes = get_analyse_routes(routes)

        await tracker.new_points((
            {'time': d('2017/01/01 01:05:00'), 'position': (-27.88049, 27.91745, 1800)},
            {'time': d('2017/01/01 02:00:00'), 'position': (-27.84379, 28.16451, 1800)},
            {'time': d('2017/01/01 03:00:00'), 'position': (-27.94558, 28.04493, 1800)},
            {'time': d('2017/01/01 04:00:00'), 'position': (-27.88125, 27.91984, 1800)},
            {'time': d('2017/01/01 05:00:00'), 'position': (-27.77983, 27.74638, 1800)},
            {'time': d('2017/01/01 06:00:00'), 'position': (-28.04381, 27.96971, 1800)},
            {'time': d('2017/01/01 07:00:00'), 'position': (-27.88049, 27.91745, 1800)},
        ))
        tracker.completed.set_result(None)
        analyse_tracker = await AnalyseTracker.start(tracker, d('2017/01/01 01:00:00'), event_routes)
        await analyse_tracker.complete()

        points = filter_keys(analyse_tracker.points, ('dist_route', ))
        print_points(points)
        self.assertSequenceEqual(points, [
            {'dist_route': 114.0},
            {'dist_route': 40054.0},
            {'dist_route': 56359.0},
            {'dist_route': 70588.0},
            {'dist_route': 92187.0},
            {'dist_route': 141196.0},
            {'dist_route': 166916.0},
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
        print_points(analyse_tracker.points)

        predicted_point = analyse_tracker.get_predicted_position(d('2017/01/01 05:01:30'))
        pprint.pprint(predicted_point)

        tracker.completed.set_result(None)
        await analyse_tracker.complete()

    async def test_reset(self):
        tracker = Tracker('test')
        await tracker.new_points((
            {'time': d('2017/01/01 05:00:00'), 'position': (-26.300822, 28.049444, 1800)},
            {'time': d('2017/01/01 05:01:00'), 'position': (-26.302245, 28.051139, 1800)},
            # {'time': d('2017/01/01 05:30:00'), 'position': (-27.280315, 27.969365, 1800)},
            # {'time': d('2017/01/01 05:31:00'), 'position': (-27.282870, 27.970620, 1800)},
        ))
        analyse_tracker = await AnalyseTracker.start(tracker, d('2017/01/01 05:00:00'), [])
        await analyse_tracker.process_initial_points_fut

        print_points(analyse_tracker.points)
        self.assertSequenceEqual(analyse_tracker.points, [
            {'time': d('2017/01/01 05:00:00'), 'position': (-26.300822, 28.049444, 1800), 'track_id': 0},
            {'time': d('2017/01/01 05:01:00'), 'position': (-26.302245, 28.051139, 1800), 'track_id': 0, 'dist': 231.0, 'dist_from_last': 231.0, 'speed_from_last': 13.9, 'time_from_last': timedelta(0, 60)},
        ])

        await tracker.reset_points()
        self.assertSequenceEqual(analyse_tracker.points, [])

        await tracker.new_points((
            {'time': d('2017/01/01 05:30:00'), 'position': (-27.280315, 27.969365, 1800)},
            {'time': d('2017/01/01 05:31:00'), 'position': (-27.282870, 27.970620, 1800)},
        ))
        print_points(analyse_tracker.points)
        self.assertSequenceEqual(analyse_tracker.points, [
            {'time': d('2017/01/01 05:30:00'), 'position': (-27.280315, 27.969365, 1800), 'track_id': 0},
            {'time': d('2017/01/01 05:31:00'), 'position': (-27.28287, 27.97062, 1800), 'track_id': 0, 'dist': 309.0, 'dist_from_last': 309.0, 'speed_from_last': 18.6, 'time_from_last': timedelta(0, 60)},
        ])

        tracker.completed.set_result(None)
        await analyse_tracker.complete()

    async def test_pre_post(self):
        tracker = Tracker('test')
        await tracker.new_points((
            # Pre
            {'time': d('2017/01/01 02:00:00'), 'position': (-26.300822, 28.049444, 1800)},
            {'time': d('2017/01/01 02:01:00'), 'position': (-26.302245, 28.051139, 1800)},
            {'time': d('2017/01/01 02:30:00'), 'position': (-27.280315, 27.969365, 1800)},
            {'time': d('2017/01/01 02:31:00'), 'position': (-27.282870, 27.970620, 1800)},

            # During
            {'time': d('2017/01/01 05:00:00'), 'position': (-26.300822, 28.049444, 1800)},
            {'time': d('2017/01/01 05:01:00'), 'position': (-26.302245, 28.051139, 1800), 'status': 'Finished'},

            # Post
            {'time': d('2017/01/01 05:02:00'), 'position': (-27.280315, 27.969365, 1800)},
            {'time': d('2017/01/01 05:03:00'), 'position': (-27.282870, 27.970620, 1800)},

        ))
        tracker.completed.set_result(None)
        analyse_tracker = await AnalyseTracker.start(tracker, d('2017/01/01 05:00:00'), [], track_break_time=timedelta(minutes=20))
        await analyse_tracker.complete()

        points = filter_keys(analyse_tracker.pre_post_tracker.points, keys_to_keep=('track_id', 'status'))
        print_points(points)
        self.assertSequenceEqual(points, [
            {'track_id': 0},
            {'track_id': 0},
            {'track_id': 1},
            {'track_id': 1},
            {'track_id': 2},
            {'track_id': 2},
        ])
        print_points(analyse_tracker.points)


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
