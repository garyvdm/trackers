import argparse
import asyncio
import copy
import itertools
import logging.config
import os
import signal
import sys
from contextlib import AsyncExitStack, closing
from functools import partial, wraps
from math import floor, sqrt
from os.path import join, relpath

import aiohttp
import dulwich.repo
import polyline
import yaml
from more_itertools import chunked, interleave_longest

import trackers.events
import trackers.general
import trackers.modules
from trackers.analyse import (
    distance,
    find_closest_point_pair_route,
    get_analyse_route,
    get_equal_spaced_points,
    IndexedPoint,
    ramer_douglas_peucker,
    ramer_douglas_peucker_sections,
    route_with_distance_and_index,
)
from trackers.dulwich_helpers import TreeWriter

defaults_yaml = f"""
    data_path: {relpath(join(__file__, '../../data'))}
    cache_path: {relpath(join(__file__, '../../cache'))}
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
                format: '%(asctime)s %(levelname)-5.5s [%(name)s] %(message)s'
        root:
            level: INFO
            handlers: [console, ]

        loggers:
            aiohttp.access:
                level: ERROR

"""


def get_base_argparser(*args, **kwargs):
    parser = argparse.ArgumentParser()
    parser.add_argument('settings_file', action='store', nargs='?',
                        default=None,
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
        settings_file = args.settings_file
        if settings_file is None:
            paths = (
                '/etc/trackers.yaml',
                'settings.yaml',
                relpath(join(__file__, '../../settings.yaml', ))
            )

            for path in paths:
                if os.path.exists(path):
                    settings_file = path
                    break

        if settings_file is None:
            logging.warn(f'Settings file not specified, and no defaults not exist ({paths}).')
        else:
            try:
                with open(settings_file) as f:
                    settings_from_file = yaml.load(f)
            except FileNotFoundError:
                settings_from_file = {}
            settings.update(settings_from_file)

    logging.config.dictConfig(settings['logging'])
    if args and args.debug:
        logging.getLogger('trackers').setLevel(logging.DEBUG)
        logging.getLogger('web_app').setLevel(logging.DEBUG)
        # logging.getLogger('asyncio').setLevel(logging.DEBUG)

    settings['debug'] = args and args.debug

    if sys.stdout.isatty() and settings['logging'] != defaults['logging']:
        # Reapply the default logging settings
        logging.config.dictConfig(defaults['logging'])

    # settings_dump = yaml.dump(settings)
    # logging.getLogger('trackers').info('Combined Settings: \n{}'.format(settings_dump))

    return settings


async def app_setup(app, settings):
    stack = AsyncExitStack()

    await stack.enter_async_context(await app_setup_basic(app, settings))
    await stack.enter_async_context(await trackers.modules.config_modules(app, settings))

    return stack


async def app_setup_basic(app, settings):
    stack = AsyncExitStack()

    app['start_event_trackers'] = {
        'static': trackers.general.static_start_event_tracker,
        'cropped': partial(trackers.general.wrapped_tracker_start_event, trackers.general.cropped_tracker_start),
        'filter_inaccurate': partial(trackers.general.wrapped_tracker_start_event, trackers.general.filter_inaccurate_tracker_start),
    }

    app['trackers.settings'] = settings
    app['trackers.data_repo'] = stack.enter_context(dulwich.repo.Repo(settings['data_path']))
    app['trackers.events'] = {}
    app['analyse_processing_lock'] = asyncio.Lock()

    return stack


def async_command(get_parser_func, basic=False):
    def async_command_decorator(func):
        async def run_with_app(settings, args):
            app = {}
            setup = app_setup_basic if basic else app_setup
            async with await setup(app, settings):
                loop = asyncio.get_event_loop()
                run_fut = asyncio.ensure_future(func(app, settings, args))

                def cancel(signal):
                    logging.info('Canceling')
                    run_fut.cancel()

                for signame in ('SIGINT', 'SIGTERM'):
                    loop.add_signal_handler(getattr(signal, signame), cancel, None)
                try:
                    await run_fut
                finally:
                    for signame in ('SIGINT', 'SIGTERM'):
                        loop.remove_signal_handler(getattr(signal, signame))

        @wraps(func)
        def async_command_wrapper():
            parser = get_parser_func()
            args = parser.parse_args()
            settings = get_combined_settings(args=args)
            with closing(asyncio.get_event_loop()) as loop:
                # loop.set_debug(settings['debug'])
                try:
                    loop.run_until_complete(run_with_app(settings, args))
                except asyncio.CancelledError:
                    logging.error('CancelledError.')

        return async_command_wrapper
    return async_command_decorator


def event_command_parser(*args, **kwargs):
    parser = get_base_argparser(*args, **kwargs)
    parser.add_argument('event_name', action='store')
    return parser


def event_name_clean(event_name, settings):
    if os.path.split(event_name)[0] != '':
        events_path = join(settings['data_path'], 'events')
        events_rel_path = relpath(event_name, start=events_path)
        if os.path.split(events_rel_path)[0] == '':
            return events_rel_path
    return event_name


@async_command(partial(event_command_parser, description="Convert live trackers to static data."))
async def convert_to_static(app, settings, args):
    tree_writer = TreeWriter(app['trackers.data_repo'])

    event_name = event_name_clean(args.event_name, settings)
    event = await trackers.events.Event.load(app, event_name, tree_writer)
    await event.convert_to_static(tree_writer)
    await event.store_analyse(tree_writer)


@async_command(partial(event_command_parser, description="Recalculate and store analyse trackers"), basic=True)
async def store_analyse(app, settings, args):
    tree_writer = TreeWriter(app['trackers.data_repo'])
    event_name = event_name_clean(args.event_name, settings)
    event = await trackers.events.Event.load(app, event_name, tree_writer)
    await event.store_analyse(tree_writer)


@async_command(partial(event_command_parser, description="Assigns unique colors to riders"), basic=True)
async def assign_rider_colors(app, settings, args):
    event_name = event_name_clean(args.event_name, settings)
    tree_writer = TreeWriter(app['trackers.data_repo'])
    event = await trackers.events.Event.load(app, event_name, tree_writer)
    assign_rider_colors_inner(event)
    await event.save(f'{event_name}: assign_rider_colors', tree_writer=tree_writer)


def assign_rider_colors_inner(event):
    num_riders = len(event.config['riders'])
    hues = [round(i * 360 / num_riders) for i in range(num_riders)]
    alternating_chunks = floor(sqrt(num_riders))
    chunked_hues = [list(s) for s in chunked(hues, alternating_chunks)]
    alternating_hues = interleave_longest(*chunked_hues)
    for rider, hue in zip(event.config['riders'], alternating_hues):
        rider['color'] = 'hsl({}, 100%, 50%)'.format(hue)
        rider['color_pre_post'] = 'hsl({}, 100%, 70%)'.format(hue)
        rider['color_marker'] = 'hsl({}, 100%, 60%)'.format(hue)


def add_gpx_to_event_routes_parser():
    parser = get_base_argparser(description="Add a gpx file to the routes for of an event.")
    parser.add_argument('event_name', action='store')
    parser.add_argument('gpx_file', action='store')
    parser.add_argument('--no-elevation', action='store_true')
    parser.add_argument('--split-at-dist', action='store', type=int, nargs='*',
                        help="Distances to split route at when performing RDP simplification")
    parser.add_argument('--split-point-range', action='store', type=int, default=500)
    parser.add_argument('--rdp-epsilon', action='store', type=int, default=2)
    parser.add_argument('--circular-range', action='store', type=int,
                        help="Set to about 1/2 of distance (m) of circular route. To help with find closest point.")
    parser.add_argument('--print', action='store_true')

    return parser


@async_command(add_gpx_to_event_routes_parser, basic=True)
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

    route = {'original_points': points}
    for key in ('no_elevation', 'split_at_dist', 'split_point_range', 'rdp_epsilon', 'circular_range', 'gpx_file'):
        route[key] = getattr(args, key)

    markers = [
        {
            'title': wpt.find('gpx:name', gpx_ns).text,
            'marker_text': wpt.find('gpx:name', gpx_ns).text,
            'position': {
                'lat': round(float(wpt.attrib['lat']), 6),
                'lng': round(float(wpt.attrib['lon']), 6),
            },
        }
        for wpt in xml_doc.findall('./gpx:wpt', gpx_ns)
    ]

    event_name = event_name_clean(args.event_name, settings)
    if not args.print:
        writer = TreeWriter(app['trackers.data_repo'])
        event = await trackers.events.Event.load(app, event_name, writer)
        await process_route(settings, route)
        event.routes.append(route)
        process_secondary_route_details(event.routes)
        if 'markers' not in event.config:
            event.config['markers'] = []
        event.config['markers'].extend(markers)
        # TODO - add gpx file to repo
        await event.save(f'{event_name}: add_gpx_to_event_routes {args.gpx_file}',
                         tree_writer=writer, save_routes=True)
    else:
        original_points = route['original_points']
        filtered_points = (point for last_point, point in zip([None] + original_points[:-1], original_points) if point != last_point)
        point_points = route_with_distance_and_index(filtered_points)
        for point in point_points:
            print(f'{point.index}: {point.distance} {point.lat},{point.lng}')


@async_command(partial(event_command_parser, description="Open and save event. Side effect is convert to new formats."), basic=True)
async def reformat_event(app, settings, args):
    event_name = event_name_clean(args.event_name, settings)
    writer = TreeWriter(app['trackers.data_repo'])
    event = await trackers.events.Event.load(app, event_name, writer)
    for route in event.routes:
        for key in ('start_distance', 'end_distance', 'dist_factor'):
            if key in route:
                route[key] = float(route[key])
    await event.save(f'{event_name}: reformat_event', tree_writer=writer, save_routes=True)


@async_command(partial(event_command_parser, description="Update bounds for event"), basic=True)
async def update_bounds(app, settings, args):
    event_name = event_name_clean(args.event_name, settings)
    writer = TreeWriter(app['trackers.data_repo'])
    event = await trackers.events.Event.load(app, event_name, writer)
    update_bounds_inner(event)
    await event.save(f'{event_name}: update_bounds', tree_writer=writer)


def update_bounds_inner(event):
    points = list(itertools.chain.from_iterable(
        [((marker['position']['lat'], marker['position']['lng']), ) for marker in event.config.get('markers', ())] +
        [route['points'] for route in event.routes]
    ))
    lats = [point[0] for point in points]
    lngs = [point[1] for point in points]

    event.config['bounds'] = {
        'north': max(lats),
        'south': min(lats),
        'east': max(lngs),
        'west': min(lngs),
    }


def process_event_routes_parser(*args, **kwargs):
    parser = get_base_argparser(*args, **kwargs)
    parser.add_argument('event_name', action='store')
    parser.add_argument('--rdp-epsilon', action='store', type=int)
    return parser


@async_command(partial(process_event_routes_parser, description="Reprocess event routes."), basic=True)
async def process_event_routes(app, settings, args):
    event_name = event_name_clean(args.event_name, settings)
    writer = TreeWriter(app['trackers.data_repo'])
    event = await trackers.events.Event.load(app, event_name, writer)
    for route in event.routes:
        if args.rdp_epsilon:
            route['rdp_epsilon'] = args.rdp_epsilon
        await process_route(settings, route)
    process_secondary_route_details(event.routes)
    await event.save(f'{event_name}: process_event_routes', tree_writer=writer, save_routes=True)


def process_secondary_route_details(routes):
    main_route = get_analyse_route(routes[0])
    for i, route in enumerate(routes):
        if i > 0:
            route['main'] = False
            route_points = route_with_distance_and_index(route['points'])
            start_closest = find_closest_point_pair_route(main_route, route_points[0], None, None)
            prev_point = start_closest.point_pair[0]
            route['prev_point'] = prev_point.index
            route['start_distance'] = start_distance = float(prev_point.distance + distance(prev_point, route_points[0]))
            end_closest = find_closest_point_pair_route(main_route, route_points[-1], None, None)
            next_point = end_closest.point_pair[1]
            route['next_point'] = next_point.index
            route['end_distance'] = end_distance = float(next_point.distance - distance(next_point, route_points[-1]))
            route['dist_factor'] = float((end_distance - start_distance) / route_points[-1].distance)
        else:
            route['main'] = True


async def process_route(settings, route):
    original_points = route['original_points']
    filtered_points = (point for last_point, point in zip([None] + original_points[:-1], original_points) if point != last_point)
    point_points = route_with_distance_and_index(filtered_points)

    if 'rdp_epsilon' not in route:
        route['rdp_epsilon'] = 2

    if not route.get('split_at_dist'):
        points = ramer_douglas_peucker(point_points, route['rdp_epsilon'])
    else:
        points = ramer_douglas_peucker_sections(point_points, route['rdp_epsilon'], route['split_at_dist'], route['split_point_range'])
    route['points'] = [(point.lat, point.lng) for point in points]

    indexed_points = [IndexedPoint(point.lat, point.lng, index=i, distance=point.distance) for i, point in enumerate(points)]
    if not route.get('split_at_dist'):
        simplified_points = ramer_douglas_peucker(indexed_points, 500)
    else:
        simplified_points = ramer_douglas_peucker_sections(indexed_points, 500, route['split_at_dist'], route['split_point_range'])
    route['simplified_points_indexes'] = [point.index for point in simplified_points]

    logging.info(f"Original point count: {len(route['original_points'])}, point count: {len(points)}, simplified point count: {len(simplified_points)}")

    if not route.get('no_elevation', False):
        logging.info('Getting Elevation')
        elevation_points = list(get_equal_spaced_points(points, 500))
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


@async_command(partial(event_command_parser, description="Loads csv from stdin, writes to riders in config"), basic=True)
async def load_riders_from_csv(app, settings, args):
    event_name = event_name_clean(args.event_name, settings)
    tree_writer = TreeWriter(app['trackers.data_repo'])
    event = await trackers.events.Event.load(app, event_name, tree_writer)

    import csv
    reader = csv.DictReader(sys.stdin)

    def trackers_from_row(row):
        if row.get('Traccar Device Id'):
            yield {'type': 'traccar', 'unique_id': row['Traccar Device Id']}
        if row.get('TKStorage'):
            yield {'type': 'tkstorage', 'id': row['TKStorage']}
        if row.get('Spot'):
            yield {'type': 'spot', 'feed_id': row['Spot']}

    event.config['riders'] = [
        {
            'name': row['Name'],
            'name_short': row['Short Name'],
            'trackers': list(trackers_from_row(row)),
        }
        for row in reader
    ]
    assign_rider_colors_inner(event)

    await event.save(f'{event_name}: load_riders_from_csv', tree_writer=tree_writer)


@async_command(partial(event_command_parser, description="Runs analyse trackers. Just for testing."))
async def analyse(app, settings, args):
    event_name = event_name_clean(args.event_name, settings)
    tree_writer = TreeWriter(app['trackers.data_repo'])
    event = await trackers.events.Event.load(app, event_name, tree_writer)
    await event.start_trackers(analyse=True)
    await event.stop_and_complete_trackers()
