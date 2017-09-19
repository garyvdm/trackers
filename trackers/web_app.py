import asyncio
import base64
import contextlib
import datetime
import hashlib
import json
import logging
from copy import copy
from functools import partial, wraps

import magic
import pkg_resources
from aiohttp import web, WSCloseCode, WSMsgType
from more_itertools import chunked
from slugify import slugify

import trackers
import trackers.events
import trackers.modules
import trackers.traccar

from trackers.analyse import start_analyse_tracker
from trackers.base import cancel_and_wait_task
from trackers.general import json_dumps

logger = logging.getLogger(__name__)

server_version = 10


async def make_aio_app(loop, settings):
    app = web.Application(loop=loop)
    app['trackers.ws_sessions'] = []
    app.on_shutdown.append(shutdown)

    app['trackers.settings'] = settings

    app['trackers.static_etags'] = static_etags = {}
    app['trackers.individual_trackers'] = {}

    def page_body_processor(app, body, related_resources, hash_key):
        hash = hashlib.sha1(body)
        for resource_name in related_resources:
            hash.update(pkg_resources.resource_string('trackers', resource_name))
        client_hash = base64.urlsafe_b64encode(hash.digest()).decode('ascii')
        app[hash_key] = client_hash
        return body.decode('utf8').format(api_key=settings['google_api_key'], client_hash=client_hash).encode('utf8')

    with magic.Magic(flags=magic.MAGIC_MIME_TYPE) as m:
        add_static = partial(add_static_resource, app, 'trackers', static_etags, m)
        add_static('/static/event.css', '/static/event.css', charset='utf8', content_type='text/css')
        add_static('/static/event.js', '/static/event.js', charset='utf8', content_type='text/javascript')
        add_static('/static/individual.js', '/static/individual.js', charset='utf8', content_type='text/javascript')
        add_static('/static/richmarker.js', '/static/richmarker.js', charset='utf8', content_type='text/javascript')
        add_static('/static/es7-shim.min.js', '/static/es7-shim.min.js', charset='utf8', content_type='text/javascript')
        add_static('/static/traccar_testing.html', '/testing', charset='utf8', content_type='text/html')
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
    app.router.add_route('GET', '/{event}/initial_data', handler=event_initial_data, name='event_initial_data')
    app.router.add_route('GET', '/{event}/routes', handler=event_routes, name='event_routes')
    app.router.add_route('GET', '/{event}/rider_points', handler=rider_points, name='rider_points')

    app.router.add_route('GET', '/{event}/set_start', handler=event_set_start, name='event_set_start')

    app.router.add_route('POST', '/client_error', handler=client_error_logger, name='client_error_logger')

    app['trackers.modules_cm'] = modules_cm = await trackers.modules.config_modules(app, settings)
    await modules_cm.__aenter__()

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

    await app['trackers.modules_cm'].__aexit__(None, None, None)


def add_static_resource(app, package, etags, magic, resource_name, route, *args, **kwargs):
    body = pkg_resources.resource_string(package, resource_name)
    body_processor = kwargs.pop('body_processor', None)
    if body_processor:
        body = body_processor(app, body)
    if 'content_type' not in kwargs:
        kwargs['content_type'] = magic.id_buffer(body)
    kwargs['body'] = body
    headers = kwargs.setdefault('headers', {})
    etag = base64.urlsafe_b64encode(hashlib.sha1(body).digest()).decode('ascii')
    headers['ETag'] = etag
    headers['Cache-Control'] = 'public'

    etags[slugify(resource_name)] = etag

    async def static_resource_handler(request):
        if request.headers.get('If-None-Match', '') == etag:
            return web.Response(status=304, headers=headers)
        else:
            # TODO check etag query string
            return web.Response(*args, **kwargs)
    if route:
        app.router.add_route('GET', route, static_resource_handler, name=slugify(resource_name))
    return static_resource_handler


@contextlib.contextmanager
def list_register(list, item, on_empty=None):
    list.append(item)
    try:
        yield
    finally:
        list.remove(item)
        if not list and on_empty:
            on_empty()


def say_error_handler(func):
    @wraps(func)
    async def say_error_handler_inner(request):
        try:
            return await func(request)
        except web.HTTPError:
            raise
        except Exception as e:
            logger.exception('')
            return web.HTTPInternalServerError(text=str(e))
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


