import asyncio
import base64
import contextlib
import copy
import csv
import datetime
import hashlib
import io
import json
import logging
import re
from base64 import urlsafe_b64encode
from collections import defaultdict
from contextlib import asynccontextmanager, suppress
from functools import partial, wraps

import pkg_resources
import yaml
from aiohttp import web, WSCloseCode, WSMsgType
from htmlwrite import Markup, Tag, Writer
from more_itertools import chunked, first

import trackers.auth
import trackers.bin_utils
import trackers.events
from trackers.analyse import AnalyseTracker
from trackers.auth import ensure_authorized_event, get_git_author, get_identity, show_identity
from trackers.base import cancel_and_wait_task, list_register, Observable
from trackers.dulwich_helpers import TreeReader, TreeWriter
from trackers.general import hash_bytes, json_dumps
from trackers.web_helpers import (
    coro_partial,
    etag_query_hash_response,
    etag_response,
    immutable_cache_control,
    ProcessedStaticManager,
    sass_body_loader,
)

logger = logging.getLogger(__name__)

server_version = 10


async def client_error_logger(request):
    body = await request.text()
    static_path = pkg_resources.resource_filename('trackers', '/static')
    body = convert_client_urls_to_paths(static_path, body)
    body = body[:1024 * 1024]  # limit to 1kb
    agent = request.headers.get('User-Agent', '')
    peername = request.transport.get_extra_info('peername')
    forwared_for = request.headers.get('X-Forwarded-For')
    client = forwared_for or (peername[0] if peername else '')
    logger.error('\n'.join((body, agent, client)))
    return web.Response()


async def make_aio_app(settings,
                       app_setup=trackers.bin_utils.app_setup,
                       client_error_handler=client_error_logger,
                       exception_recorder=lambda: None):
    app = web.Application()
    app.on_shutdown.append(shutdown)

    app['trackers.settings'] = settings

    app['trackers.ws_sessions'] = []
    app['trackers.event_ws_sessions'] = defaultdict(list)
    app['trackers.individual_trackers'] = {}
    app['exception_recorder'] = exception_recorder

    app['static_manager'] = static_manager = ProcessedStaticManager(app, 'trackers', (on_static_processed, ))
    static_manager.add_resource('/static/event.css', charset='utf8', content_type='text/css', body_loader=sass_body_loader)
    static_manager.add_resource('/static/event.js', charset='utf8', content_type='text/javascript')
    static_manager.add_resource('/static/lib.js', charset='utf8', content_type='text/javascript')
    static_manager.add_resource('/static/individual.js', charset='utf8', content_type='text/javascript')

    static_manager.add_resource('/static/instructions.html', '/instructions', charset='utf8', content_type='text/html')
    static_manager.add_resource('/static/instructions_lgg2019.html', '/instructions_lgg2019', charset='utf8', content_type='text/html')
    static_manager.add_resource('/static/tkstorage_admin.js', charset='utf8', content_type='text/javascript')
    static_manager.add_resource('/static/tkstorage_admin.html', '/tkstorage_admin', charset='utf8', content_type='text/html',
                                body_processor=page_body_processor, )

    static_manager.add_resource_dir('/static/markers')
    static_manager.add_resource_dir('/static/logos')
    static_manager.add_resource_dir('/static/contrib')

    static_manager.add_resource('/static/event.html')  # This is just here so that we reload on change.
    static_manager.add_resource('/static/individual.html', route_name='individual_page',
                                charset='utf8', content_type='text/html',
                                body_processor=partial(
                                    page_body_processor,
                                    api_key=settings['google_api_key'],
                                ))

    static_manager.start_monitor_and_process_resources()
    await trackers.auth.config_aio_app(app, settings)

    app.router.add_route('GET', '/', handler=home, name='home')
    app['home_pages'] = {}
    app.router.add_route('GET', '/admin', handler=admin, name='admin')

    app.router.add_route('GET', '/{event}', handler=event_page, name='event_page')
    app.router.add_route('GET', '/{event}/websocket', handler=event_ws, name='event_ws')
    app.router.add_route('GET', '/{event}/state', handler=event_state, name='event_state')
    app.router.add_route('GET', '/{event}/config', handler=event_config, name='event_config')
    app.router.add_route('GET', '/{event}/routes', handler=event_routes, name='event_routes')
    app.router.add_route('GET', '/{event}/riders_points', name='riders_points',
                         handler=coro_partial(blocked_lists, list_attr_name='blocked_list'))
    app.router.add_route('GET', '/{event}/riders_off_route', name='riders_off_route',
                         handler=coro_partial(blocked_lists, list_attr_name='off_route_blocked_list'))
    app.router.add_route('GET', '/{event}/riders_pre_post', name='riders_pre_post',
                         handler=coro_partial(blocked_lists, list_attr_name='pre_post_blocked_list'))
    app.router.add_route('GET', '/{event}/riders_csv', name='riders_csv', handler=riders_csv)

    app.router.add_route('GET', '/{event}/admin', handler=event_admin, name='event_admin')
    app.router.add_route('POST', '/{event}/set_start', handler=event_set_start, name='event_set_start')
    app.router.add_route('POST', '/{event}/add_rider_point', handler=event_add_rider_point, name='event_add_rider_point')
    app.router.add_route('POST', '/{event}/add_rider_tracker', handler=event_add_rider_tracker, name='event_add_rider_tracker')
    app.router.add_route('GET', '/{event}/edit', handler=event_config_edit, name='event_config_edit')

    app.router.add_route('POST', '/client_error', handler=client_error_handler, name='client_error')

    app['trackers.app_setup_cm'] = app_setup_cm = await app_setup(app, settings)
    await app_setup_cm.__aenter__()

    app['load_with_watcher_task'] = asyncio.ensure_future(
        trackers.events.load_with_watcher(
            app,
            new_event_observable=Observable(logger=trackers.events.logger, callbacks=(on_new_event, )),
            removed_event_observable=Observable(logger=trackers.events.logger, callbacks=(on_removed_event, )),
        ))

    return app


