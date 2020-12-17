import asyncio
from datetime import datetime

import asynctest

from trackers.base import Tracker
from trackers.combined import Combined


def d(date_string):
    return datetime.strptime(date_string, '%Y/%m/%d %H:%M:%S')


class TestCombinedTracker(asynctest.TestCase):

    async def test_basic(self):
        tracker1 = Tracker('tracker1')
        tracker2 = Tracker('tracker2')
        await tracker1.new_points([{'time': d('2017/01/01 05:05:00'), 'item': 1}])

        new_points_callback = asynctest.CoroutineMock()
        reset_points_callback = asynctest.CoroutineMock()

        combined = await Combined.start(
            'combined', (tracker1, tracker2),
            new_points_callbacks=(new_points_callback, ),
            reset_points_callbacks=(reset_points_callback, ),
        )

        new_points_callback.assert_called_once_with(combined, [{'time': d('2017/01/01 05:05:00'), 'item': 1}])
        new_points_callback.reset_mock()

        await tracker1.new_points([{'time': d('2017/01/01 05:10:00'), 'item': 2}])
        new_points_callback.assert_called_once_with(combined, [{'time': d('2017/01/01 05:10:00'), 'item': 2}])
        new_points_callback.reset_mock()

        await tracker2.new_points([{'time': d('2017/01/01 05:00:00'), 'item': 3}])
        reset_points_callback.assert_called_once_with(combined)
        new_points_callback.assert_called_once_with(combined, [
            {'time': d('2017/01/01 05:00:00'), 'item': 3},
            {'time': d('2017/01/01 05:05:00'), 'item': 1},
            {'time': d('2017/01/01 05:10:00'), 'item': 2},
        ])
        reset_points_callback.reset_mock()
        new_points_callback.reset_mock()

        combined.stop()
        await asyncio.wait_for(combined.complete(), timeout=0.5)
