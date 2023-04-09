from datetime import datetime, timedelta
from unittest import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock

import fixtures
from dulwich.repo import MemoryRepo
from dulwich_tree import TreeWriter

from trackers.base import Tracker
from trackers.events import Event
from trackers.general import (
    cropped_tracker_start,
    start_replay_tracker,
    static_start_event_tracker,
)
from trackers.tests import get_test_app_and_settings


class TestStatic(IsolatedAsyncioTestCase, fixtures.TestWithFixtures):
    async def test_start_msgpack(self):
        repo = MemoryRepo()
        writer = TreeWriter(repo)
        writer.set_data("events/test_event/data.yaml", "{}".encode())
        writer.set_data(
            "events/test_event/test_rider",
            b"\x91\x82\xa4time\xcbA\xd6\x1a\n\x98\x00\x00\x00\xa3bar\xa3foo",
        )
        writer.do_commit(b"add test_event")

        app, settings = get_test_app_and_settings(repo)
        event = await Event.load(app, "test_event", writer)
        tracker = await static_start_event_tracker(
            app,
            event,
            "Test rider",
            {"name": "test_rider", "format": "msgpack"},
            None,
            None,
        )
        await tracker.complete()
        self.assertEqual(len(tracker.points), 1)
        self.assertEqual(
            tracker.points[0],
            {
                "time": datetime(2017, 1, 1),
                "bar": "foo",
            },
        )

    async def test_start_json(self):
        repo = MemoryRepo()
        writer = TreeWriter(repo)
        writer.set_data("events/test_event/data.yaml", "{}".encode())
        writer.set_data("events/test_event/test_rider", "[]".encode())
        writer.do_commit(b"add test_event")

        app, settings = get_test_app_and_settings(repo)
        event = await Event.load(app, "test_event", writer)
        tracker = await static_start_event_tracker(
            app,
            event,
            "Test rider",
            {"name": "test_rider", "format": "json"},
            None,
            None,
        )
        await tracker.complete()
        self.assertEqual(tracker.points, [])


class TestCropped(IsolatedAsyncioTestCase):
    async def test_with_start(self):
        org_tracker = Tracker("test")
        await org_tracker.new_points(
            [
                {"i": 0, "time": datetime(2017, 1, 1, 5, 55)},
                {"i": 1, "time": datetime(2017, 1, 1, 6, 5)},
            ]
        )
        org_tracker.completed.set_result(None)

        tracker = await cropped_tracker_start(org_tracker, {"start": datetime(2017, 1, 1, 6, 0)})
        await tracker.complete()
        self.assertEqual(len(tracker.points), 1)
        self.assertEqual(tracker.points[0]["i"], 1)

    async def test_with_end(self):
        org_tracker = Tracker("test")
        await org_tracker.new_points(
            [
                {"i": 0, "time": datetime(2017, 1, 1, 5, 55)},
                {"i": 1, "time": datetime(2017, 1, 1, 6, 5)},
            ]
        )
        org_tracker.completed.set_result(None)

        tracker = await cropped_tracker_start(org_tracker, {"end": datetime(2017, 1, 1, 6, 0)})
        await tracker.complete()
        self.assertEqual(len(tracker.points), 1)
        self.assertEqual(tracker.points[0]["i"], 0)


class TestReplayTracker(IsolatedAsyncioTestCase):
    async def test(self):
        org_tracker = Tracker("test")
        await org_tracker.new_points(
            [
                {"i": 0, "time": datetime(2017, 1, 1, 6, 0)},
                {"i": 1, "time": datetime(2017, 1, 1, 6, 5)},
            ]
        )
        org_tracker.completed.set_result(None)

        new_points_callback = AsyncMock()

        event_start = datetime(2017, 1, 1, 6, 0)
        now = datetime.now() + timedelta(seconds=0.01)
        replay_tracker = await start_replay_tracker(org_tracker, event_start, now)
        replay_tracker.new_points_observable.subscribe(new_points_callback)

        await replay_tracker.complete()

        self.assertEqual(new_points_callback.call_count, 2)