async def shutdown(app):
    logger.info('Closing web socket connections.')
    close_fs = [ws.close(code=WSCloseCode.SERVICE_RESTART, message='Server shutdown') for ws in app['trackers.ws_sessions']]
    if close_fs:
        await asyncio.wait(close_fs, timeout=20)

    logger.info('Stopping load_with_watcher_task')
    await cancel_and_wait_task(app['load_with_watcher_task'])

    logger.info('Stopping event and individual trackers')
    stop_and_complete_trackers_fs = \
        [event.stop_and_complete_trackers() for event in app['trackers.events'].values()] + \
        [individual_discard_tracker(app, tracker_info) for tracker_info in app['trackers.individual_trackers'].values()]

    if stop_and_complete_trackers_fs:
        await asyncio.wait(stop_and_complete_trackers_fs)

    logger.info('Module cleanup')
    await app['trackers.app_setup_cm'].__aexit__(None, None, None)


def json_response(data, **kwargs):
    return web.Response(text=json_dumps(data), content_type='application/json', **kwargs)


def page_body_processor(static_manager, body, **kwargs):
    # since we need the hash in the body, we manually hash every thing that goes into the body.

    hash = hashlib.sha1(body)

    for key, value in kwargs.items():
        hash.update(key.encode())
        hash.update(value.encode())

    # Probably should not use all static resources.
    for key, value in static_manager.urls.items():
        hash.update(key.encode())
        hash.update(str(value).encode())

    client_hash = base64.urlsafe_b64encode(hash.digest()).decode('ascii')

    formated_body = body.decode('utf8').format(
        client_hash=client_hash,
        static_urls=static_manager.urls,
        **kwargs,
    ).encode('utf8')

    return formated_body, client_hash


def say_error_handler(func):
    @wraps(func)
    async def say_error_handler_inner(request, **kwargs):
        try:
            return await func(request, **kwargs)
        except web.HTTPException:
            raise
        except Exception as e:
            request.app['exception_recorder']()
            logger.exception('')
            message = getattr(e, 'message', None)
            if not message:
                message = '{}: {}'.format(type(e).__name__, e)
            return web.HTTPInternalServerError(text=message)
    return say_error_handler_inner


def event_handler(func):
    @wraps(func)
    async def event_handler_inner(request, **kwargs):
        event_name = request.match_info['event']
        event = request.app['trackers.events'].get(event_name)
        if event is None:
            raise web.HTTPNotFound()
        return await func(request, event, **kwargs)
    return event_handler_inner


async def get_event_state(app, event):
    await ensure_event_page(app, event)
    return {
        'live': event.config.get('live', False),
        'config_hash': event.client_config_body_hash,
        'routes_hash': event.client_routes_body_hash,
        'riders_values': getattr(event, 'riders_current_values', {}),
        'client_hash': event.page[1],
        'server_time': datetime.datetime.now(),
    }


async def home(request):
    host = request.headers.get('Host')
    if host.endswith(':5234'):
        host = 'trackrace.tk'
    page = request.app['home_pages'].get('host')
    if not page:
        events = tuple(sorted(request.app['trackers.events'].values(),
                              key=lambda event: event.config.get('event_start'),
                              reverse=True))
        if host != 'trackrace.tk':
            events = [event for event in events if host in event.config.get('hosts', ())]
        live_events = [event for event in events if event.config.get('live', False)]
        past_events = [event for event in events if not event.config.get('live', False)]

        router = request.app.router
        events_body = io.StringIO()
        writer = Writer(events_body)
        w = writer.w
        c = writer.c
        if live_events:
            w(Tag('h2'), 'Live Events')
            with c(Tag('ul', class_='collection')):
                for event in live_events:
                    w(Tag('a', class_='collection-item', href=router['event_page'].url_for(event=event.name)),
                      event.config.get('title', event.name))

        w(Tag('h2'), 'Past Events')
        with c(Tag('div', class_='collection')):
            for event in past_events:
                w(Tag('a', class_='collection-item', href=router['event_page'].url_for(event=event.name)),
                  event.config.get('title', event.name))

        page_path = f'/static/home/{host}.html'
        page = await request.app['static_manager'].get_static_processed_resource(
            page_path,
            body_processor=partial(
                page_body_processor,
                events=events_body.getvalue()
            ),
        )

        request.app['home_pages'][host] = page

    body, etag = page
    response = web.Response(body=body, charset='utf8', content_type='text/html',)
    return etag_response(request, response, etag)


