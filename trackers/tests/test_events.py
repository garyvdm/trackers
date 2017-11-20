import fixtures

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
