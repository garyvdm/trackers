import asyncio
import base64
import contextlib
import datetime
import hashlib
import json
import logging
from functools import partial, wraps

import magic
import pkg_resources
from aiohttp import web, WSCloseCode, WSMsgType
from more_itertools import chunked
from slugify import slugify

import trackers.bin_utils
import trackers.events
import trackers.modules
from trackers.analyse import start_analyse_tracker
from trackers.base import cancel_and_wait_task, list_register
from trackers.general import json_dumps

logger = logging.getLogger(__name__)

server_version = 10
immutable_cache_control = 'public,max-age=31536000,immutable'
mutable_cache_control = 'public'


async def make_aio_app(loop, settings):
    app = web.Application(loop=loop)
    app.on_shutdown.append(shutdown)

    app['trackers.settings'] = settings

    app['trackers.ws_sessions'] = []
    static_urls = {}
    app['trackers.individual_trackers'] = {}

    def page_body_processor(app, body, related_resources, hash_key):
        hash = hashlib.sha1(body)
        for resource_name in related_resources:
            hash.update(pkg_resources.resource_string('trackers', resource_name))
        hash.update(settings['google_api_key'].encode('utf8'))
        for key, value in static_urls.items():
            hash.update(key.encode('utf8'))
            hash.update(value.encode('utf8'))

        client_hash = base64.urlsafe_b64encode(hash.digest()).decode('ascii')
        app[hash_key] = client_hash
        return body.decode('utf8').format(api_key=settings['google_api_key'],
                                          client_hash=client_hash,
                                          static_urls=static_urls).encode('utf8')

    with magic.Magic(flags=magic.MAGIC_MIME_TYPE) as m:
        add_static = partial(add_static_resource, app, 'trackers', static_urls, m)
        add_static('/static/event.css', '/static/event.css', charset='utf8', content_type='text/css')
        add_static('/static/event.js', '/static/event.js', charset='utf8', content_type='text/javascript')
        add_static('/static/individual.js', '/static/individual.js', charset='utf8', content_type='text/javascript')
        add_static('/static/richmarker.js', '/static/richmarker.js', charset='utf8', content_type='text/javascript')
        add_static('/static/es7-shim.min.js', '/static/es7-shim.min.js', charset='utf8', content_type='text/javascript')
        add_static('/static/highcharts.js', '/static/highcharts.js', charset='utf8', content_type='text/javascript')
        add_static('/static/traccar_testing.html', '/testing', charset='utf8', content_type='text/html')
        # print(list(static_urls.keys()))
        add_static('/static/event.html', '/{event}', charset='utf8', content_type='text/html',
                   body_processor=partial(
                       page_body_processor,
                       related_resources=('/static/event.js', '/static/event.css', '/static/richmarker.js',),
                       hash_key='trackers.event_client_hash'
                   ))
        app['add_individual_handler'] = partial(
            add_static, '/static/individual.html',
            charset='utf8', content_type='text/html',
            body_processor=partial(
                page_body_processor,
                related_resources=('/static/individual.js', '/static/event.css'),
                hash_key='trackers.individual_client_hash'
            )
        )

        for name in pkg_resources.resource_listdir('trackers', '/static/markers'):
            full_name = '/static/markers/{}'.format(name)
            add_static(full_name, full_name)

    app.router.add_route('GET', '/{event}/websocket', handler=event_ws, name='event_ws')
    app.router.add_route('GET', '/{event}/state', handler=event_state, name='event_state')
    app.router.add_route('GET', '/{event}/config', handler=event_config, name='event_config')
    app.router.add_route('GET', '/{event}/routes', handler=event_routes, name='event_routes')
    app.router.add_route('GET', '/{event}/rider_points', handler=rider_points, name='rider_points')

    app.router.add_route('GET', '/{event}/set_start', handler=event_set_start, name='event_set_start')

    app.router.add_route('POST', '/client_error', handler=client_error_logger, name='client_error_logger')

    app['trackers.app_setup_cm'] = app_setup_cm = await trackers.bin_utils.app_setup(app, settings)
    await app_setup_cm.__aenter__()

    trackers.events.load_events(app, settings)
    for event in app['trackers.events'].values():
        await event.start_trackers(app)

    return app


