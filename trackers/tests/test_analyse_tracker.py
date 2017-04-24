import asyncio
import datetime
import pprint

import asynctest

import trackers


def d(date_string):
    return datetime.datetime.strptime(date_string, '%Y/%m/%d %H:%M:%S')


class TestAnalyseTracker(asynctest.TestCase):
    maxDiff = None

    async def test_break_tracks_and_inactive(self):
        tracker = trackers.Tracker('test')
        await tracker.new_points((
            {'time': d('2017/01/01 05:00:00'), 'position': (-26.300822, 28.049444, 1800)},
            {'time': d('2017/01/01 05:01:00'), 'position': (-26.302245, 28.051139, 1800)},
            {'time': d('2017/01/01 05:30:00'), 'position': (-27.280315, 27.969365, 1800)},
            {'time': d('2017/01/01 05:31:00'), 'position': (-27.282870, 27.970620, 1800)},
        ))
        analyse_tracker = await trackers.start_analyse_tracker(tracker, {}, [])
        pprint.pprint(analyse_tracker.points)
        self.assertSequenceEqual(analyse_tracker.points, (
            {'time': d('2017/01/01 05:00:00'), 'position': (-26.300822, 28.049444, 1800), 'track_id': 0, 'status': 'Active'},
            {'time': d('2017/01/01 05:01:00'), 'position': (-26.302245, 28.051139, 1800), 'track_id': 0, 'dist_from_last': 231, 'dist_ridden': 231},
            {'time': d('2017/01/01 05:21:00'), 'status': 'Inactive'},
            {'time': d('2017/01/01 05:30:00'), 'position': (-27.280315, 27.969365, 1800), 'track_id': 1, 'status': 'Active', 'dist_from_last': 108675, 'dist_ridden': 108906},
            {'time': d('2017/01/01 05:31:00'), 'position': (-27.282870, 27.970620, 1800), 'track_id': 1, 'dist_from_last': 309, 'dist_ridden': 109216},
        ))
        await analyse_tracker.stop()

    async def test_break_inactive_current(self):
        tracker = trackers.Tracker('test')
        t1 = datetime.datetime.now()
        await tracker.new_points((
            {'time': t1, 'position': (-26.300822, 28.049444, 1800)},
        ))
        break_time = datetime.timedelta(seconds=0.1)
        analyse_tracker = await trackers.start_analyse_tracker(tracker, {}, [], track_break_time=break_time)

        await asyncio.sleep(0.05)
        # an inactive status should not be added here, as it's too soon.

        t2 = datetime.datetime.now()
        await tracker.new_points((
            {'time': t2, 'position': (-26.302245, 28.051139, 1800)},
        ))
        await asyncio.sleep(0.15)
        pprint.pprint(analyse_tracker.points)
        self.assertSequenceEqual(analyse_tracker.points, (
            {'time': t1, 'position': (-26.300822, 28.049444, 1800), 'track_id': 0, 'status': 'Active'},
            {'time': t2, 'position': (-26.302245, 28.051139, 1800), 'track_id': 0, 'dist_from_last': 231, 'dist_ridden': 231,},
            {'time': t2 + break_time, 'status': 'Inactive'},
        ))
        await analyse_tracker.stop()

    async def test_break_inactive_old(self):
        tracker = trackers.Tracker('test')
        await tracker.new_points((
            {'time': d('2017/01/01 05:00:00'), 'position': (-26.300822, 28.049444, 1800)},
        ))
        analyse_tracker = await trackers.start_analyse_tracker(tracker, {}, [])
        await analyse_tracker.make_inactive_fut
        self.assertSequenceEqual(analyse_tracker.points, (
            {'time': d('2017/01/01 05:00:00'), 'position': (-26.300822, 28.049444, 1800), 'track_id': 0, 'status': 'Active'},
            {'time': d('2017/01/01 05:20:00'), 'status': 'Inactive'},
        ))
        await analyse_tracker.stop()

    async def test_with_route(self):
        tracker = trackers.Tracker('test')
        event_data = {
            'routes': [
                [
                    [-26.300420, 28.049410],
                    [-26.315691, 28.062354],
                    [-26.322250, 28.042440],
                ]
            ]
        }
        event_routes = trackers.get_expanded_routes(event_data['routes'])

        await tracker.new_points((
            {'time': d('2017/01/01 05:00:00'), 'position': (-26.300824, 28.050185, 1800)},
            {'time': d('2017/01/01 05:01:00'), 'position': (-26.322167, 28.042920, 1800)},
        ))
        analyse_tracker = await trackers.start_analyse_tracker(tracker, event_data, event_routes)
        await analyse_tracker.stop()

        pprint.pprint(analyse_tracker.points)
        self.assertSequenceEqual(analyse_tracker.points, (
            {'time': d('2017/01/01 05:00:00'), 'position': (-26.300824, 28.050185, 1800), 'track_id': 0, 'status': 'Active', 'dist_route': 82},
            {'time': d('2017/01/01 05:01:00'), 'position': (-26.322167, 28.042920, 1800), 'track_id': 0, 'dist_from_last': 2473, 'dist_ridden': 2473, 'dist_route': 4198, 'finished_time': d('2017/01/01 05:01:00'),},
        ))

    async def test_with_route_alt(self):
        tracker = trackers.Tracker('test')
        event_data = {
            'routes': [
                [
                    [-26.300420, 28.049410],
                    [-26.315685, 28.062377],
                    [-26.381378, 28.067689],
                    [-26.417153, 28.072707],
                ],
                [
                    [-26.315685, 28.062377],
                    [-26.324918, 27.985781],
                    [-26.381378, 28.067689],
                ],
            ]
        }
        event_routes = trackers.get_expanded_routes(event_data['routes'])
        pprint.pprint(event_routes)

        await tracker.new_points((
            {'time': d('2017/01/01 05:00:00'), 'position': (-26.300824, 28.050185, 1800)},
            {'time': d('2017/01/01 05:01:00'), 'position': (-26.325051, 27.985600, 1800)},
            {'time': d('2017/01/01 05:02:00'), 'position': (-26.417149, 28.073087, 1800)},
        ))
        analyse_tracker = await trackers.start_analyse_tracker(tracker, event_data, event_routes)
        await analyse_tracker.stop()

        pprint.pprint(analyse_tracker.points)
        self.assertSequenceEqual(analyse_tracker.points, [
            {'time': d('2017/01/01 05:00:00'), 'position': (-26.300824, 28.050185, 1800), 'track_id': 0, 'status': 'Active', 'dist_route': 82},
            {'time': d('2017/01/01 05:01:00'), 'position': (-26.325051, 27.9856, 1800), 'track_id': 0, 'dist_from_last': 6985, 'dist_ridden': 6985, 'dist_route': 5256,},
            {'time': d('2017/01/01 05:02:00'), 'position': (-26.417149, 28.073087, 1800), 'track_id': 0, 'dist_from_last': 13430, 'dist_ridden': 20415, 'dist_route': 13423, 'finished_time': d('2017/01/01 05:02:00')},
        ])
