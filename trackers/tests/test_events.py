from textwrap import dedent
from unittest import IsolatedAsyncioTestCase

import fixtures
import msgpack
from dulwich.repo import MemoryRepo

from trackers.base import Tracker
from trackers.dulwich_helpers import TreeWriter
from trackers.events import Event, load_events
from trackers.general import json_encode
from trackers.tests import get_test_app_and_settings


class TestEvents(IsolatedAsyncioTestCase, fixtures.TestWithFixtures):
    async def test_load_events(self):
        repo = MemoryRepo()
        writer = TreeWriter(repo)
        writer.set_data("events/test_event/data.yaml", "{}".encode())
        writer.commit("add test_event")

        app, settings = get_test_app_and_settings(repo)
        await load_events(app, writer)

        events = app["trackers.events"]
        self.assertEqual(len(events), 1)
        event = events["test_event"]
        self.assertEqual(event.name, "test_event")

    async def test_from_load(self):
        repo = MemoryRepo()
        writer = TreeWriter(repo)
        writer.set_data(
            "events/test_event/data.yaml",
            """
            title: Test event
        """.encode(),
        )
        writer.commit("add test_event")

        app, settings = get_test_app_and_settings(repo)
        event = await Event.load(app, "test_event", writer)
        self.assertEqual(event.config, {"title": "Test event"})
        self.assertEqual(event.routes, [])

    async def test_from_load_with_routes(self):
        repo = MemoryRepo()
        writer = TreeWriter(repo)

        self.assertFalse(writer.exists("events/test_event/routes"))

        writer.set_data("events/test_event/data.yaml", "{}".encode())
        writer.set_data("events/test_event/routes.yaml", "- {data_hash: abcd, name: foo}".encode())
        writer.set_data("events/test_event/routes_data/abcd", b"\x81\xa6points\x90")

        writer.commit("add test_event")

        app, settings = get_test_app_and_settings(repo)
        event = await Event.load(app, "test_event", writer)
        self.assertEqual(event.routes, [{"name": "foo", "points": []}])

    async def test_from_load_with_routes_old(self):
        repo = MemoryRepo()
        writer = TreeWriter(repo)
        writer.set_data("events/test_event/data.yaml", "{}".encode())
        writer.set_data("events/test_event/routes", b"\x91\x81\xa6points\x90")
        writer.commit("add test_event")

        app, settings = get_test_app_and_settings(repo)
        event = await Event.load(app, "test_event", writer)
        self.assertEqual(event.routes, [{"points": []}])

    async def test_save(self):
        repo = MemoryRepo()
        writer = TreeWriter(repo)

        app, settings = get_test_app_and_settings(repo)
        event = Event(
            app,
            "test_event",
            {"title": "Test event"},
            [{"title": "foobar", "points": []}],
        )
        await event.save("save test_event", tree_writer=writer, save_routes=True)

        self.assertEqual(
            writer.get("events/test_event/data.yaml").data.decode(),
            "title: Test event\n",
        )
        self.assertEqual(
            writer.get("events/test_event/routes.yaml").data.decode(),
            "- title: foobar\n  data_hash: KhGSreKJpp4AwDUWjtATeuAYLms=\n",
        )
        self.assertEqual(
            writer.get("events/test_event/routes_data/KhGSreKJpp4AwDUWjtATeuAYLms=").data,
            b"\x81\xa6points\x90",
        )

    async def test_save_no_routes(self):
        repo = MemoryRepo()
        writer = TreeWriter(repo)
        writer.set_data("events/test_event/data.yaml", "{}".encode())
        writer.set_data("events/test_event/routes", b"\x91\x81\xa6points\x90")
        writer.commit("add test_event")

        app, settings = get_test_app_and_settings(repo)
        event = await Event.load(app, "test_event", writer)
        event.routes.pop()
        await event.save("save test_event", tree_writer=writer, save_routes=True)

        self.assertFalse(writer.exists("events/test_event/routes"))

    async def test_save_no_routes_before_and_after(self):
        repo = MemoryRepo()
        writer = TreeWriter(repo)

        app, settings = get_test_app_and_settings(repo)
        event = Event(app, "test_event", {"title": "Test event"}, [])
        await event.save("save test_event", tree_writer=writer, save_routes=True)

        self.assertFalse(writer.exists("events/test_event/routes"))


