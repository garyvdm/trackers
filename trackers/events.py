import os
import asyncio

import yaml

import trackers
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
        if rider['tracker']:
            tracker = await trackers.modules.start_event_trackers[rider['tracker']['type']](
                app, settings, event_name, event_data, rider['tracker'])
            tracker = await trackers.start_analyse_tracker(tracker)
            # print(rider['name'])
            # import pprint
            # pprint.pprint(tracker.points)
            event_rider_trackers[rider['name']] = tracker
            # trackers.print_tracker(tracker)


async def stop_event_trackers(app, event_name):
    event_rider_trackers = app['trackers.events_rider_trackers'][event_name]
    for tracker in event_rider_trackers.values():
        await tracker.stop()


def save_event(app, settings, event_name):
    app['trackers.events_data'][event_name]['data_version'] += 1
    with open(os.path.join(settings['data_path'], event_name, 'data.yaml'), 'w') as f:
        yaml.dump(app['trackers.events_data'][event_name], f)