async def ensure_event_page(app, event):
    if not hasattr(event, 'page'):
        page_path = event.config.get('page', '/static/event.html')
        event.page = await app['static_manager'].get_static_processed_resource(
            page_path,
            body_processor=partial(
                page_body_processor,
                api_key=app['trackers.settings']['google_api_key'],
                title=event.config['title'],
            ),
        )


async def event_page(request):
    event_name = request.match_info['event']
    event = request.app['trackers.events'].get(event_name)
    if event is None:
        raise web.HTTPNotFound()
    await ensure_event_page(request.app, event)
    body, etag = event.page
    response = web.Response(body=body, charset='utf8', content_type='text/html',)
    return etag_response(request, response, etag)


@say_error_handler
@event_handler
async def event_state(request, event):
    if event.config.get('live', False):
        state = {'live': True}
    else:
        await event.start_trackers()
        if all([rider_objs.tracker.completed.done() for rider_objs in event.riders_objects.values()]):
            state = await get_event_state(request.app, event)
        else:
            state = {'loading': True}

    response = json_response(state)
    etag = base64.urlsafe_b64encode(hashlib.sha1(response.body).digest()).decode('ascii')
    return etag_response(request, response, etag)


@say_error_handler
@event_handler
async def event_config(request, event):
    response = web.Response(text=event.client_config_body, content_type='application/json')
    return etag_query_hash_response(request, response, event.client_config_body_hash)


@say_error_handler
@event_handler
async def event_routes(request, event):
    response = web.Response(body=event.client_routes_body, content_type='application/json')
    return etag_query_hash_response(request, response, event.client_routes_body_hash)


point_keys = {
    'time': 't',
    'position': 'p',
    'track_id': 'i',
    'status': 's',
    'dist_route': 'o',
    'dist_ridden': 'd',
    'dist_from_last': 'l',
    'hash': 'h',
    'index': 'x',
}


def compress_point(point):
    return {point_keys.get(key, key): value for key, value in point.items()}


@say_error_handler
@event_handler
async def blocked_lists(request, event, list_attr_name):
    await event.start_trackers()
    rider_name = request.query.get('name')
    if not rider_name:
        hasher = hashlib.sha1()
        lists = [(rider_objects.rider_name, getattr(rider_objects, list_attr_name)) for rider_objects in event.riders_objects.values()]
        lists = [(name, list) for name, list in lists if list]
        for name, list in lists:
            source = list.get_source()
            if source:
                hasher.update(source[-1]['hash'].encode())
        hash = urlsafe_b64encode(hasher.digest()[:3]).decode('ascii')
        blocked_lists_full = {name: list.full for name, list in lists}
        return etag_response(request, partial(json_response, blocked_lists_full), hash)
    else:
        start_index = int(request.query.get('start_index'))
        end_index = int(request.query.get('end_index'))
        end_hash = request.query.get('end_hash')

        rider_objects = event.riders_objects[rider_name]
        list = getattr(rider_objects, list_attr_name)
        source = list.get_source()
        points = source[start_index:end_index + 1]
        if points and points[-1]['hash'] != end_hash:
            raise web.HTTPInternalServerError(text='Wrong end_hash')

        return etag_response(request, json_response(points), end_hash,
                             cache_control=immutable_cache_control)


@say_error_handler
@event_handler
async def riders_csv(request, event):
    await event.start_trackers()

    rider_name = request.query.get('name')
    tracker = event.riders_objects[rider_name].tracker

    out_file = io.StringIO()
    writer = csv.writer(out_file)

    writer.writerow(['latitude', 'longitude', 'time'])
    for point in tracker.points:
        if 'position' in point:
            writer.writerow((format(point['position'][0], '.6f'), format(point['position'][1], '.6f'),
                             point['time'].astimezone(datetime.timezone.utc).isoformat()))
    return etag_response(
        request, web.Response(
            text=out_file.getvalue(),
            content_type='text/csv',
            headers=(('content-disposition', f'attachment; filename="{event.name} - {rider_name}.csv"'), )
        ),
        etag=tracker.points[-1]['hash'],
    )


async def on_static_processed(static_manager):
    app = static_manager.app
    for event in app.get('trackers.events', {}).values():
        with suppress(AttributeError):
            del event.page
        event_wss = event.app['trackers.event_ws_sessions'][event.name]
        if event_wss:
            await ensure_event_page(app, event)
            await message_to_multiple_wss(
                event.app,
                event_wss,
                {'client_hash': event.page[1]}
            )