class TestEventWithMockTracker(fixtures.TestWithFixtures):
    def do_setup(self, data):
        repo = MemoryRepo()
        cache_dir = self.useFixture(fixtures.TempDir())
        writer = TreeWriter(repo)
        writer.set_data("events/test_event/data.yaml", dedent(data).encode())
        writer.commit("add test_event")

        async def start_mock_event_tracker(app, event, rider_name, tracker_data, start, end):
            tracker = Tracker("mock_tracker")
            tracker.completed.set_result(None)
            return tracker

        app, settings = get_test_app_and_settings(repo)
        settings["cache_path"] = cache_dir.path
        app["start_event_trackers"] = {
            "mock": start_mock_event_tracker,
        }
        return app, settings, writer


class TestEventsStartStopTracker(IsolatedAsyncioTestCase, TestEventWithMockTracker):
    async def test_mock(self):
        app, settings, writer = self.do_setup(
            """
            tracker_end: 2019-01-01 00:00:00
            riders:
              - name: foo
                tracker: {type: mock}
        """
        )

        event = await Event.load(app, "test_event", writer)
        await event.start_trackers(analyse=False)

        rider_objects = event.riders_objects["foo"]
        self.assertEqual(rider_objects.source_trackers[0].name, "mock_tracker")
        self.assertEqual(rider_objects.tracker.name, "indexed_and_hashed.combined.foo")
        self.assertIsNotNone(rider_objects.blocked_list)

        await event.stop_and_complete_trackers()

    async def test_with_analyse(self):
        app, settings, writer = self.do_setup(
            """
            tracker_end: 2019-01-01 00:00:00
            analyse: True
            riders:
              - name: foo
                tracker: {type: mock}
        """
        )

        event = await Event.load(app, "test_event", writer)
        await event.start_trackers()

        self.assertEqual(
            event.riders_objects["foo"].tracker.name,
            "indexed_and_hashed.analysed.combined.foo",
        )

        await event.stop_and_complete_trackers()

    async def test_with_replay(self):
        app, settings, writer = self.do_setup(
            """
            event_start: 2017-07-01 05:00:00
            tracker_end: 2019-01-01 00:00:00
            replay: True
            riders:
              - name: foo
                tracker: {type: mock}
        """
        )

        event = await Event.load(app, "test_event", writer)
        await event.start_trackers(analyse=False)

        self.assertEqual(
            event.riders_objects["foo"].tracker.name,
            "indexed_and_hashed.replay.combined.foo",
        )

        await event.stop_and_complete_trackers()

    async def test_no_tracker(self):
        app, settings, writer = self.do_setup(
            """
            tracker_end: 2019-01-01 00:00:00
            riders:
              - name: foo
                tracker: null
        """
        )

        event = await Event.load(app, "test_event", writer)
        await event.start_trackers()

        self.assertEqual(len(event.riders_objects["foo"].source_trackers), 0)

        await event.stop_and_complete_trackers()

    async def test_static(self):
        data = """
            tracker_end: 2019-01-01 00:00:00
            riders:
              - name: foo
            static_analyse: True
        """
        repo = MemoryRepo()
        cache_dir = self.useFixture(fixtures.TempDir())
        writer = TreeWriter(repo)
        writer.set_data("events/test_event/data.yaml", dedent(data).encode())
        writer.set_data(
            "events/test_event/static/foo/source",
            msgpack.dumps([{"foo": "bar"}], default=json_encode),
        )
        writer.set_data(
            "events/test_event/static/foo/analyse",
            msgpack.dumps([{"foo": "bar", "speed": 1}], default=json_encode),
        )
        writer.set_data(
            "events/test_event/static/foo/off_route",
            msgpack.dumps([], default=json_encode),
        )

        writer.commit("add test_event")

        app, settings = get_test_app_and_settings(repo)
        settings["cache_path"] = cache_dir.path

        event = await Event.load(app, "test_event", writer)
        await event.start_trackers()

        rider_objects = event.riders_objects["foo"]
        self.assertEqual(rider_objects.source_trackers[0].points, [{"foo": "bar"}])
        self.assertEqual(rider_objects.analyse_tracker.points, [{"foo": "bar", "speed": 1}])

        await event.stop_and_complete_trackers()
