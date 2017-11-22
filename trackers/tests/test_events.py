import asyncio
from textwrap import dedent

import asynctest
import fixtures

from trackers.base import Tracker
from trackers.dulwich_helpers import TreeWriter
from trackers.events import Event, load_events
from trackers.tests import get_test_app_and_settings, TempRepoFixture


class TestEvents(fixtures.TestWithFixtures):

    def test_load_events(self):
        repo = self.useFixture(TempRepoFixture()).repo
        writer = TreeWriter(repo)
        writer.set_data('events/test_event/data.yaml', '{}'.encode())
        writer.commit('add test_event')

        app, settings = get_test_app_and_settings(repo, writer)
        load_events(app, settings)

        events = app['trackers.events']
        self.assertEqual(len(events), 1)
        event = events['test_event']
        self.assertEqual(event.name, 'test_event')

    def test_init(self):
        repo = self.useFixture(TempRepoFixture()).repo
        writer = TreeWriter(repo)
        writer.set_data('events/test_event/data.yaml', '''
            title: Test event
        '''.encode())
        writer.commit('add test_event')

        app, settings = get_test_app_and_settings(repo, writer)
        event = Event(app, 'test_event')
        self.assertEqual(event.config, {'title': 'Test event'})
        self.assertEqual(event.routes, [])

    def test_init_with_routes(self):
        repo = self.useFixture(TempRepoFixture()).repo
        writer = TreeWriter(repo)
        writer.set_data('events/test_event/data.yaml', '{}'.encode())
        writer.set_data('events/test_event/routes', b'\x91\x81\xa6points\x90')
        writer.commit('add test_event')

        app, settings = get_test_app_and_settings(repo, writer)
        event = Event(app, 'test_event')
        self.assertEqual(event.routes, [{'points': []}])

    def test_save(self):
        repo = self.useFixture(TempRepoFixture()).repo
        writer = TreeWriter(repo)
        writer.set_data('events/test_event/data.yaml', '{}'.encode())
        writer.commit('add test_event')

        app, settings = get_test_app_and_settings(repo, writer)
        event = Event(app, 'test_event')
        event.config['title'] = 'Test event'
        event.routes.append({'points': []})
        event.save('save test_event', tree_writer=writer)

        self.assertEqual(writer.get('events/test_event/data.yaml').data.decode(), '{title: Test event}\n')
        self.assertEqual(writer.get('events/test_event/routes').data, b'\x91\x81\xa6points\x90')

    def test_save_no_routes(self):
        repo = self.useFixture(TempRepoFixture()).repo
        writer = TreeWriter(repo)
        writer.set_data('events/test_event/data.yaml', '{}'.encode())
        writer.set_data('events/test_event/routes', b'\x91\x81\xa6points\x90')
        writer.commit('add test_event')

        app, settings = get_test_app_and_settings(repo, writer)
        event = Event(app, 'test_event')
        event.routes.pop()
        event.save('save test_event', tree_writer=writer)

        self.assertFalse(writer.exists('events/test_event/routes'))

    def test_save_no_routes_before_and_after(self):
        repo = self.useFixture(TempRepoFixture()).repo
        writer = TreeWriter(repo)
        writer.set_data('events/test_event/data.yaml', '{}'.encode())
        writer.commit('add test_event')

        app, settings = get_test_app_and_settings(repo, writer)
        event = Event(app, 'test_event')
        event.save('save test_event', tree_writer=writer)

        self.assertFalse(writer.exists('events/test_event/routes'))


class TestEventWithMockTracker(fixtures.TestWithFixtures):

    def do_setup(self, data):
        repo = self.useFixture(TempRepoFixture()).repo
        writer = TreeWriter(repo)
        writer.set_data('events/test_event/data.yaml', dedent(data).encode())
        writer.commit('add test_event')

        async def start_mock_event_tracker(app, event, rider_name, tracker_data):
            tracker = Tracker('mock_tracker')
            tracker.completed = asyncio.Future()
            tracker.completed.set_result(None)
            return tracker

        app, settings = get_test_app_and_settings(repo, writer)
        app['start_event_trackers'] = {
            'mock': start_mock_event_tracker
        }
        return app, settings, writer


class TestEventsStartStopTracker(asynctest.TestCase, TestEventWithMockTracker):

    async def test_mock(self):
        app, settings, writer = self.do_setup('''
            riders:
              - name: foo
                tracker: {type: mock}
        ''')

        event = Event(app, 'test_event')
        await event.start_trackers(app)

        self.assertEqual(event.rider_trackers['foo'].name, 'indexed_and_hashed.mock_tracker')
        self.assertIn('foo', event.rider_trackers_blocked_list)

        await event.stop_and_complete_trackers()

    async def test_with_analyse(self):
        app, settings, writer = self.do_setup('''
            analyse: True
            riders:
              - name: foo
                tracker: {type: mock}
        ''')

        event = Event(app, 'test_event')
        await event.start_trackers(app)

        self.assertEqual(event.rider_trackers['foo'].name, 'indexed_and_hashed.analysed.mock_tracker')

        await event.stop_and_complete_trackers()

    async def test_with_replay(self):
        app, settings, writer = self.do_setup('''
            event_start: 2017-07-01 05:00:00
            replay: True
            riders:
              - name: foo
                tracker: {type: mock}
        ''')

        event = Event(app, 'test_event')
        await event.start_trackers(app)

        self.assertEqual(event.rider_trackers['foo'].name, 'indexed_and_hashed.replay.mock_tracker')

        await event.stop_and_complete_trackers()

    async def test_no_tracker(self):
        app, settings, writer = self.do_setup('''
            riders:
              - name: foo
                tracker: null
        ''')

        event = Event(app, 'test_event')
        await event.start_trackers(app)

        self.assertEqual(len(event.rider_trackers), 0)

        await event.stop_and_complete_trackers()
