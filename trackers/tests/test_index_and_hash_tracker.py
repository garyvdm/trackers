import pprint

import asynctest

from trackers.base import Tracker
from trackers.general import index_and_hash_tracker


class Test(asynctest.TestCase):
    maxDiff = None

    async def test(self):
        tracker = Tracker('test')
        await tracker.new_points((
            {'position': (-26.300822, 28.049444, 1800)},
            {'position': (-26.302245, 28.051139, 1800)},
        ))
        ih_tracker = await index_and_hash_tracker(tracker)
        await tracker.new_points((
            {'position': (-27.280315, 27.969365, 1800)},
            {'position': (-27.282870, 27.970620, 1800)},
        ))
        tracker.completed.set_result(None)
        await ih_tracker.complete()

        pprint.pprint(ih_tracker.points)
        self.assertSequenceEqual(ih_tracker.points, [
            {'hash': 'y1sH', 'index': 0, 'position': (-26.300822, 28.049444, 1800)},
            {'hash': '-fC_', 'index': 1, 'position': (-26.302245, 28.051139, 1800)},
            {'hash': 'A1jI', 'index': 2, 'position': (-27.280315, 27.969365, 1800)},
            {'hash': 'AvnU', 'index': 3, 'position': (-27.282870, 27.970620, 1800)},
        ])

    async def test_reset_and_change(self):
        tracker = Tracker('test')
        ih_tracker = await index_and_hash_tracker(tracker)

        await tracker.new_points((
            {'position': (-26.300822, 28.049444, 1800)},
            {'position': (-26.302245, 28.051139, 1800)},
        ))

        pprint.pprint(ih_tracker.points)
        self.assertSequenceEqual(ih_tracker.points, [
            {'hash': 'y1sH', 'index': 0, 'position': (-26.300822, 28.049444, 1800)},
            {'hash': '-fC_', 'index': 1, 'position': (-26.302245, 28.051139, 1800)},
        ])

        await tracker.reset_points()
        self.assertSequenceEqual(ih_tracker.points, [])

        await tracker.new_points((
            {'position': (-27.280315, 27.969365, 1800)},
            {'position': (-27.282870, 27.970620, 1800)},
        ))

        pprint.pprint(ih_tracker.points)
        self.assertSequenceEqual(ih_tracker.points, [
            {'hash': 'fdCj', 'index': 0, 'position': (-27.280315, 27.969365, 1800)},
            {'hash': 'rUnG', 'index': 1, 'position': (-27.282870, 27.970620, 1800)},
        ])

        tracker.completed.set_result(None)
        await ih_tracker.complete()
