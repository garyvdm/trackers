import contextlib
import copy
import logging
import os

import msgpack
import yaml

from trackers.analyse import get_expanded_routes, start_analyse_tracker
from trackers.modules import start_event_trackers

logger = logging.getLogger(__name__)


def load_events(app, settings):
    app['trackers.events'] = events = {}

    with open(os.path.join(settings['data_path'], 'events.yaml')) as f:
        event_names = yaml.load(f)

    for event_name in event_names:
        events[event_name] = Event(settings, event_name)


class Event(object):
    def __init__(self, settings, name):
        self.settings = settings
        self.name = name
        self.base_path = os.path.join(settings['data_path'], self.name)
        self.ws_sessions = []
        self.rider_trackers = {}
        self.load()

    def load(self):
        with open(os.path.join(self.base_path, 'data.yaml')) as f:
            self.data = yaml.load(f)

        routes_path = os.path.join(self.base_path, 'routes')
        if os.path.exists(routes_path):
            with open(routes_path, 'rb') as f:
                self.routes = msgpack.load(f)
        else:
            self.routes = ()

    def save(self):
        data = copy.copy(self.data)
        data['data_version'] += 1
        with open(os.path.join(self.base_path, 'data.yaml'), 'w') as f:
            yaml.dump(data, f)

        routes_path = os.path.join(self.base_path, 'routes')
        if self.routes:
            with open(routes_path, 'wb') as f:
                msgpack.dump(self.routes, f)
        else:
            with contextlib.suppress(FileNotFoundError):
                os.remove(routes_path)

    async def start_trackers(self, app):
        logger.info('Starting {}'.format(self.name))

        analyse = self.data.get('analyse', False)

        if analyse:
            expanded_routes = get_expanded_routes(self.routes)

        for rider in self.data['riders']:
            if rider['tracker']:
                start_tracker = start_event_trackers[rider['tracker']['type']]
                tracker = await start_tracker(app, self, rider['name'], rider['tracker'])
                if analyse:
                    tracker = await start_analyse_tracker(tracker, self, expanded_routes)

                self.rider_trackers[rider['name']] = tracker
                # trackers.print_tracker(tracker)

    async def stop_trackers(self):
        for tracker in self.rider_trackers.values():
            await tracker.stop()
        for tracker in self.rider_trackers.values():
            await tracker.finish()