async def on_new_event(event):
    event.config_routes_change_observable.subscribe(on_event_config_routes_change)
    event.rider_new_values_observable.subscribe(partial(on_event_rider_new_values, 'riders_values'))
    event.rider_pre_post_new_values_observable.subscribe(partial(on_event_rider_new_values, 'riders_pre_post_values',
                                                                 filter_ws=lambda ws: 'riders_pre_post' in ws.subscriptions))
    event.rider_blocked_list_update_observable.subscribe(partial(on_event_rider_blocked_list_update, 'riders_points'))
    event.rider_off_route_blocked_list_update_observable.subscribe(partial(on_event_rider_blocked_list_update, 'riders_off_route'))
    event.rider_pre_post_blocked_list_update_observable.subscribe(partial(on_event_rider_blocked_list_update, 'riders_pre_post'))
    event.rider_predicted_updated_observable.subscribe(on_event_rider_predicted_updated)
    await on_event_config_routes_change(event)
    event.app['home_pages'] = {}


async def on_removed_event(event):
    event.app['home_pages'] = {}


web_route_keys = (
    'points',
    'elevation',
    'main',
    'dist_factor',
    'start_distance',
    'end_distance',
)


def filter_event_config_for_web(config):
    'Filters out keys from event config that the tracker page does not need.'

    config = copy.deepcopy(config)

    with suppress(KeyError):
        del config['admin']
    with suppress(KeyError):
        del config['page']

    for rider in config.get('riders', ()):
        with suppress(KeyError):
            del rider['points']
        with suppress(KeyError):
            del rider['trackers']
        with suppress(KeyError):
            del rider['tracker']
    return config


async def on_event_config_routes_change(event):
    event.client_config_body = json_dumps(filter_event_config_for_web(event.config))
    event.client_config_body_hash = hash_bytes(event.client_config_body.encode())
    filtered_routes = [
        {key: value for key, value in route.items() if key in web_route_keys}
        for route in event.routes]

    event.client_routes_body = json_dumps(filtered_routes)
    event.client_routes_body_hash = hash_bytes(event.client_routes_body.encode())

    state = await get_event_state(event.app, event)
    await message_to_multiple_wss(
        event.app,
        event.app['trackers.event_ws_sessions'][event.name],
        state,
    )
    event.app['home_pages'] = {}
    if event.config.get('live', False):
        event.start_trackers_without_wait()


async def on_event_rider_new_values(key, event, rider_name, values, filter_ws=None):
    await message_to_multiple_wss(
        event.app,
        event.app['trackers.event_ws_sessions'][event.name],
        {key: {rider_name: values}},
        filter_ws=filter_ws,
    )


async def on_event_rider_blocked_list_update(key, event, rider_name, blocked_list, update):
    event_wss = event.app['trackers.event_ws_sessions'][event.name]
    await message_to_multiple_wss(
        event.app,
        event_wss,
        {key: {rider_name: update}},
        filter_ws=lambda ws: key in ws.subscriptions or f'{key}.{rider_name}' in ws.subscriptions,
    )


async def on_event_rider_predicted_updated(event, predicted, time):
    await message_to_multiple_wss(
        event.app,
        event.app['trackers.event_ws_sessions'][event.name],
        {'riders_predicted': predicted, },
        filter_ws=lambda ws: 'riders_predicted' in ws.subscriptions,
    )


def get_rider_blocked_list(event, list_name):
    unfiltered = (
        (rider_objects.rider_name, getattr(rider_objects, list_name))
        for rider_objects in event.riders_objects.values())
    return ((rider_name, list.full) for rider_name, list in unfiltered if list)


async def event_ws(request):
    ws = web.WebSocketResponse()
    ws.subscriptions = set()
    await ws.prepare(request)
    with contextlib.ExitStack() as exit_stack:
        try:
            exit_stack.enter_context(list_register(request.app['trackers.ws_sessions'], ws))

            send = partial(message_to_multiple_wss, request.app, (ws, ))
            await send({})

            event_name = request.match_info['event']
            event = request.app['trackers.events'].get(event_name)
            if event is None:
                await ws.close(message='Error: Event not found.')
                return ws

            if not event.config.get('live', False):
                await send({'live': False})
                await ws.close(message='Event not live. Use rest api')
                return ws

            await event.start_trackers()

            state = await get_event_state(request.app, event)
            await send(state)
            exit_stack.enter_context(list_register(request.app['trackers.event_ws_sessions'][event_name], ws))

            async for msg in ws:
                if msg.type == WSMsgType.text:
                    try:
                        logger.debug('receive: {}'.format(msg.data))
                        data = json.loads(msg.data)
                        if 'subscriptions' in data:
                            old_subscriptions = ws.subscriptions
                            ws.subscriptions = set(data['subscriptions'])
                            added_subscriptions = ws.subscriptions - old_subscriptions
                            if 'riders_points' in added_subscriptions:
                                await send({'riders_points': {rider_name: list_full
                                                              for rider_name, list_full in get_rider_blocked_list(event, 'blocked_list')}})
                            else:
                                selected_rider_points = {rider_name: list_full
                                                         for rider_name, list_full in get_rider_blocked_list(event, 'blocked_list')
                                                         if f'riders_points.{rider_name}' in added_subscriptions}
                                if selected_rider_points:
                                    await send({'riders_points': selected_rider_points})

                            if 'riders_off_route' in added_subscriptions:
                                await send({'riders_off_route': {rider_name: list_full
                                                                 for rider_name, list_full in get_rider_blocked_list(event, 'off_route_blocked_list')}})

                            if 'riders_pre_post' in added_subscriptions:
                                await send({
                                    'riders_pre_post_values': getattr(event, 'riders_pre_post_values', {}),
                                    'riders_pre_post': {rider_name: list_full
                                                        for rider_name, list_full in get_rider_blocked_list(event, 'pre_post_blocked_list')}
                                })

                            if 'riders_predicted' in added_subscriptions:
                                await send({'riders_predicted': event.riders_predicted_points})

                    except Exception:
                        request.app['exception_recorder']()
                        logger.exception('Error in receive ws msg:')

                if msg.type == WSMsgType.close:
                    await ws.close()
                if msg.type == WSMsgType.error:
                    raise ws.exception()
        except asyncio.CancelledError:
            pass
        except Exception as e:
            request.app['exception_recorder']()
            await ws.close(message='Server Error: {}'.format(e))
            logger.exception('Error in event_ws: ')
        finally:
            return ws


