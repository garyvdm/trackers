import base64
import copy
import datetime
import hashlib
import logging
import os

import msgpack
import yaml

from trackers.analyse import get_analyse_routes, start_analyse_tracker
from trackers.base import BlockedList
from trackers.dulwich_helpers import TreeWriter
from trackers.general import index_and_hash_tracker, start_replay_tracker

logger = logging.getLogger(__name__)


def load_events(app, settings):
    app['trackers.events'] = events = {}

    tree_reader = app['trackers.tree_reader']
    for name in tree_reader.tree_items('events'):
        events[name] = Event(app, name)


class Event(object):
    def __init__(self, app, name):
        self.name = name
        self.app = app
        self.tree_reader = tree_reader = app['trackers.tree_reader']
        self.base_path = os.path.join('events', name)
        self.ws_sessions = []
        self.rider_trackers = {}
        self.rider_trackers_blocked_list = {}

        config_bytes = tree_reader.get(os.path.join(self.base_path, 'data.yaml')).data

        self.config = yaml.load(config_bytes.decode())
        self.config_hash = base64.urlsafe_b64encode(hashlib.sha1(config_bytes).digest()).decode('ascii')

        routes_path = os.path.join(self.base_path, 'routes')
        if tree_reader.exists(routes_path):
            routes_bytes = tree_reader.get(routes_path).data
            self.routes = msgpack.loads(routes_bytes, encoding='utf8')
            self.routes_hash = base64.urlsafe_b64encode(hashlib.sha1(routes_bytes).digest()).decode('ascii')
        else:
            self.routes = []
            self.routes_hash = 'None'

    def save(self, message, author=None, tree_writer=None):
        if tree_writer is None:
            tree_writer = TreeWriter(self.app['trackers.data_repo'])
        config = copy.copy(self.config)
        config_text = yaml.dump(config)
        tree_writer.set_data(os.path.join(self.base_path, 'data.yaml'), config_text.encode())

        routes_path = os.path.join(self.base_path, 'routes')
        if self.routes:
            routes_bytes = msgpack.dumps(self.routes)
            tree_writer.set_data(routes_path, routes_bytes)
        else:
            tree_writer.remove(routes_path)
        tree_writer.commit(message, author=author)

    async def start_trackers(self, app):
        logger.info('Starting {}'.format(self.name))

        analyse = self.config.get('analyse', False)
        replay = self.config.get('replay', False)
        is_live = self.config.get('live', False)

        if analyse:
            analyse_routes = get_analyse_routes(self.routes)

        if replay:
            replay_start = datetime.datetime.now() + datetime.timedelta(seconds=2)
            event_start = self.config['event_start']

        for rider in self.config['riders']:
            if rider['tracker']:
                start_tracker = app['start_event_trackers'][rider['tracker']['type']]
                tracker = await start_tracker(app, self, rider['name'], rider['tracker'])
                if replay:
                    tracker = await start_replay_tracker(tracker, event_start, replay_start)
                if analyse:
                    tracker = await start_analyse_tracker(tracker, self, analyse_routes)
                tracker = await index_and_hash_tracker(tracker)
                self.rider_trackers[rider['name']] = tracker
                self.rider_trackers_blocked_list[rider['name']] = BlockedList.from_tracker(tracker, entire_block=not is_live)

    async def stop_and_complete_trackers(self):
        for tracker in self.rider_trackers.values():
            tracker.stop()
        for tracker in self.rider_trackers.values():
            try:
                await tracker.complete()
            except Exception:
                tracker.logger.exception('Unhandled error: ')
