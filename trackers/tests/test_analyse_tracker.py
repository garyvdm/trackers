import datetime
import asyncio

import asynctest

import trackers


def d(date_string):
    return datetime.datetime.strptime(date_string, '%Y/%m/%d %H:%M:%S')


class TestAnalyseTracker(asynctest.TestCase):

    async def test_break_tracks_and_inactive(self):
        tracker = trackers.Tracker('test')
        await tracker.new_points((
            {'time': d('2017/01/01 05:00:00'), 'position': (-26.300822, 28.049444, 1800)},
            {'time': d('2017/01/01 05:01:00'), 'position': (-26.302245, 28.051139, 1800)},
            {'time': d('2017/01/01 05:30:00'), 'position': (-27.280315, 27.969365, 1800)},
            {'time': d('2017/01/01 05:31:00'), 'position': (-27.282870, 27.970620, 1800)},
        ))
        analyse_tracker = await trackers.start_analyse_tracker(tracker)
        self.assertSequenceEqual(analyse_tracker.points, (
            {'time': d('2017/01/01 05:00:00'), 'position': (-26.300822, 28.049444, 1800), 'track_id': 0, 'status': 'Active'},
            {'time': d('2017/01/01 05:01:00'), 'position': (-26.302245, 28.051139, 1800), 'track_id': 0},
            {'time': d('2017/01/01 05:21:00'), 'status': 'Inactive'},
            {'time': d('2017/01/01 05:30:00'), 'position': (-27.280315, 27.969365, 1800), 'track_id': 1, 'status': 'Active'},
            {'time': d('2017/01/01 05:31:00'), 'position': (-27.282870, 27.970620, 1800), 'track_id': 1},
        ))
        await analyse_tracker.stop()

    async def test_break_inactive_current(self):
        tracker = trackers.Tracker('test')
        t1 = datetime.datetime.now()
        await tracker.new_points((
            {'time': t1, 'position': (-26.300822, 28.049444, 1800)},
        ))
        break_time = datetime.timedelta(seconds=0.1)
        analyse_tracker = await trackers.start_analyse_tracker(tracker, track_break_time=break_time)

        await asyncio.sleep(0.05)
        # an inactive status should not be added here, as it's too soon.

        t2 = datetime.datetime.now()
        await tracker.new_points((
            {'time': t2, 'position': (-26.302245, 28.051139, 1800)},
        ))
        await asyncio.sleep(0.15)
        self.assertSequenceEqual(analyse_tracker.points, (
            {'time': t1, 'position': (-26.300822, 28.049444, 1800), 'track_id': 0, 'status': 'Active'},
            {'time': t2, 'position': (-26.302245, 28.051139, 1800), 'track_id': 0},
            {'time': t2 + break_time, 'status': 'Inactive'},
        ))
        await analyse_tracker.stop()

    async def test_break_inactive_old(self):
        tracker = trackers.Tracker('test')
        await tracker.new_points((
            {'time': d('2017/01/01 05:00:00'), 'position': (-26.300822, 28.049444, 1800)},
        ))
        analyse_tracker = await trackers.start_analyse_tracker(tracker)
        await analyse_tracker.make_inactive_fut
        self.assertSequenceEqual(analyse_tracker.points, (
            {'time': d('2017/01/01 05:00:00'), 'position': (-26.300822, 28.049444, 1800), 'track_id': 0, 'status': 'Active'},
            {'time': d('2017/01/01 05:20:00'), 'status': 'Inactive'},
        ))
        await analyse_tracker.stop()