async def message_to_multiple_wss(app, wss, msg, log_level=logging.DEBUG, filter_ws=None):
    msg = json_dumps(msg)
    filtered_wss = [ws for ws in wss if (filter_ws(ws) if filter_ws else True) and not ws.closed]
    logger.log(log_level, f'send to {len(filtered_wss)}: {msg[:1000]}')
    if filtered_wss:
        futures = [asyncio.ensure_future(ws.send_str(msg)) for ws in filtered_wss]
        await asyncio.wait(futures)
        for fut in futures:
            try:
                fut.result()
            except Exception:
                app['exception_recorder']()
                logger.exception('Error sending msg to ws:')


@say_error_handler
@event_handler
@ensure_authorized_event
async def event_set_start(request, event):
    event.config['event_start'] = datetime.datetime.now().replace(microsecond=0)
    author = await get_git_author(request)
    await event.save(f"{event.name}: Set event start", author=author)
    return web.Response(text='Start time set to {}'.format(event.config['event_start']))


async def individual_page(request):
    return await request.app['static_manager'].resource_handler('individual_page', request)


async def individual_ws(get_key, get_tracker, request):
    ws = web.WebSocketResponse()
    await ws.prepare(request)
    try:
        async with contextlib.AsyncExitStack() as exit_stack:
            async def send(msg):
                logger.debug('send: {}'.format(str(msg)[:1000]))
                await ws.send_str(json_dumps(msg))
            await send({
                'client_hash': request.app['static_manager'].processed_resources['individual_page'].hash,
                'server_time': datetime.datetime.now()
            })

            tracker_info = await exit_stack.enter_async_context(get_individual_tracker(request, get_key, get_tracker))
            tracker = tracker_info['tracker']
            exit_stack.enter_context(list_register(request.app['trackers.ws_sessions'], ws))
            exit_stack.enter_context(list_register(tracker_info['ws_sessions'], ws))

            async for msg in ws:
                if msg.type == WSMsgType.text:
                    data = json.loads(msg.data)
                    logger.debug('receive: {}'.format(data))
                    resend = False
                    if 'send_points_since' in data:
                        if resend:
                            await send({'erase_points': 1})
                            client_point_indexes = 0
                        else:
                            client_point_indexes = data['send_points_since']

                        last_index = client_point_indexes
                        new_points = tracker.points[last_index:]
                        if new_points:
                            await individual_tracker_new_points_to_ws(request.app, send, tracker, new_points)
                        exit_stack.enter_context(list_register(tracker.new_points_observable.callbacks,
                                                               partial(individual_tracker_new_points_to_ws, request.app, send)))

                if msg.type == WSMsgType.close:
                    await ws.close()
                if msg.type == WSMsgType.error:
                    raise ws.exception()
            return ws
    except asyncio.CancelledError:
        pass
    except Exception as e:
        request.app['exception_recorder']()
        await ws.send_str(json_dumps({'error': 'Error getting tracker: {}'.format(e)}))
        logger.exception('')
        await ws.close(message='Server Error')

    return ws


@asynccontextmanager
async def get_individual_tracker(request, get_key, get_tracker):
    tracker_key = get_key(request)
    tracker_info = request.app['trackers.individual_trackers'].get(tracker_key)

    if tracker_info is None:
        logger.debug(f'Starting individual tracker: {tracker_key}')
        tracker = await get_tracker(request)
        tracker = await AnalyseTracker.start(tracker, None, ())
        tracker_info = {
            'key': tracker_key,
            'tracker': tracker,
            'ws_sessions': [],
            'discard_task': None
        }
        request.app['trackers.individual_trackers'][tracker_key] = tracker_info
    else:
        logger.debug(f'Using existing individual tracker: {tracker_key}')

        if tracker_info['discard_task']:
            await cancel_and_wait_task(tracker_info['discard_task'])
            tracker_info['discard_task'] = None
    try:
        yield tracker_info
    finally:
        if tracker_info['discard_task']:
            await cancel_and_wait_task(tracker_info['discard_task'])
            tracker_info['discard_task'] = None
        if not tracker_info['ws_sessions']:
            logger.debug(f'No more ws_sessions for {tracker_key}. Will discard in 60 min.')
            tracker_info['discard_task'] = asyncio.ensure_future(individual_discard_tracker_wait(request.app, tracker_info))