async def shutdown(app):
    for ws in app['trackers.ws_sessions']:
        await ws.close(code=WSCloseCode.GOING_AWAY,
                       message='Server shutdown')

    for event in app['trackers.events'].values():
        await event.stop_trackers()

    await app['trackers.app_setup_cm'].__aexit__(None, None, None)


def etag_response(request, response, etag, cache_control=None):
    if cache_control is None:
        cache_control = mutable_cache_control
    headers = {'ETag': etag, 'Cache-Control': cache_control}
    if request.headers.get('If-None-Match', '') == etag:
        return web.Response(status=304, headers=headers)
    else:
        if callable(response):
            response = response()
        response.headers.update(headers)
        return response


def etag_query_hash_response(request, response, etag):
    query_hash = request.query.get('hash')
    if query_hash and query_hash != etag:
        # Redirect to same url with correct hash query
        return web.HTTPFound(request.app.router[request.match_info.route.name]
                             .url_for(**request.match_info).with_query({'hash': etag}))
    else:
        cache_control = immutable_cache_control if query_hash else mutable_cache_control
        return etag_response(request, response, etag, cache_control=cache_control)


def json_response(data, **kwargs):
    return web.Response(text=json_dumps(data), content_type='application/json', **kwargs)


def add_static_resource(app, package, static_urls, magic, resource_name, route, *args, **kwargs):
    body = pkg_resources.resource_string(package, resource_name)
    body_processor = kwargs.pop('body_processor', None)
    if body_processor:
        body = body_processor(app, body)
    if 'content_type' not in kwargs:
        kwargs['content_type'] = magic.id_buffer(body)
    kwargs['body'] = body
    etag = base64.urlsafe_b64encode(hashlib.sha1(body).digest()).decode('ascii')

    async def static_resource_handler(request):
        return etag_query_hash_response(request, lambda: web.Response(*args, **kwargs), etag)

    route_name = slugify(resource_name)
    if route:
        app.router.add_route('GET', route, static_resource_handler, name=route_name)

    try:
        static_urls[route_name] = str(app.router[route_name].url_for().with_query({'hash': etag}))
    except KeyError:
        pass  # for routes with match items

    return static_resource_handler


def say_error_handler(func):
    @wraps(func)
    async def say_error_handler_inner(request):
        try:
            return await func(request)
        except web.HTTPError:
            raise
        except Exception as e:
            logger.exception('')
            message = getattr(e, 'message', None)
            if not message:
                message = '{}: {}'.format(type(e).__name__, e)
            return web.HTTPInternalServerError(text=message)
    return say_error_handler_inner


def event_handler(func):
    @wraps(func)
    async def event_handler_inner(request):
        event_name = request.match_info['event']
        event = request.app['trackers.events'].get(event_name)
        if event is None:
            raise web.HTTPNotFound()
        return await func(request, event)
    return event_handler_inner


def get_event_state(event):
    riders_points = {rider_name: blocked_list.full for rider_name, blocked_list in event.rider_trackers_blocked_list.items()}

    return {
        'live': event.config.get('live', False),
        'config_hash': event.config_hash,
        'routes_hash': event.routes_hash,
        'riders_points': riders_points,
    }


@say_error_handler
@event_handler
async def event_state(request, event):
    if event.config.get('live', False):
        state = {'live': True}
    else:
        state = get_event_state(event)

    response = json_response(state)
    etag = base64.urlsafe_b64encode(hashlib.sha1(response.body).digest()).decode('ascii')
    return etag_response(request, response, etag, cache_control='public')


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
async def rider_points(request, event):
    rider_name = request.query.get('name')
    start_index = int(request.query.get('start_index'))
    end_index = int(request.query.get('end_index'))
    end_hash = request.query.get('end_hash')

    points = event.rider_trackers[rider_name].points[start_index:end_index + 1]
    if points and points[-1]['hash'] != end_hash:
        raise web.HTTPInternalServerError(text='Wrong end_hash')

    return etag_response(request, json_response(points), end_hash,
                         cache_control=immutable_cache_control)