def get_list_update(source, existing, block_len=50, compress_item=None):
    if not source:
        return {'empty': True}, []

    good_existing = []
    len_source = len(source)
    for item in existing:
        if item['index'] > len_source or source[item['index']]['hash'] != item['hash']:
            break
        good_existing.append(item)

    last_block_end_index = (len_source // block_len * block_len) - 1

    # Drop any partial blocks that should be in full block
    if good_existing and good_existing[-1]['index'] < last_block_end_index:
        existing_last_block_end_index = ((good_existing[-1]['index'] + 1) // block_len * block_len) - 1
        if existing_last_block_end_index < good_existing[-1]['index']:
            good_existing.pop()

    first_block_start_index = good_existing[-1]['index'] + 1 if good_existing else 0

    full_blocks = [{'start_index': start_index,
                    'end_index': start_index + block_len - 1,
                    'end_hash': source[start_index + block_len - 1]['hash']}
                   for start_index in range(first_block_start_index, last_block_end_index, block_len)]

    partial_block_start_index = max(last_block_end_index + 1, first_block_start_index)
    partial_block = [item for item in source[partial_block_start_index:]]

    new_existing = copy(good_existing)
    if partial_block and new_existing and new_existing[-1]['index'] > last_block_end_index:
        new_existing.pop()
    new_existing += [{'index': block['end_index'], 'hash': block['end_hash']} for block in full_blocks]
    if partial_block:
        item = partial_block[-1]
        new_existing += [{'index': item['index'], 'hash': item['hash']}]

    update = {}
    if full_blocks:
        update['full_blocks'] = full_blocks
    if partial_block:
        update['partial_block'] = [compress_item(item) for item in partial_block]
    return update, new_existing


def get_list_update_full_block(source):
    if source:
        return {'full_blocks': [{'start_index': 0, 'end_index': source[-1]['index'], 'end_hash': source[-1]['hash'], }]}
    else:
        return {'empty': True}


@say_error_handler
@event_handler
async def event_initial_data(request, event):
    is_live = event.data.get('live', False)

    if is_live:
        get_update = lambda points: get_list_update(points, (), compress_item=compress_point)[0]  # NOQA: E731
    else:
        get_update = get_list_update_full_block
    riders_points = {rider_name: get_update(tracker.points) for rider_name, tracker in event.rider_trackers.items()}

    initial_data = {
        'live': is_live,
        'event_data': event.data,
        'event_data_hash': event.data_hash,
        'routes_hash': event.routes_hash,
        'riders_points': riders_points,
    }

    response = web.Response(text=json_dumps(initial_data), content_type='application/json')
    etag = base64.urlsafe_b64encode(hashlib.sha1(response.body).digest()).decode('ascii')
    headers = {'ETag': etag, 'Cache-Control': 'public'}
    if request.headers.get('If-None-Match', '') == etag:
        return web.Response(status=304, headers=headers)
    else:
        response.headers.update(headers)
    return response


@say_error_handler
@event_handler
async def event_routes(request, event):
    etag = event.routes_hash
    request_etag = request.query.get('hash')
    if request_etag and request_etag != etag:
        return web.HTTPFound(request.app.router['event_routes'].url_for(event=event.name).with_query({'hash': etag}))

    headers = {'ETag': etag, 'Cache-Control': 'public, max-age={}'.format(31536000 if request_etag else 60)}

    if request.headers.get('If-None-Match', '') == etag:
        return web.Response(status=304, headers=headers)
    else:
        return web.Response(text=json_dumps(event.routes), headers=headers, content_type='application/json')


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
    if points or points[-1]['hash'] != end_hash:
        raise web.HTTPInternalServerError(text='Wrong end_hash')

    headers = {'ETag': end_hash, 'Cache-Control': 'public, max-age=31536000'}

    if request.headers.get('If-None-Match', '') == end_hash:
        return web.Response(status=304, headers=headers)
    else:
        return web.Response(text=json_dumps([compress_point(point) for point in points]),
                            headers=headers, content_type='application/json')


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

            live = event.data.get('live', False)
            send({'client_hash': request.app['trackers.event_client_hash'], 'server_time': datetime.datetime.now(),
                  'live': live})

            if not live:
                ws.close(message='Event not live. Use rest api')

            exit_stack.enter_context(list_register(event.ws_sessions, send))
            state_riders_points = {}

            async for msg in ws:
                if msg.tp == WSMsgType.text:
                    logger.debug('receive: {}'.format(msg.data))
                    if live:
                        data = json.loads(msg.data)
                        response = {}
                        if 'event_data_hash' in data and data['event_data_hash'] != event.data_hash:
                            response['event_data'] = event.data
                            response['event_data_hash'] = event.data_hash
                        if 'routes_hash' in data and data['routes_hash'] != event.routes_hash:
                            response['routes_hash'] = event.routes_hash
                        if 'riders_points' in data:
                            for rider_name, tracker in event.rider_trackers.items():
                                state_riders_points[rider_name] = data['riders_points'].get(rider_name, ())
                                new_points_call = partial(tracker_new_points_to_ws, send, rider_name, state_riders_points)
                                await new_points_call(tracker, tracker.points)
                                exit_stack.enter_context(list_register(tracker.new_points_callbacks, new_points_call))

                        if response:
                            send(response)

                if msg.tp == WSMsgType.close:
                    await ws.close()
                if msg.tp == WSMsgType.error:
                    raise ws.exception()
            return ws

        except Exception as e:
            await ws.close(message='Server Error: {}'.format(e))
            raise


async def tracker_new_points_to_ws(ws_send, rider_name, state_riders_points, tracker, new_points):
    try:
        existing = state_riders_points.get(rider_name, ())
        update, new_existing = get_list_update(tracker.points, existing, compress_item=compress_point)
        if update:
            state_riders_points[rider_name] = new_existing
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