async def individual_discard_tracker_wait(app, tracker_info):
    await asyncio.sleep(3600)
    await asyncio.shield(individual_discard_tracker(app, tracker_info))


async def individual_discard_tracker(app, tracker_info):
    logger.debug(f'Stoping individual tracker: {tracker_info["key"]}')
    try:
        tracker = tracker_info['tracker']
        tracker.stop()
        await tracker.complete()
        if tracker_info['discard_task']:
            await cancel_and_wait_task(tracker_info['discard_task'])
    finally:
        del app['trackers.individual_trackers'][tracker_info['key']]


async def individual_tracker_new_points_to_ws(app, ws_send, tracker, new_points):
    try:
        for points in chunked(new_points, 100):
            if len(points) > 50:
                await ws_send({'sending': 'Points'})
            compressed_points = [
                {point_keys.get(key, key): value for key, value in point.items()}
                for point in points
            ]
            await ws_send({'points': compressed_points})

    except Exception:
        app['exception_recorder']()
        logger.exception('Error in individual_tracker_new_points_to_ws:')


client_url_re = re.compile(r'https?://.*?/static/(?P<path>.*?)(\?hash=.*?)?(?P<term>[:\s])')


def convert_client_urls_to_paths(static_path, s):
    return client_url_re.sub(rf'\n{static_path}/\g<path>\g<term>', s)


@say_error_handler
async def admin(request):
    identity = await get_identity(request)

    router = request.app.router
    body = io.StringIO()
    writer = Writer(body)
    w = writer.w
    c = writer.c

    w(Markup('<!DOCTYPE html>'))
    with c(Tag('html')):
        with c(Tag('head')):
            w(Tag('title'), 'Admin')
            w(Tag('meta', name="viewport", content="initial-scale=1.0, user-scalable=no"))
            w(Tag('link', rel="stylesheet", href="https://cdnjs.cloudflare.com/ajax/libs/materialize/1.0.0/css/materialize.min.css"))

        with c(Tag('body', s_padding="24px", s_width="100%", )):
            with c(Tag('div', s_margin="auto", s_max_width="800px", )):
                w(Tag('h3'), 'Admin Login')
                with c(Tag('div', class_="card-panel", s_display="flex", s_width="100%", s_justify_content="space-between")):
                    await show_identity(request, writer, identity)

                if identity:
                    w(Tag('h4'), 'Events')

                    events = tuple(sorted(request.app['trackers.events'].values(),
                                          key=lambda event: event.config.get('event_start'),
                                          reverse=True))
                    with c(Tag('ul', class_='collection')):
                        for event in events:
                            if identity['email'] in event.admin_allowed_principals:
                                w(Tag('a', class_='collection-item', href=router['event_admin'].url_for(event=event.name)),
                                  event.config.get('title', event.name))

    return web.Response(body=body.getvalue(), headers={'Content-Type': 'text/html; charset=utf-8', 'Cache-Control': 'no-cache'})


