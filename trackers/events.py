import base64
import datetime
import hashlib
import logging
import os

import msgpack
import yaml

from trackers.analyse import AnalyseTracker, get_analyse_routes
from trackers.base import BlockedList
from trackers.dulwich_helpers import TreeReader, TreeWriter
from trackers.general import index_and_hash_tracker, start_replay_tracker

logger = logging.getLogger(__name__)


def load_events(app, settings):
    app['trackers.events'] = events = {}

    try:
        tree_reader = TreeReader(app['trackers.data_repo'])
    except KeyError:
        pass
    else:
        for name in tree_reader.tree_items('events'):
            events[name] = Event.load(app, name, tree_reader)


def hash_bytes(b):
    return base64.urlsafe_b64encode(hashlib.sha1(b).digest()).decode('ascii')


class Event(object):
    def __init__(self, app, name, config, routes, config_hash=None, routes_hash=None):
        self.name = name
        self.app = app
        self.ws_sessions = []
        self.rider_trackers = {}
        self.rider_trackers_blocked_list = {}

        self.config = config
        if not config_hash:
            config_hash = hash_bytes(yaml.dump(self.config).encode())
        self.config_hash = config_hash

        self.routes = routes
        if not routes_hash:
            routes_hash = hash_bytes(msgpack.dumps(self.routes))
        self.routes_hash = routes_hash

        self.trackers_started = False

    @classmethod
    def load(cls, app, name, tree_reader):
        config_bytes = tree_reader.get(os.path.join('events', name, 'data.yaml')).data
        config = yaml.load(config_bytes.decode())
        config_hash = hash_bytes(config_bytes)

        routes_path = os.path.join('events', name, 'routes')
        if tree_reader.exists(routes_path):
            routes_bytes = tree_reader.get(routes_path).data
            routes = msgpack.loads(routes_bytes, encoding='utf8')
            routes_hash = hash_bytes(routes_bytes)
        else:
            routes = []
            routes_hash = None
        return cls(app, name, config, routes, config_hash=config_hash, routes_hash=routes_hash)

    def save(self, message, author=None, tree_writer=None):
        if tree_writer is None:
            tree_writer = TreeWriter(self.app['trackers.data_repo'])
        config_text = yaml.dump(self.config, default_flow_style=False)
        tree_writer.set_data(os.path.join('events', self.name, 'data.yaml'), config_text.encode())

        routes_path = os.path.join('events', self.name, 'routes')
        if self.routes:
            routes_bytes = msgpack.dumps(self.routes)
            tree_writer.set_data(routes_path, routes_bytes)
        else:
            if tree_writer.exists(routes_path):
                tree_writer.remove(routes_path)
        tree_writer.commit(message, author=author)

    async def start_trackers(self, app):
        if not self.trackers_started:
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
                        tracker = await AnalyseTracker.start(tracker, self, analyse_routes)
                    tracker = await index_and_hash_tracker(tracker)
                    self.rider_trackers[rider['name']] = tracker
                    self.rider_trackers_blocked_list[rider['name']] = BlockedList.from_tracker(tracker, entire_block=not is_live)

            self.trackers_started = True

    async def stop_and_complete_trackers(self):
        for tracker in self.rider_trackers.values():
            tracker.stop()
        for tracker in self.rider_trackers.values():
            try:
                await tracker.complete()
            except Exception:
                tracker.logger.exception('Unhandled error: ')
        self.trackers_started = False
