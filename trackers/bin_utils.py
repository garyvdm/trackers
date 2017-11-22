import argparse
import asyncio
import contextlib
import copy
import logging.config
import os
import sys
from functools import partial, wraps

import aiohttp
import dulwich.repo
import msgpack
import polyline
import yaml


import trackers.events
import trackers.modules
from trackers.analyse import (
    distance,
    find_closest_point_pair_route,
    get_analyse_route,
    get_equal_spaced_points,
    Point,
    ramer_douglas_peucker,
    route_with_distance_and_index,
)
from trackers.async_exit_stack import AsyncExitStack
from trackers.dulwich_helpers import TreeReader, TreeWriter
from trackers.general import json_dumps, json_encode

defaults_yaml = """
    data_path: data
    cache_path: cache

    logging:
        version: 1
        disable_existing_loggers: false
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
            level: INFO
            handlers: [console, ]

"""


def get_base_argparser(*args, **kwargs):
    parser = argparse.ArgumentParser()
    parser.add_argument('settings_file', action='store', nargs='?',
                        default='/etc/trackers.yaml',
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

    # settings_dump = yaml.dump(settings)
    # logging.getLogger('trackers').debug('Combined Settings: \n{}'.format(settings_dump))

    return settings


async def app_setup(app, settings):
    stack = AsyncExitStack()

    await stack.enter_context(app_setup_basic(app, settings))
    await stack.enter_context(await trackers.modules.config_modules(app, settings))

    return stack


def app_setup_basic(app, settings):
    app['trackers.settings'] = settings
    app['trackers.data_repo'] = repo = dulwich.repo.Repo(settings['data_path'])
    app['trackers.tree_reader'] = TreeReader(app['trackers.data_repo'])
    return repo


def async_command(get_parser_func):
    def async_command_decorator(func):
        async def run_with_app(settings, args):
            app = {}
            async with await app_setup(app, settings):
                await func(app, settings, args)

        @wraps(func)
        def async_command_wrapper():
            parser = get_parser_func()
            args = parser.parse_args()
            settings = get_combined_settings(args=args)
            with contextlib.closing(asyncio.get_event_loop()) as loop:
                loop.set_debug(settings['debug'])
                loop.run_until_complete(run_with_app(settings, args))

        return async_command_wrapper
    return async_command_decorator


def event_command_parser(*args, **kwargs):
    parser = get_base_argparser(*args, **kwargs)
    parser.add_argument('event_name', action='store')


def convert_to_static_parser():
    parser = get_base_argparser(description="Convert live trackers to static data.")
    parser.add_argument('event_name', action='store')
    parser.add_argument('--dry-run', '-d', action='store_true')
    parser.add_argument('--format', '-f', choices=['msgpack', 'json'], default='msgpack')
    return parser


@async_command(convert_to_static_parser)
async def convert_to_static(app, settings, args):
    event = trackers.events.Event(app, args.event_name)
    await event.start_trackers(app)
    tree_writer = TreeWriter(app['trackers.data_repo'])

    for rider in event.config['riders']:
        rider_name = rider['name']
        tracker = event.rider_trackers.get(rider_name)
        if tracker:
            await tracker.complete()
            path = os.path.join(event.base_path, rider_name)
            if args.format == 'msgpack':
                tree_writer.set_data(path, msgpack.dumps(tracker.points, default=json_encode))

            if args.format == 'json':
                tree_writer.set_data(path, json_dumps(tracker.points).encode())

            rider['tracker'] = {'type': 'static', 'name': rider_name, 'format': args.format}
    event.config['analyse'] = False
    event.config['live'] = False
    if not args.dry_run:
        event.save('convert_to_static: {}'.format(args.event_name), tree_writer=tree_writer)


@async_command(partial(event_command_parser, description="Assigns unique colors to riders"))
async def assign_rider_colors(app, settings, args):
    event = trackers.events.Event(app, args.event_name)
    num_riders = len(event.config['riders'])
    for i, rider in enumerate(event.config['riders']):
        hue = round(((i * 360 / num_riders) + (180 * (i % 2))) % 360)
        rider['color'] = 'hsl({}, 100%, 50%)'.format(hue)
        rider['color_marker'] = 'hsl({}, 100%, 60%)'.format(hue)
    event.save('assign_rider_colors: {}'.format(args.event_name))


def add_gpx_to_event_routes_parser():
    parser = get_base_argparser(description="Add a gpx file to the routes for of an event.")
    parser.add_argument('event_name', action='store')
    parser.add_argument('gpx-file', action='store')
    parser.add_argument('--no-elevation', action='store_true')
    return parser


@async_command(add_gpx_to_event_routes_parser)
async def add_gpx_to_event_routes(app, settings, args):
    import xml.etree.ElementTree as xml

    with open(args.gpx_file) as f:
        gpx_text = f.read()

    xml_doc = xml.fromstring(gpx_text)

    gpx_ns = {
        '1.0': {'gpx': 'http://www.topografix.com/GPX/1/0', },
        '1.1': {'gpx': 'http://www.topografix.com/GPX/1/1', },
    }[xml_doc.attrib['version']]

    trkpts = xml_doc.findall('./gpx:trk/gpx:trkseg/gpx:trkpt', gpx_ns)
    points = [[float(trkpt.attrib['lat']), float(trkpt.attrib['lon'])] for trkpt in trkpts]

    event = trackers.events.Event(app, args.event_name)
    route = {'original_points': points}
    await process_route(settings, route, get_elevation=not args.no_elevation)
    event.routes.append(route)
    process_secondary_route_details(event.routes)
    # TODO - add gpx file to repo
    event.save('add_gpx_to_event_routes: {} - {}'.format(args.event_name, args.gpx_file))


@async_command(partial(event_command_parser, description="Open and save event. Side effect is convert to new formats."))
async def reformat_event(app, settings, args):
    event = trackers.events.Event(app, args.event_name)
    event.save('reformat_event: {}'.format(args.event_name))


@async_command(partial(event_command_parser, description="Reprocess event routes."))
async def process_event_routes(app, settings, args):
    event = trackers.events.Event(app, args.event_name)
    for route in event.routes:
        await process_route(settings, route)
    process_secondary_route_details(event.routes)
    event.save('process_event_routes: {}'.format(args.event_name))


def process_secondary_route_details(routes):
    main_route = get_analyse_route(routes[0])
    for i, route in enumerate(routes):
        if i > 0:
            route['main'] = False
            route_points = route_with_distance_and_index(route['points'])
            start_closest = find_closest_point_pair_route(main_route, route_points[0], 2000)
            prev_point = start_closest.point_pair[0]
            route['prev_point'] = prev_point.index
            route['start_distance'] = start_distance = prev_point.distance + distance(prev_point, route_points[0])
            end_closest = find_closest_point_pair_route(main_route, route_points[-1], 2000)
            next_point = end_closest.point_pair[1]
            route['next_point'] = next_point.index
            route['end_distance'] = end_distance = next_point.distance - distance(next_point, route_points[-1])
            route['dist_factor'] = (end_distance - start_distance) / route_points[-1].distance
        else:
            route['main'] = True


async def process_route(settings, route, get_elevation=True):
    original_points = route['original_points']
    filtered_points = (point for last_point, point in zip([None] + original_points[:-1], original_points) if point != last_point)
    point_points = [Point(*point) for point in filtered_points]
    simplified_points = ramer_douglas_peucker(point_points, 2)
    route['points'] = [(point.lat, point.lng) for point in simplified_points]
    logging.info('Original point count: {}, simplified point count: {}'.format(
        len(route['original_points']), len(route['points'])))

    if get_elevation:
        elevation_points = list(get_equal_spaced_points(simplified_points, 500))
        elevations = await get_elevation_for_points(settings, [point for point, distance in elevation_points])
        route['elevation'] = [(round(point.lat, 6), round(point.lng, 6), elevation, dist) for elevation, (point, dist) in zip(elevations, elevation_points)]


async def get_elevation_for_points(settings, points):
    n = 200
    result = []
    async with aiohttp.ClientSession() as session:
        for i in range(0, len(points), n):
            section_points = points[i:i + n]

            section_polyline = polyline.encode([(round(point.lat, 6), round(point.lng, 6)) for point in section_points])
            r = await session.get(
                'https://maps.googleapis.com/maps/api/elevation/json',
                params={
                    'sensor': 'false',
                    'key': settings['google_api_key'],
                    'locations': "enc:{}".format(section_polyline)
                })
            elevations = await r.json()
            if elevations['status'] != 'OK':
                logging.error(elevations)
            else:
                result.extend((elv['elevation'] for elv in elevations['results']))

    return result