@say_error_handler
@event_handler
@ensure_authorized_event
async def event_admin(request, event):
    router = request.app.router
    body = io.StringIO()
    writer = Writer(body)
    w = writer.w
    c = writer.c

    w(Markup('<!DOCTYPE html>'))
    with c(Tag('html')):
        with c(Tag('head')):
            w(Tag('title'), ('Admin', event.name))
            w(Tag('meta', name="viewport", content="initial-scale=1.0, user-scalable=no"))
            w(Tag('link', rel="stylesheet",
                  href="/static/contrib/materialize.min.css"))

        with c(Tag('body', s_padding="24px", s_width="100%", )):

            with c(Tag('div', s_margin="auto", s_max_width="800px", )):
                w(Tag('h3'), ('Admin - ', event.config['title']))
                with c(Tag('div', class_="card-panel", s_display="flex", s_width="100%", s_justify_content="space-between")):
                    await show_identity(request, writer)

                with c(Tag('div', class_="card")):
                    with c(Tag('form', action=router['event_add_rider_point'].url_for(event=event.name), method='POST')):

                        with c(Tag('div', class_="card-content")):
                            w(Tag('span', class_="card-title"), 'Add Rider Point')
                            w('Rider: ')
                            w(Tag('br'))
                            with c(Tag('select', name='rider_name', class_="browser-default", s_border="revert", s_background="revert", s_border_radius="revert")):
                                w(Tag('option', value=''), '---')
                                for rider in event.config['riders']:
                                    w(Tag('option', value=rider['name']), rider['name'])

                            w('Point: ')
                            w(Tag('br'))
                            w(Tag('script', src='https://cdnjs.cloudflare.com/ajax/libs/moment.js/2.22.2/moment.min.js'))
                            w(Tag('script'), Markup('''
                                function set_status(status) {
                                    var now = new moment().local().format('YYYY-MM-DDTHH:mm:ss');
                                    var val = 'rider_status: '+status+'\\ntime: ' + now;
                                    if (status == 'Finished') val += '\\nfinish_time: ' + now;
                                    document.getElementById('point').value = val
                                }
                            '''))
                            w(Tag('button', onclick='set_status("Did not start")', type="button", class_="btn waves-effect waves-light"), 'Did not start')
                            w(Tag('button', onclick='set_status("Withdrawn")', type="button", class_="btn waves-effect waves-light"), 'Withdrawn')
                            w(Tag('button', onclick='set_status("Disqualified")', type="button", class_="btn waves-effect waves-light"), 'Disqualified')
                            w(Tag('button', onclick='set_status("Finished")', type="button", class_="btn waves-effect waves-light"), 'Finished')
                            w(Tag('br'))
                            w('Data: ')
                            w(Tag('br'))
                            w(Tag('textarea', name='point', id='point', rows="10", cols="50", s_height="10em"))
                        with c(Tag('div', class_="card-action", s_text_align="right")):
                            w(Tag('button', type='submit', class_="btn waves-effect waves-light"), 'Add rider point')

                with c(Tag('div', class_="card")):
                    with c(Tag('form', action=router['event_add_rider_tracker'].url_for(event=event.name), method='POST')):

                        with c(Tag('div', class_="card-content")):
                            w(Tag('span', class_="card-title"), 'Add rider tracker')
                            w('Rider: ')
                            w(Tag('br'))
                            with c(Tag('select', name='rider_name', class_="browser-default")):
                                w(Tag('option', value=''), '---')
                                for rider in event.config['riders']:
                                    w(Tag('option', value=rider['name']), rider['name'])

                            w('Tracker: ')
                            w(Tag('br'))
                            with c(Tag('label')):
                                w(Tag('input', name="type", type="radio", checked="checked", value="tkstorage", id='type_tkstorage'))
                                w(Tag('span'), 'TKStorage (dedicated trackers)')
                            w(Tag('br'))

                            trackers_objects = request.app['tkstorage.trackers_objects']
                            trackers = [{'id': tracker} for tracker in request.app['tkstorage.trackers']]
                            for tracker in trackers:
                                tracker_objects = trackers_objects.get(tracker['id'])
                                if trackers_objects:
                                    active = tracker_objects.values.get('active')
                                    if active:
                                        tracker['active'] = active
                            trackers.sort(key=lambda tracker: 'active' in tracker)

                            with c(Tag('select', id='tkstorage_id', class_="browser-default")):
                                w(Tag('option', value=''), '---')
                                for tracker in trackers:
                                    active = tracker.get('active')
                                    if active:
                                        active_text = ', '.join([item for item, count in active.items() if count])
                                    else:
                                        active_text = ''
                                    w(Tag('option', value=tracker['id']), f'{tracker["id"]} {active_text}')

                            w(Tag('br'))
                            with c(Tag('label')):
                                w(Tag('input', name="type", type="radio", value="traccar", id='type_traccar'))
                                w(Tag('span'), 'Traccar (phones)')
                            w(Tag('br'))
                            with c(Tag('label')):
                                w(Tag('span'), 'Device id: ')
                                w(Tag('input', id="traccar_device_id", type="text"))

                            w('Data: ')
                            w(Tag('br'))
                            w(Tag('textarea', name='tracker', id='tracker_raw', rows="10", cols="50", s_height="10em"))

                            w(Tag('script'), Markup('''
                                var type_tkstorage = document.getElementById('type_tkstorage');
                                var tkstorage_id = document.getElementById('tkstorage_id');
                                var type_traccar = document.getElementById('type_traccar');
                                var traccar_device_id = document.getElementById('traccar_device_id');
                                var tracker_raw = document.getElementById('tracker_raw');
                                
                                function update_tracker() {
                                    if (type_tkstorage.checked) {
                                        tracker_raw.value = 'type: tkstorage\\nid: ' + tkstorage_id.value;
                                    }
                                    if (type_traccar.checked) {
                                        tracker_raw.value = 'type: traccar\\nunique_id: ' + traccar_device_id.value;
                                    }
                                }
                                type_tkstorage.oninput = update_tracker;
                                tkstorage_id.oninput = update_tracker;
                                type_traccar.oninput = update_tracker;
                                traccar_device_id.oninput = update_tracker;
                            '''))  # NOQA W293

                        with c(Tag('div', class_="card-action", s_text_align="right")):
                            w(Tag('button', type='submit', class_="btn waves-effect waves-light"), 'Add rider tracker')

                with c(Tag('div', class_="card")):
                    with c(Tag('div', class_="card-content")):
                        w(Tag('span', class_="card-title"), 'Start')
                        if 'event_start' in event.config:
                            with c(Tag('p')):
                                w(Tag('b'), 'Start is set to: ')
                                w(event.config['event_start'])
                    with c(Tag('div', class_="card-action", s_text_align="right")):
                        with c(Tag('form', action=router['event_set_start'].url_for(event=event.name), method='POST')):
                            w(Tag('button', type='submit', class_="btn waves-effect waves-light"), 'Set Start to Now')
                with c(Tag('div', class_="card-panel", s_text_align="right")):
                    w(Tag('a', class_="btn waves-effect waves-light",
                          href=router['event_config_edit'].url_for(event=event.name)),
                      'Edit config (Advanced)')

    return web.Response(body=body.getvalue(), headers={'Content-Type': 'text/html; charset=utf-8', 'Cache-Control': 'no-cache'})


