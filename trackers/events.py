import os
import asyncio
from functools import partial

import yaml

import trackers.modules

def load_events(app, settings):
    app['trackers.events_data'] = events_data = {}
    app['trackers.tracker_tasks'] = []
    app['trackers.events_rider_trackers'] = {}

    with open(os.path.join(settings['data_path'], 'events.yaml')) as f:
        event_names = yaml.load(f)

    for event_name in event_names:
        with open(os.path.join(settings['data_path'], event_name, 'data.yaml')) as f:
            event_data = yaml.load(f)
        events_data[event_name] = event_data


async def start_event_trackers(app, settings, event_name):
    event_data = app['trackers.events_data'][event_name]
    event_rider_trackers = app['trackers.events_rider_trackers'][event_name] = {}

    for rider in event_data['riders']:
        tracker, task = await trackers.modules.start_event_trackers[rider['tracker']['type']](
            app, settings, event_name, event_data, rider['tracker'])
        app['trackers.tracker_tasks'].append(task)
        event_rider_trackers[rider['name']] = tracker
        task.add_done_callback(partial(tracker_task_callback, tracker))


def tracker_task_callback(tracker, task):
    try:
        task.result()
        tracker.logger.info('Tracker task complete')
    except asyncio.CancelledError:
        tracker.logger.info('Tracker task canceled')
    except Exception:
        tracker.logger.exception('Unhandled error in tracker task:')


def save_event(app, settings, event_name):
    app['trackers.events_data'][event_name]['data_version'] += 1
    with open(os.path.join(settings['data_path'], event_name, 'data.yaml'), 'w') as f:
        yaml.dump(app['trackers.events_data'][event_name], f)

