import argparse
import asyncio
import concurrent
import contextlib
import copy
import logging.config
import os
import signal
import sys
import colorsys
from functools import partial

import yaml

import trackers.modules
import trackers.events


defaults_yaml = """
    data_path: data

    logging:
        version: 1
        handlers:
            console:
                formatter: generic
                stream  : ext://sys.stdout
                class : logging.StreamHandler
                level: NOTSET

        formatters:
            generic:
                format: '%(levelname)-5.5s [%(name)s] %(message)s'
        root:
            level: NOTSET
            handlers: [console, ]

        loggers:
            trackers:
                 level: INFO
                 qualname: trackers

            aiohttp:
                 level: INFO
                 qualname: aiohttp

            asyncio:
                 level: INFO
                 qualname: asyncio
"""


def get_base_argparser(*args, **kwargs):
    parser = argparse.ArgumentParser()
    parser.add_argument('settings_file', action='store', nargs='?', default='/etc/route_view.yaml',
                        help='File to load settings from.')
    parser.add_argument('--google-api-key', action='store',
                        help='Google api key. ')
    parser.add_argument('--debug', action='store_true',
                        help='Set logging level to DEBUG.')
    return parser


def get_combined_settings(specific_defaults_yaml=None, args=None):
    # TODO logic to combine logging config

    defaults = yaml.load(defaults_yaml)
    settings = copy.deepcopy(defaults)

    if specific_defaults_yaml:
        specific_defaults = yaml.load(specific_defaults_yaml)
        settings.update(specific_defaults)

    if 'TRACKERS_SETTINGS_FILE' in os.environ:
        try:
            with open(os.environ['TRACKERS_SETTINGS_FILE']) as f:
                settings_from_file = yaml.load(f)
        except FileNotFoundError:
            settings_from_file = {}
        settings.update(settings_from_file)

    if args:
        try:
            with open(args.settings_file) as f:
                settings_from_file = yaml.load(f)
        except FileNotFoundError:
            settings_from_file = {}
        settings.update(settings_from_file)

    logging.config.dictConfig(settings['logging'])
    if args and args.debug:
        logging.getLogger('cfs').setLevel(logging.DEBUG)

    if sys.stdout.isatty() and settings['logging'] != defaults['logging']:
        # Reapply the default logging settings
        logging.config.dictConfig(defaults['logging'])

    settings_dump = yaml.dump(settings)
    logging.getLogger('trackers').debug('Combined Settings: \n{}'.format(settings_dump))

    return settings


def convert_to_static():
    parser = get_base_argparser(description="Convert live trackers to static data.")
    parser.add_argument('event', action='store')
    args = parser.parse_args()
    settings = get_combined_settings(args=args)
    with contextlib.closing(asyncio.get_event_loop()) as loop:
        loop.run_until_complete(convert_to_static_async(settings, args.event))

async def convert_to_static_async(settings, event_name):
    app = {}
    async with await trackers.modules.config_modules(app, settings):
        trackers.events.load_events(app, settings)
        await trackers.events.start_event_trackers(app, settings, event_name)

        for task in app['trackers.tracker_tasks']:
            await task

        event_data = app['trackers.events_data'][event_name]
        rider_trackers = app['trackers.events_rider_trackers'][event_name]
        for rider in event_data['riders']:
            rider_name = rider['name']
            tracker = rider_trackers[rider_name]
            with open(os.path.join(os.path.join(settings['data_path'], event_name, rider_name)), 'w') as f:
                yaml.dump(tracker.points, f)
            rider['tracker'] = {'type': 'static', 'name': rider_name}
        trackers.events.save_event(app, settings, event_name)


def assign_rider_colors():
    parser = get_base_argparser(description="Assigns unique colors to riders")
    parser.add_argument('event', action='store')
    args = parser.parse_args()
    settings = get_combined_settings(args=args)
    app = {}
    event_name = args.event
    trackers.events.load_events(app, settings)
    event_data = app['trackers.events_data'][event_name]
    num_riders = len(event_data['riders'])
    for i, rider in enumerate(event_data['riders']):
        rider['color'] = 'hsl({}, 100%, 50%)'.format(round(i * 360 / num_riders))
    trackers.events.save_event(app, settings, event_name)