@say_error_handler
@event_handler
@ensure_authorized_event
async def event_add_rider_point(request, event):
    post = await request.post()
    rider_name = post['rider_name']
    point = post['point']
    point = yaml.safe_load(point)

    rider_objects = event.riders_objects[rider_name]
    await rider_objects.data_tracker.add_points([point])

    status = point.get('rider_status', '')
    author = await get_git_author(request)
    await event.save(f'{event.name}: add point to {rider_name} - {status}', author=author)

    return web.Response(text=f'Added point to {rider_name}: {point}')


@say_error_handler
@event_handler
@ensure_authorized_event
async def event_add_rider_tracker(request, event):
    post = await request.post()
    rider_name = post['rider_name']
    tracker = post['tracker']
    tracker = yaml.safe_load(tracker)

    rider_objects = event.riders_objects[rider_name]

    start_tracker = event.app['start_event_trackers'][tracker['type']]
    start = tracker.get('start') or event.config['tracker_start'],
    end = tracker.get('end') or event.config['tracker_end'],
    source_tracker = await start_tracker(event.app, event, rider_name, tracker, start, end)

    rider_objects.source_trackers.append(source_tracker)
    await rider_objects.combined_tracker.append_sub_tracker(source_tracker)

    rider_config = first(filter(lambda rider: rider['name'] == rider_name, event.config['riders']))
    rider_config['trackers'].append(tracker)
    author = await get_git_author(request)
    await event.save(f'{event.name}: add tracker to {rider_name} - {tracker}', author=author)
    return web.Response(text=f'Added tracker to {rider_name}: {tracker}')


@say_error_handler
@event_handler
@ensure_authorized_event
async def event_config_edit(request, event):
    body = io.StringIO()
    writer = Writer(body)
    w = writer.w
    c = writer.c

    errors = []
    commited = False

    if request.method == 'POST':
        form = await request.post()
        tree = TreeWriter(request.app['trackers.data_repo'])
        _, sha = tree.lookup(event.config_path)

        if sha.decode() != form['sha']:
            errors.append(
                "Config has changed. Reload and reapply your change.")
            sha = form['sha'].encode()

        try:
            config = yaml.safe_load(form['data'])
            data = yaml.dump(config, default_flow_style=False,
                             Dumper=trackers.events.YamlEventDumper).encode()
        except Exception as e:
            errors.append(Tag('pre', c=str(e)))
            data = form['data'].encode()

        if not errors:
            tree.set_data(event.config_path, data)
            author = await get_git_author(request)
            tree.commit(f'{event.name}: web edit', author=author)
            commited = True
    else:
        tree = TreeReader(request.app['trackers.data_repo'])
        _, sha = tree.lookup(event.config_path)
        data = tree.lookup_obj(sha).data

    w(Markup('<!DOCTYPE html>'))
    with c(Tag('html')):
        with c(Tag('head')):
            w(Tag('title'), ('Edit', event.config['title']))
            w(Tag('meta', name="viewport", content="initial-scale=1.0, user-scalable=no"))
            w(Tag('link', rel="stylesheet",
                  href="https://cdnjs.cloudflare.com/ajax/libs/materialize/1.0.0/css/materialize.min.css"))
            w(Tag('script', src=r'//cdnjs.cloudflare.com/ajax/libs/ace/1.4.2/ace.js', c=''))

        with c(Tag('body')):
            with c(Tag('form', method="POST", id='form')):
                w(Tag('input', type='hidden', name='sha', value=sha.decode()))
                w(Tag('input', type='hidden', name='data',
                      id="data", value=data.decode()))
                with c(Tag('div', s_display="flex", s_flex_direction="column",
                           s_position='absolute', s_left='0', s_right='0', s_top='0', s_bottom='0', s_padding='8px', )):
                    with c(Tag('div', s_display="flex", class_='hide-on-med-and-down')):
                        w(Tag('h3', s_flex="1 1 80%"),
                          ('Edit - ', event.config['title']))
                        await show_identity(request, writer)

                    for error in errors:
                        w(Tag('div', class_="card-panel red white-text ", ), error)
                    if commited:
                        w(Tag('div', class_="card-panel teal white-text "),
                          'Change saved')

                    w(Tag('div', id='editor', s_flex='1 1 100%'))

                    with c(Tag('div', s_text_align="right")):
                        w(Tag('button', type='submit',
                              class_="btn waves-effect waves-light"), 'Save')
            w(Tag('script'), Markup('''
                var editor = ace.edit("editor");
                var data = document.getElementById('data');
                editor.session.setMode("ace/mode/yaml");
                editor.getSession().setValue(data.value);
                document.getElementById('form').onsubmit = function (){
                    data.value = editor.getSession().getValue();
                }
            '''))
    return web.Response(body=body.getvalue(), headers={'Content-Type': 'text/html; charset=utf-8', 'Cache-Control': 'no-cache'})
