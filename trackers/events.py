import base64
import contextlib
import copy
import datetime
import hashlib
import logging
import os

import msgpack
import yaml

from trackers.analyse import get_expanded_routes, start_analyse_tracker
from trackers.base import BlockedList
from trackers.general import index_and_hash_tracker, start_replay_tracker
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
        self.rider_trackers_blocked_list = {}

        with open(os.path.join(self.base_path, 'data.yaml'), 'rb') as f:
            config_bytes = f.read()

        self.config = yaml.load(config_bytes.decode())
        self.config_hash = base64.urlsafe_b64encode(hashlib.sha1(config_bytes).digest()).decode('ascii')

        routes_path = os.path.join(self.base_path, 'routes')
        if os.path.exists(routes_path):
            with open(routes_path, 'rb') as f:
                routes_bytes = f.read()
            self.routes = msgpack.loads(routes_bytes)
        else:
            self.routes = ()
            routes_bytes = b''
        self.routes_hash = base64.urlsafe_b64encode(hashlib.sha1(routes_bytes).digest()).decode('ascii')

    def save(self):
        config = copy.copy(self.config)
        with open(os.path.join(self.base_path, 'data.yaml'), 'w') as f:
            yaml.dump(config, f)

        routes_path = os.path.join(self.base_path, 'routes')
        if self.routes:
            with open(routes_path, 'wb') as f:
                msgpack.dump(self.routes, f)
        else:
            with contextlib.suppress(FileNotFoundError):
                os.remove(routes_path)

    async def start_trackers(self, app):
        logger.info('Starting {}'.format(self.name))

        analyse = self.config.get('analyse', False)
        replay = self.config.get('replay', False)
        is_live = self.config.get('live', False)

        if analyse:
            expanded_routes = get_expanded_routes(self.routes)

        if replay:
            replay_start = datetime.datetime.now() + datetime.timedelta(seconds=2)
            event_start = self.config['start']

        for rider in self.config['riders']:
            if rider['tracker']:
                start_tracker = start_event_trackers[rider['tracker']['type']]
                tracker = await start_tracker(app, self, rider['name'], rider['tracker'])
                if replay:
                    tracker = await start_replay_tracker(tracker, event_start, replay_start)
                if analyse:
                    tracker = await start_analyse_tracker(tracker, self, expanded_routes)
                tracker = await index_and_hash_tracker(tracker)
                self.rider_trackers[rider['name']] = tracker
                self.rider_trackers_blocked_list[rider['name']] = BlockedList.from_tracker(tracker, entire_block=not is_live)

    async def stop_trackers(self):
        for tracker in self.rider_trackers.values():
            await tracker.stop()
        for tracker in self.rider_trackers.values():
            await tracker.finish()
