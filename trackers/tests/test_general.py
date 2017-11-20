import datetime

import asynctest
import fixtures

from trackers.base import Tracker
from trackers.dulwich_helpers import TreeWriter
from trackers.events import Event
from trackers.general import (
    cropped_tracker_start,
    static_start_event_tracker,
)
from trackers.tests import get_test_app_and_settings, TempRepoFixture


class TestStatic(asynctest.TestCase, fixtures.TestWithFixtures):

    async def test_start_msgpack(self):
        repo = self.useFixture(TempRepoFixture()).repo
        writer = TreeWriter(repo)
        writer.set_data('events/test_event/data.yaml', '{}'.encode())
        writer.set_data('events/test_event/test_rider', b'\x91\x82\xa4time\xcbA\xd6\x1a\n\x98\x00\x00\x00\xa3bar\xa3foo')
        writer.commit('add test_event')

        app, settings = get_test_app_and_settings(repo, writer)
        event = Event(app, 'test_event')
        tracker = await static_start_event_tracker(app, event, 'Test rider', {'name': 'test_rider', 'format': 'msgpack'})
        self.assertEqual(len(tracker.points), 1)
        self.assertEqual(tracker.points[0], {
            'time': datetime.datetime(2017, 1, 1),
            'bar': 'foo',
        })

    async def test_start_json(self):
        repo = self.useFixture(TempRepoFixture()).repo
        writer = TreeWriter(repo)
        writer.set_data('events/test_event/data.yaml', '{}'.encode())
        writer.set_data('events/test_event/test_rider', '[]'.encode())
        writer.commit('add test_event')

        app, settings = get_test_app_and_settings(repo, writer)
        event = Event(app, 'test_event')
        tracker = await static_start_event_tracker(app, event, 'Test rider', {'name': 'test_rider', 'format': 'json'})
        self.assertEqual(tracker.points, [])


class TestCropped(asynctest.TestCase, fixtures.TestWithFixtures):

    async def test_with_start(self):
        org_tracker = Tracker('test')
        await org_tracker.new_points([
            {'i': 0, 'time': datetime.datetime(2017, 1, 1, 5, 55)},
            {'i': 1, 'time': datetime.datetime(2017, 1, 1, 6, 5)},
        ])

        tracker = await cropped_tracker_start(org_tracker, {'start': datetime.datetime(2017, 1, 1, 6, 0)})
        self.assertEqual(len(tracker.points), 1)
        self.assertEqual(tracker.points[0]['i'], 1)

    async def test_with_end(self):
        org_tracker = Tracker('test')
        await org_tracker.new_points([
            {'i': 0, 'time': datetime.datetime(2017, 1, 1, 5, 55)},
            {'i': 1, 'time': datetime.datetime(2017, 1, 1, 6, 5)},
        ])

        tracker = await cropped_tracker_start(org_tracker, {'end': datetime.datetime(2017, 1, 1, 6, 0)})
        self.assertEqual(len(tracker.points), 1)
        self.assertEqual(tracker.points[0]['i'], 0)
