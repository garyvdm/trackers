import argparse
import asyncio
import contextlib
import copy
import datetime
import json
import logging.config
import os
import sys
import xml.etree.ElementTree as xml

import yaml

import trackers.events
import trackers.modules

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
            web_app:
                 level: INFO
                 qualname: web_app
            events:
                 level: INFO
                 qualname: events
            aiohttp:
                 level: INFO
                 qualname: aiohttp
            asyncio:
                 level: INFO
                 qualname: asyncio
"""


def get_base_argparser(*args, **kwargs):
    parser = argparse.ArgumentParser()
    parser.add_argument('settings_file', action='store', nargs='?', default='/etc/trackers.yaml',
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
        logging.getLogger('trackers').setLevel(logging.DEBUG)
        logging.getLogger('web_app').setLevel(logging.DEBUG)
        logging.getLogger('asyncio').setLevel(logging.DEBUG)

    settings['debug'] = args and args.debug

    if sys.stdout.isatty() and settings['logging'] != defaults['logging']:
        # Reapply the default logging settings
        logging.config.dictConfig(defaults['logging'])

    settings_dump = yaml.dump(settings)
    logging.getLogger('trackers').debug('Combined Settings: \n{}'.format(settings_dump))

    return settings


def convert_to_static():
    parser = get_base_argparser(description="Convert live trackers to static data.")
    parser.add_argument('event', action='store')
    parser.add_argument('--dry-run', '-d', action='store_true')

    args = parser.parse_args()
    settings = get_combined_settings(args=args)
    with contextlib.closing(asyncio.get_event_loop()) as loop:
        loop.set_debug(settings['debug'])
        loop.run_until_complete(convert_to_static_async(settings, args.event, args.dry_run))


async def convert_to_static_async(settings, event_name, dry_run):
    app = {}
    async with await trackers.modules.config_modules(app, settings):
        trackers.events.load_events(app, settings)
        await trackers.events.start_event_trackers(app, settings, event_name)

        event_data = app['trackers.events_data'][event_name]
        rider_trackers = app['trackers.events_rider_trackers'][event_name]
        for rider in event_data['riders']:
            rider_name = rider['name']
            tracker = rider_trackers.get(rider_name)
            if tracker:
                await tracker.finish()
                with open(os.path.join(os.path.join(settings['data_path'], event_name, rider_name)), 'w') as f:
                    json.dump(tracker.points, f, default=json_encode)
                rider['tracker'] = {'type': 'static', 'name': rider_name}
        if not dry_run:
            trackers.events.save_event(app, settings, event_name)


def json_encode(obj):
    if isinstance(obj, datetime.datetime):
        return obj.timestamp()


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
        hue = round(((i * 360 / num_riders) + (180 * (i % 2))) % 360)
        print(hue)
        rider['color'] = 'hsl({}, 100%, 50%)'.format(hue)
        rider['color_marker'] = 'hsl({}, 100%, 60%)'.format(hue)
    trackers.events.save_event(app, settings, event_name)


def add_gpx_to_event_routes():
    parser = get_base_argparser(description="Add a gpx file to the routes for of an event.")
    parser.add_argument('event', action='store')
    parser.add_argument('gpx_file', action='store')
    args = parser.parse_args()
    settings = get_combined_settings(args=args)
    app = {}
    event_name = args.event
    trackers.events.load_events(app, settings)
    event_data = app['trackers.events_data'][event_name]

    with open(args.gpx_file) as f:
        gpx_text = f.read()

    xml_doc = xml.fromstring(gpx_text)

    gpx_ns = {
        '1.0': {'gpx': 'http://www.topografix.com/GPX/1/0', },
        '1.1': {'gpx': 'http://www.topografix.com/GPX/1/1', },
    }[xml_doc.attrib['version']]

    trkpts = xml_doc.findall('./gpx:trk/gpx:trkseg/gpx:trkpt', gpx_ns)
    points = [[float(trkpt.attrib['lat']), float(trkpt.attrib['lon'])] for trkpt in trkpts]
    event_data.setdefault('routes', []).append(points)
    trackers.events.save_event(app, settings, event_name)