async def event_ws(request):
    ws = web.WebSocketResponse()
    await ws.prepare(request)
    with contextlib.ExitStack() as exit_stack:
        try:
            exit_stack.enter_context(list_register(request.app['trackers.ws_sessions'], ws))

            def send(data):
                msg = json_dumps(data)
                logger.debug('send: {}'.format(msg[:1000]))
                ws.send_str(msg)

            event_name = request.match_info['event']
            event = request.app['trackers.events'].get(event_name)
            if event is None:
                await ws.close(message='Error: Event not found.')
                return ws

            live = event.config.get('live', False)
            send({'client_hash': request.app['trackers.event_client_hash'], 'server_time': datetime.datetime.now()})

            if not live:
                send({'live': False})
                ws.close(message='Event not live. Use rest api')

            else:
                send(get_event_state(event))
                for rider_name, tracker_block_list in event.rider_trackers_blocked_list.items():
                    exit_stack.enter_context(list_register(tracker_block_list.new_update_callbacks,
                                                           partial(tracker_updates_to_ws, send, rider_name)))

            async for msg in ws:
                if msg.tp == WSMsgType.text:
                    logger.debug('receive: {}'.format(msg.data))
                    # We used to do stuff here, but now we just send stuff. Will do subscribe stuff later
                    # Maybe we want to switch to SSE

                if msg.tp == WSMsgType.close:
                    await ws.close()
                if msg.tp == WSMsgType.error:
                    raise ws.exception()
            return ws

        except Exception as e:
            await ws.close(message='Server Error: {}'.format(e))
            raise


async def tracker_updates_to_ws(ws_send, rider_name, update):
    try:
        if update:
            ws_send({'riders_points': {rider_name: update}})
    except Exception:
        logger.exception('Error in tracker_new_points_to_ws:')


async def client_error_logger(request):
    body = await request.text()
    body = body[:1024 * 1024]  # limit to 1kb
    agent = request.headers.get('User-Agent', '')
    peername = request.transport.get_extra_info('peername')
    forwared_for = request.headers.get('X-Forwarded-For')
    client = forwared_for or (peername[0] if peername else '')
    logger.error('\n'.join((body, agent, client)))
    return web.Response()


async def event_set_start(request):
    event_name = request.match_info['event']
    event_data = request.app['trackers.events_data'].get(event_name)
    event_data['event_start'] = datetime.datetime.now()
    trackers.events.save_event(request.app, request.app['trackers.settings'], event_name)

    for send in request.app['trackers.events_ws_sessions'][event_name]:
        send({'sending': 'event data'})
        send({'event_data': event_data, 'server_version': server_version})

    return web.Response(text='Start time set to {}'.format(event_data['event_start']))


async def individual_ws(get_key, get_tracker, request):
    ws = web.WebSocketResponse()
    await ws.prepare(request)
    try:
        with contextlib.ExitStack() as exit_stack:
            def send(msg):
                logger.debug('send: {}'.format(str(msg)[:1000]))
                ws.send_str(json_dumps(msg))

            send({'client_hash': request.app['trackers.individual_client_hash'], 'server_time': datetime.datetime.now()})

            tracker_key = get_key(request)
            tracker_info = request.app['trackers.individual_trackers'].get(tracker_key)

            if tracker_info is None:
                tracker = await get_tracker(request)
                tracker = await start_analyse_tracker(tracker, None, ())
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
                            await individual_tracker_new_points_to_ws(send, tracker, new_points)
                        exit_stack.enter_context(list_register(tracker.new_points_callbacks,
                                                               partial(individual_tracker_new_points_to_ws, send)))

                if msg.tp == WSMsgType.close:
                    await ws.close()
                if msg.tp == WSMsgType.error:
                    raise ws.exception()
            return ws
    except Exception as e:
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
        await tracker.stop()
        await tracker.finish()
    finally:
        del app['trackers.individual_trackers'][tracker_info['key']]


async def individual_tracker_new_points_to_ws(ws_send, tracker, new_points):
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
        logger.exception('Error in individual_tracker_new_points_to_ws:')
