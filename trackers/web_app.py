import logging
import hashlib
import json
import pkg_resources
import os
import base64
import asyncio
import datetime
from functools import partial

import magic
import aiohttp
from aiohttp import web, WSMsgType
from slugify import slugify

import trackers.garmin_livetrack
import trackers.map_my_tracks
import trackers.modules
import trackers.events


async def make_aio_app(loop, settings):
    app = web.Application(loop=loop)
    app['trackers.settings'] = settings

    app['trackers.static_etags'] = static_etags = {}

    with magic.Magic(flags=magic.MAGIC_MIME_TYPE) as m:
        add_static = partial(add_static_resource, app, 'trackers', static_etags, m)
        add_static('/static/event.js', '/static/event.js', charset='utf8', content_type='application/javascript')
        add_static('/static/event.html', '/{event}', charset='utf8', content_type='text/html',
                   body_processor=lambda app, body: body.decode('utf8').format(api_key=settings['google_api_key']).encode('utf8'))
        for name in pkg_resources.resource_listdir('trackers', '/static/markers'):
            full_name = '/static/markers/{}'.format(name)
            add_static(full_name, full_name)

    app.router.add_route('GET', '/{event}/websocket', handler=event_ws, name='event_ws')

    app['trackers.ws_sessions'] = []

    app['trackers.modules_cm'] = modules_cm = await trackers.modules.config_modules(app, settings)
    await modules_cm.__aenter__()

    trackers.events.load_events(app, settings)
    for event_name in app['trackers.events_data']:
        await trackers.events.start_event_trackers(app, settings, event_name)

    app.on_shutdown.append(shutdown)

    return app


async def shutdown(app):
    for task in app['trackers.tracker_tasks']:
        task.cancel()
    for task in app['trackers.tracker_tasks']:
        try:
            await task
        except Exception:
            pass
    await app['trackers.modules_cm'].__aexit__(None, None, None)

    for ws in app['trackers.ws_sessions']:
        await ws.close(code=aiohttp.WSCloseCode.GOING_AWAY,
                       message='Server shutdown')



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
    headers['Cache-Control'] = 'public, max-age=31536000'

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


async def event_ws(request):
    ws = web.WebSocketResponse()
    await ws.prepare(request)

    ws_sessions = request.app['trackers.ws_sessions']
    ws_sessions.append(ws)
    try:
        event_name = request.match_info['event']
        event_data = request.app['trackers.events_data'].get(event_name)
        if event_data is None:
            await ws.close(message='Error: Event not found.')
            return ws

        trackers = request.app['trackers.events_rider_trackers'].get(event_name)
        client_rider_point_indexes = {}

        send = lambda msg: ws.send_str(json.dumps(msg, default=json_encode))

        static_etags = request.app['trackers.static_etags']
        send({'client_etags': [
            ('', static_etags['static-event-html']),
            ('/static/event.js', static_etags['static-event-js'])
        ]})

        async for msg in ws:
            if msg.tp == WSMsgType.text:
                data = json.loads(msg.data)
                logging.debug(data)
                if 'event_data_version' in data:
                    if not data['event_data_version'] or data['event_data_version'] != event_data['data_version']:
                        # TODO: massage data to remove stuff that is only approiate to server
                        send({'sending': 'event data'})
                        send({'event_data': event_data})
                if 'rider_indexes' in data:
                    if not data['event_data_version'] or data['event_data_version'] != event_data['data_version']:
                        send({'erase_rider_points': 1})
                        client_rider_point_indexes = {}
                    else:
                        client_rider_point_indexes = data['rider_indexes']
                    for rider in event_data['riders']:
                        rider_name = rider['name']
                        tracker = trackers[rider_name]
                        last_index = client_rider_point_indexes.get(rider_name, 0)
                        new_points = tracker.points[last_index:]
                        if new_points:
                            if len(new_points) > 100:
                                send({'sending': rider_name})
                            send({'rider_points': {'name': rider_name, 'points': new_points}})
                        client_rider_point_indexes[rider_name] = len(tracker.points)


            if msg.tp == WSMsgType.close:
                await ws.close()
            if msg.tp == WSMsgType.error:
                raise ws.exception()
        return ws
    except Exception as e:
        await ws.close(message='Error: {}'.format(e))
        raise
    finally:
        ws_sessions.remove(ws)



def json_encode(obj):
    if isinstance(obj, datetime.datetime):
        return obj.timestamp()
