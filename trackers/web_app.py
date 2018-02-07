import asyncio
import base64
import contextlib
import csv
import datetime
import hashlib
import io
import json
import logging
import re
from base64 import urlsafe_b64encode
from collections import defaultdict
from contextlib import suppress
from functools import partial, wraps

import pkg_resources
from aiohttp import web, WSCloseCode, WSMsgType
from more_itertools import chunked

import trackers.bin_utils
import trackers.events
from trackers.analyse import AnalyseTracker
from trackers.base import cancel_and_wait_task, list_register, Observable
from trackers.general import json_dumps
from trackers.web_helpers import (
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
    static_manager.add_resource('/static/richmarker.js', charset='utf8', content_type='text/javascript')
    static_manager.add_resource('/static/es7-shim.min.js', charset='utf8', content_type='text/javascript')
    static_manager.add_resource('/static/highcharts.js', charset='utf8', content_type='text/javascript')
    static_manager.add_resource('/static/highcharts.js.map', charset='utf8', content_type='text/javascript')
    static_manager.add_resource('/static/highcharts.src.js', charset='utf8', content_type='text/javascript')

    static_manager.add_resource('/static/traccar_testing.html', '/testing', charset='utf8', content_type='text/html')
    static_manager.add_resource('/static/event.html')  # This is just here so that we reload on change.
    static_manager.add_resource('/static/individual.html', route_name='individual_page',
                                charset='utf8', content_type='text/html',
                                body_processor=partial(
                                    page_body_processor,
                                    api_key=settings['google_api_key'],
                                ))

    static_manager.add_resource_dir('/static/markers')
    static_manager.add_resource_dir('/static/logos')

    static_manager.start_monitor_and_process_resources()

    app.router.add_route('GET', '/{event}', handler=event_page, name='event_page')
    app.router.add_route('GET', '/{event}/websocket', handler=event_ws, name='event_ws')
    app.router.add_route('GET', '/{event}/state', handler=event_state, name='event_state')
    app.router.add_route('GET', '/{event}/config', handler=event_config, name='event_config')
    app.router.add_route('GET', '/{event}/routes', handler=event_routes, name='event_routes')
    app.router.add_route('GET', '/{event}/riders_points', name='riders_points',
                         handler=partial(blocked_lists, blocked_list_attr_name='rider_trackers_blocked_list'))
    app.router.add_route('GET', '/{event}/riders_off_route', name='riders_off_route',
                         handler=partial(blocked_lists, blocked_list_attr_name='rider_off_route_blocked_list'))
    app.router.add_route('GET', '/{event}/riders_csv', name='riders_csv', handler=riders_csv)
    app.router.add_route('GET', '/{event}/set_start', handler=event_set_start, name='event_set_start')

    app.router.add_route('POST', '/client_error', handler=client_error_handler, name='client_error')

    app['trackers.app_setup_cm'] = app_setup_cm = await app_setup(app, settings)
    await app_setup_cm.__aenter__()

    app['load_events_with_watcher_task'] = asyncio.ensure_future(
        trackers.events.load_events_with_watcher(
            app,
            new_event_observable=Observable(logger=trackers.events.logger, callbacks=(on_new_event, )),
            removed_event_observable=Observable(logger=trackers.events.logger, callbacks=(on_removed_event, )),
        ))

    return app


async def shutdown(app):
    for ws in app['trackers.ws_sessions']:
        await ws.close(code=WSCloseCode.GOING_AWAY,
                       message='Server shutdown')

    await cancel_and_wait_task(app['load_events_with_watcher_task'])

    for event in app['trackers.events'].values():
        await event.stop_and_complete_trackers()

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
        await event.start_trackers()
        return await func(request, event, **kwargs)
    return event_handler_inner


def get_event_state(app, event):
    ensure_event_page(app, event)
    return {
        'live': event.config.get('live', False),
        'config_hash': event.config_hash,
        'routes_hash': event.routes_hash,
        'riders_values': event.rider_current_values,
        'client_hash': event.page[1],
        'server_time': datetime.datetime.now(),
    }


def ensure_event_page(app, event):
    if not hasattr(event, 'page'):
        page_path = event.config.get('page', '/static/event.html')
        event.page = app['static_manager'].get_static_processed_resource(
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
    ensure_event_page(request.app, event)
    body, etag = event.page
    response = web.Response(body=body, charset='utf8', content_type='text/html',)
    return etag_response(request, response, etag)


@say_error_handler
@event_handler
async def event_state(request, event):
    if event.config.get('live', False):
        state = {'live': True}
    else:
        state = get_event_state(request.app, event)

    response = json_response(state)
    etag = base64.urlsafe_b64encode(hashlib.sha1(response.body).digest()).decode('ascii')
    return etag_response(request, response, etag)


@say_error_handler
@event_handler
async def event_config(request, event):
    get_response = partial(json_response, event.config)
    return etag_query_hash_response(request, get_response, event.config_hash)


@say_error_handler
@event_handler
async def event_routes(request, event):
    remove_keys = {'original_points'}
    routes = [{key: value for key, value in route.items() if key not in remove_keys} for route in event.routes]
    get_response = partial(json_response, routes)
    return etag_query_hash_response(request, get_response, event.routes_hash)


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
async def blocked_lists(request, event, blocked_list_attr_name):
    rider_name = request.query.get('name')
    blocked_lists = getattr(event, blocked_list_attr_name)
    if not rider_name:
        hasher = hashlib.sha1()
        for blocked_list in blocked_lists.values():
            if blocked_list.source:
                hasher.update(blocked_list.source[-1]['hash'].encode())
        hash = urlsafe_b64encode(hasher.digest()[:3]).decode('ascii')
        blocked_lists_full = {rider_name: list.full for rider_name, list in blocked_lists.items()}
        return etag_response(request, partial(json_response, blocked_lists_full), hash)
    else:
        start_index = int(request.query.get('start_index'))
        end_index = int(request.query.get('end_index'))
        end_hash = request.query.get('end_hash')

        points = blocked_lists[rider_name].source[start_index:end_index + 1]
        if points and points[-1]['hash'] != end_hash:
            raise web.HTTPInternalServerError(text='Wrong end_hash')

        return etag_response(request, json_response(points), end_hash,
                             cache_control=immutable_cache_control)


@say_error_handler
@event_handler
async def riders_csv(request, event):
    rider_name = request.query.get('name')
    tracker = event.rider_trackers[rider_name]

    out_file = io.StringIO()
    writer = csv.writer(out_file)

    writer.writerow(['latitude', 'longitude', 'time'])
    for point in tracker.points:
        if 'position' in point:
            writer.writerow((point['position'][0], point['position'][1], point['time'].isoformat()))
    return etag_response(request, web.Response(text=out_file.getvalue(), content_type='text/csv'),
                         etag=tracker.points[-1]['hash'])


async def on_static_processed(static_manager):
    app = static_manager.app
    for event in app.get('trackers.events', {}).values():
        with suppress(AttributeError):
            del event.page
        event_wss = event.app['trackers.event_ws_sessions'][event.name]
        if event_wss:
            ensure_event_page(app, event)
            message_to_multiple_wss(
                event.app,
                event_wss,
                {'client_hash': event.page[1]}
            )


async def on_new_event(event):
    event.config_routes_change_observable.subscribe(on_event_config_routes_change)
    event.rider_new_points_observable.subscribe(on_event_rider_new_points)
    event.rider_blocked_list_update_observable.subscribe(on_event_rider_blocked_list_update)
    event.rider_off_route_blocked_list_update_observable.subscribe(on_event_rider_off_route_blocked_list_update)
    event.rider_predicted_updated_observable.subscribe(on_event_rider_predicted_updated)

    if event.config.get('live', False):
        await event.start_trackers()


async def on_removed_event(event):
    pass


async def on_event_config_routes_change(event):
    message_to_multiple_wss(
        event.app,
        event.app['trackers.event_ws_sessions'][event.name],
        {
            'live': event.config.get('live', False),
            'config_hash': event.config_hash,
            'routes_hash': event.routes_hash,
        },
    )
    if event.config.get('live', False):
        await event.start_trackers()


async def on_event_rider_new_points(event, rider_name, tracker, new_points):
    message_to_multiple_wss(
        event.app,
        event.app['trackers.event_ws_sessions'][event.name],
        {'riders_values': {rider_name: event.rider_current_values[rider_name]}}
    )


async def on_event_rider_blocked_list_update(event, rider_name, blocked_list, update):
    event_wss = event.app['trackers.event_ws_sessions'][event.name]
    message_to_multiple_wss(
        event.app,
        event_wss,
        {'riders_points': {rider_name: update}},
        filter_ws=lambda ws: 'riders_points' in ws.subscriptions or f'riders_points.{rider_name}' in ws.subscriptions,
    )


async def on_event_rider_off_route_blocked_list_update(event, rider_name, blocked_list, update):
    message_to_multiple_wss(
        event.app,
        event.app['trackers.event_ws_sessions'][event.name],
        {'riders_off_route': {rider_name: update}},
        filter_ws=lambda ws: 'riders_off_route' in ws.subscriptions,
    )


async def on_event_rider_predicted_updated(event, predicted, time):
    message_to_multiple_wss(
        event.app,
        event.app['trackers.event_ws_sessions'][event.name],
        {'riders_predicted': predicted, },
        filter_ws=lambda ws: 'riders_predicted' in ws.subscriptions,
    )


async def event_ws(request):
    ws = web.WebSocketResponse()
    ws.subscriptions = set()
    await ws.prepare(request)
    with contextlib.ExitStack() as exit_stack:
        try:
            exit_stack.enter_context(list_register(request.app['trackers.ws_sessions'], ws))

            send = partial(message_to_multiple_wss, request.app, (ws, ))
            send({})

            event_name = request.match_info['event']
            event = request.app['trackers.events'].get(event_name)
            if event is None:
                await ws.close(message='Error: Event not found.')
                return ws

            if not event.config.get('live', False):
                send({'live': False})
                ws.close(message='Event not live. Use rest api')
                return ws

            await event.start_trackers()

            send(get_event_state(request.app, event))
            exit_stack.enter_context(list_register(request.app['trackers.event_ws_sessions'][event_name], ws))

            async for msg in ws:
                if msg.tp == WSMsgType.text:
                    try:
                        logger.debug('receive: {}'.format(msg.data))
                        data = json.loads(msg.data)
                        if 'subscriptions' in data:
                            old_subscriptions = ws.subscriptions
                            ws.subscriptions = set(data['subscriptions'])
                            added_subscriptions = ws.subscriptions - old_subscriptions
                            if 'riders_points' in added_subscriptions:
                                send({'riders_points': {rider_name: list.full for rider_name, list in event.rider_trackers_blocked_list.items()}})
                            else:
                                selected_rider_points = {rider_name: list.full
                                                         for rider_name, list in event.rider_trackers_blocked_list.items()
                                                         if f'riders_points.{rider_name}' in added_subscriptions}
                                if selected_rider_points:
                                    send({'riders_points': selected_rider_points})

                            if 'riders_off_route' in added_subscriptions:
                                send({'riders_off_route': {rider_name: list.full for rider_name, list in event.rider_off_route_blocked_list.items()}})

                            if 'riders_predicted' in added_subscriptions:
                                send({'riders_predicted': event.riders_predicted_points})

                    except Exception:
                        logger.exception('Error in receive ws msg:')

                if msg.tp == WSMsgType.close:
                    await ws.close()
                if msg.tp == WSMsgType.error:
                    raise ws.exception()
        except asyncio.CancelledError:
            pass
        except Exception as e:
            request.app['exception_recorder']()
            await ws.close(message='Server Error: {}'.format(e))
            logger.exception('Error in event_ws: ')
        finally:
            return ws


def message_to_multiple_wss(app, wss, msg, log_level=logging.DEBUG, filter_ws=None):
    msg = json_dumps(msg)
    logger.log(log_level, 'send: {}'.format(msg[:1000]))
    for ws in wss:
        send = filter_ws(ws) if filter_ws else True
        if send:
            try:
                ws.send_str(msg)
            except Exception:
                app['exception_recorder']()
                logger.exception('Error sending msg to ws:')


@say_error_handler
@event_handler
async def event_set_start(request, event):
    event.config['event_start'] = datetime.datetime.now()
    event.save("Set event start")
    return web.Response(text='Start time set to {}'.format(event.config['event_start']))


async def individual_page(request):
    return await request.app['static_manager'].resource_handler('individual_page', request)


async def individual_ws(get_key, get_tracker, request):
    ws = web.WebSocketResponse()
    await ws.prepare(request)
    try:
        with contextlib.ExitStack() as exit_stack:
            def send(msg):
                logger.debug('send: {}'.format(str(msg)[:1000]))
                ws.send_str(json_dumps(msg))
            send({'client_hash': request.app['static_manager'].processed_resources['individual_page'].hash,
                  'server_time': datetime.datetime.now()})

            tracker_key = get_key(request)
            tracker_info = request.app['trackers.individual_trackers'].get(tracker_key)

            if tracker_info is None:
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
                if tracker_info['discard_task']:
                    cancel_and_wait_task(tracker_info['discard_task'])
                    tracker_info['discard_task'] = None

                tracker = tracker_info['tracker']

            exit_stack.enter_context(list_register(request.app['trackers.ws_sessions'], ws))
            exit_stack.enter_context(list_register(tracker_info['ws_sessions'], ws))

            async for msg in ws:
                if msg.tp == WSMsgType.text:
                    data = json.loads(msg.data)
                    logger.debug('receive: {}'.format(data))
                    resend = False
                    if 'send_points_since' in data:
                        if resend:
                            send({'erase_points': 1})
                            client_point_indexes = 0
                        else:
                            client_point_indexes = data['send_points_since']

                        last_index = client_point_indexes
                        new_points = tracker.points[last_index:]
                        if new_points:
                            await individual_tracker_new_points_to_ws(request.app, send, tracker, new_points)
                        exit_stack.enter_context(list_register(tracker.new_points_observable.callbacks,
                                                               partial(individual_tracker_new_points_to_ws, request.app, send)))

                if msg.tp == WSMsgType.close:
                    await ws.close()
                if msg.tp == WSMsgType.error:
                    raise ws.exception()
            return ws
    except asyncio.CancelledError:
        pass
    except Exception as e:
        request.app['exception_recorder']()
        ws.send_str(json_dumps({'error': 'Error getting tracker: {}'.format(e)}))
        logger.exception('')
        await ws.close(message='Server Error')
    return ws


def start_individual_discard_tracker_wait(app, tracker_info):
    tracker_info['discard_task'] = asyncio.ensure_future(individual_discard_tracker_wait(app, tracker_info))


async def individual_discard_tracker_wait(app, tracker_info):
    await asyncio.sleep(3600)
    await asyncio.shield(individual_discard_tracker(app, tracker_info))


async def individual_discard_tracker(app, tracker_info):
    try:
        tracker = tracker_info['tracker']
        tracker.stop()
        await tracker.complete()
    finally:
        del app['trackers.individual_trackers'][tracker_info['key']]


async def individual_tracker_new_points_to_ws(app, ws_send, tracker, new_points):
    try:
        for points in chunked(new_points, 100):
            if len(points) > 50:
                ws_send({'sending': 'Points'})
            compressed_points = [
                {point_keys.get(key, key): value for key, value in point.items()}
                for point in points
            ]
            ws_send({'points': compressed_points})

    except Exception:
        app['exception_recorder']()
        logger.exception('Error in individual_tracker_new_points_to_ws:')


client_url_re = re.compile('https?://.*?/static/(?P<path>.*?)(\?hash=.*?)?(?P<term>[:\s])')


def convert_client_urls_to_paths(static_path, s):
    return client_url_re.sub(f'\n{static_path}/\g<path>\g<term>', s)
