import base64
import contextlib
import datetime
import hashlib
import json
import logging
import asyncio
from functools import partial

import aiohttp
import magic
import pkg_resources
from aiohttp import web, WSMsgType
from more_itertools import chunked
from slugify import slugify

import trackers
import trackers.events
import trackers.modules
import trackers.traccar

from trackers import cancel_and_wait_task

logger = logging.getLogger(__name__)

server_version = 10

async def make_aio_app(loop, settings):
    app = web.Application(loop=loop)
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
    app.router.add_route('GET', '/{event}/set_start', handler=event_set_start, name='event_set_start')



    app.router.add_route('POST', '/client_error', handler=client_error_logger, name='client_error_logger')

    app['trackers.ws_sessions'] = []

    app['trackers.modules_cm'] = modules_cm = await trackers.modules.config_modules(app, settings)
    await modules_cm.__aenter__()

    trackers.events.load_events(app, settings)
    for event_name in app['trackers.events_data']:
        await trackers.events.start_event_trackers(app, settings, event_name)

    app.on_shutdown.append(shutdown)


    return app


async def shutdown(app):
    for ws in app['trackers.ws_sessions']:
        await ws.close(code=aiohttp.WSCloseCode.GOING_AWAY,
                       message='Server shutdown')

    for event_name in app['trackers.events_data']:
        await trackers.events.stop_event_trackers(app, event_name)

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


async def event_ws(request):
    ws = web.WebSocketResponse()
    await ws.prepare(request)
    with contextlib.ExitStack() as exit_stack:
        try:
            exit_stack.enter_context(list_register(request.app['trackers.ws_sessions'], ws))

            event_name = request.match_info['event']
            event_data = request.app['trackers.events_data'].get(event_name)
            if event_data is None:
                await ws.close(message='Error: Event not found.')
                return ws

            trackers = request.app['trackers.events_rider_trackers'].get(event_name)

            def send(msg):
                logger.debug('send: {}'.format(str(msg)[:1000]))
                ws.send_str(json.dumps(msg, default=json_encode))

            exit_stack.enter_context(list_register(request.app['trackers.events_ws_sessions'][event_name], send))

            send({'client_hash': request.app['trackers.event_client_hash'], 'server_time': datetime.datetime.now()})

            async for msg in ws:
                if msg.tp == WSMsgType.text:
                    data = json.loads(msg.data)
                    logger.debug('receive: {}'.format(data))
                    resend = False
                    if 'event_data_version' in data:
                        resend = (
                            not data.get('event_data_version') or
                            not data.get('server_version') or
                            data['event_data_version'] != event_data['data_version'] or
                            data['server_version'] != server_version
                        )
                        if resend:
                            # TODO: massage data to remove stuff that is only approiate to server
                            send({'sending': 'event data'})
                            send({'event_data': event_data, 'server_version': server_version})
                    if 'rider_indexes' in data:
                        if resend:
                            send({'erase_rider_points': 1})
                            client_rider_point_indexes = {}
                        else:
                            client_rider_point_indexes = data['rider_indexes']
                        for rider in event_data['riders']:
                            rider_name = rider['name']
                            tracker = trackers.get(rider_name)
                            if tracker:
                                last_index = client_rider_point_indexes.get(rider_name, 0)
                                new_points = tracker.points[last_index:]
                                if new_points:
                                    await tracker_new_points_to_ws(send, rider_name, tracker, new_points)
                                client_rider_point_indexes[rider_name] = len(tracker.points)
                                exit_stack.enter_context(list_register(tracker.new_points_callbacks,
                                                                       partial(tracker_new_points_to_ws, send, rider_name)))


                if msg.tp == WSMsgType.close:
                    await ws.close()
                if msg.tp == WSMsgType.error:
                    raise ws.exception()
            return ws

        except Exception as e:
            await ws.close(message='Error: {}'.format(e))
            raise


point_keys = {
    'time': 't',
    'position': 'p',
    'track_id': 'i',
    'status': 's',
    'dist_route': 'o',
    'dist_ridden': 'd',
    'dist_from_last': 'l',
}


async def tracker_new_points_to_ws(ws_send, rider_name, tracker, new_points):
    try:
        for points in chunked(new_points, 100):
            if len(points) > 50:
                ws_send({'sending': rider_name if rider_name else 'Points'})
            compressed_points = [
                {point_keys.get(key, key): value for key, value in point.items()}
                for point in points
            ]
            if rider_name:
                ws_send({'rider_points': {'name': rider_name, 'points': compressed_points}})
            else:
                ws_send({'points': compressed_points})

    except Exception:
        logger.exception('Error in tracker_new_points_to_ws:')



def json_encode(obj):
    if isinstance(obj, datetime.datetime):
        return obj.timestamp()

async def client_error_logger(request):
    body = await request.text()
    body = body[:1024 * 1024]  # limit to 1kb
    agent = request.headers.get('User-Agent', '')
    peername = request.transport.get_extra_info('peername')
    forwared_for = request.headers.get('X-Forwarded-For')
    client = forwared_for or (peername[0] if peername else '')
    logger.error('\n'.join((body, agent, client)))
    return aiohttp.web.Response()


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
                ws.send_str(json.dumps(msg, default=json_encode))

            send({'client_hash': request.app['trackers.individual_client_hash'], 'server_time': datetime.datetime.now()})

            tracker_key = get_key(request)
            tracker_info = request.app['trackers.individual_trackers'].get(tracker_key)

            if tracker_info is None:
                tracker = await get_tracker(request)
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
                            await tracker_new_points_to_ws(send, None, tracker, new_points)
                        exit_stack.enter_context(list_register(tracker.new_points_callbacks,
                                                               partial(tracker_new_points_to_ws, send, None)))

                if msg.tp == WSMsgType.close:
                    await ws.close()
                if msg.tp == WSMsgType.error:
                    raise ws.exception()
            return ws

    except Exception as e:
        ws.send_str(json.dumps({'error': 'Error getting tracker: {}'.format(e)}, default=json_encode))
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

