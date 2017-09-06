import asyncio
import datetime
import functools
import logging
import time

import aiohttp
import more_itertools
from aiocontext import async_contextmanager
from aniso8601 import parse_datetime

import trackers.web_app
from trackers.base import call_callbacks, Tracker

logger = logging.getLogger(__name__)


@async_contextmanager
async def config(app, settings):
    app['trackers.traccar_servers'] = servers = {}
    for server_name, server in settings['traccar_servers'].items():
        server['session'] = session = aiohttp.ClientSession(
            # auth=aiohttp.BasicAuth(*server['auth']),
            connector=aiohttp.TCPConnector(limit=4),
            raise_for_status=True,
        )
        server['position_received_callbacks'] = position_received_callbacks = {}
        server['ws_task'] = asyncio.ensure_future(server_ws_task(app, settings, session, server_name, server, position_received_callbacks))

        servers[server_name] = server
        if isinstance(app, aiohttp.web.Application):
            app['add_individual_handler']('/traccar/{unique_id}')
            app.router.add_route('GET', '/traccar/{unique_id}/websocket',
                                 handler=functools.partial(trackers.web_app.individual_ws, get_individual_key,
                                                           functools.partial(start_individual_tracker, app, settings)),
                                 name='tarccar_individual_ws')

    try:
        yield
    finally:
        logger.debug('Shutdown.')
        for server_name, server in servers.items():
            server['ws_task'].cancel()
            try:
                await server['ws_task']
            except asyncio.CancelledError:
                pass
            except Exception:
                logger.exception('Error in ws_task: ')
            await logout(app, server_name)
            await server['session'].close()


async def ensure_login(app, server_name):
    server = app['trackers.traccar_servers'][server_name]
    logger = logging.getLogger('{}.{}'.format(__name__, server_name))
    if not server.get('user_id'):
        session_response = await server['session'].post('{url}/api/session'.format_map(server), data={'email': [server['auth'][0]], 'password': [server['auth'][1]]})
        user = await session_response.json()
        server['user_id'] = user['id']
        logger.info('Successfull login to {url}'.format_map(server))


async def logout(app, server_name):
    server = app['trackers.traccar_servers'][server_name]
    logger = logging.getLogger('{}.{}'.format(__name__, server_name))
    if server.get('user_id'):
        try:
            await server['session'].delete('{}/api/session'.format(server['url']))
        except aiohttp.client_exceptions.ClientError as e:
            logger.error('Error in delete session: {!r}'.format(e))
        except Exception:
            logger.exception('Error in delete session:')
        del server['user_id']


async def server_ws_task(app, settings, session, server_name, server, position_received_callbacks):
    try:
        url = '{}/api/socket'.format(server['url'])
        logger = logging.getLogger('{}.{}'.format(__name__, server_name))
        reconnect_sleep_time = 5
        while True:
            try:
                await ensure_login(app, server_name)
                logger.debug('Connecting to ws {}'.format(url))
                async with session.ws_connect(url) as ws:
                    reconnect_sleep_time = 1
                    async for msg in ws:
                        if msg.type == aiohttp.WSMsgType.TEXT:
                            data = msg.json()
                            if 'positions' in data:
                                for position in data['positions']:
                                    device_id = position['deviceId']
                                    callbacks = position_received_callbacks.get(device_id, ())
                                    await call_callbacks(callbacks, 'Error in position_received_callback:', logger, position)
                        elif msg.type == aiohttp.WSMsgType.CLOSED:
                            break
                        elif msg.type == aiohttp.WSMsgType.ERROR:
                            break
            except asyncio.CancelledError:
                break
            except aiohttp.client_exceptions.ClientError as e:
                logger.error('Error in ws_task: {!r}'.format(e))
                await logout(app, server_name)
            except Exception:
                logger.exception('Error in ws_task: ')
            logger.debug('Reconnecting in {} sec'.format(reconnect_sleep_time))
            await asyncio.sleep(reconnect_sleep_time)
            reconnect_sleep_time = min((reconnect_sleep_time * 2, 30))
    except asyncio.CancelledError:
        pass
    except Exception:
        logger.exception('Error in ws_task: ')


async def start_event_tracker(app, settings, event_name, event_data, rider_name, tracker_data):
    return await start_tracker(app, settings, rider_name,
                               tracker_data.get('server', 'local'), tracker_data['unique_id'],
                               event_data['tracker_start'], event_data['tracker_end'])


def get_individual_key(request):
    return "traccar-{unique_id}".format_map(request.match_info)


async def start_individual_tracker(app, settings, request):
    unique_id = request.match_info['unique_id']
    server_name = 'local'
    start = datetime.datetime.now() - datetime.timedelta(days=7)
    return await start_tracker(app, settings, unique_id,
                               server_name, unique_id,
                               start, None)


async def start_tracker(app, settings, tracker_name, server_name, device_unique_id, start, end):
    server = app['trackers.traccar_servers'][server_name]
    session = server['session']
    url = '{}/api/positions'.format(server['url'])
    devices = await (await session.get('{}/api/devices'.format(server['url']), params={'all': 'true'})).json()
    device_id = more_itertools.first((device['id'] for device in devices if device['uniqueId'] == device_unique_id))

    await session.post('{}/api/permissions/devices'.format(server['url']), json={'userId': server['user_id'], 'deviceId': device_id})

    tracker = Tracker('traccar.{}.{}-{}'.format(server_name, device_unique_id, tracker_name))
    tracker.server = server
    tracker.device_id = device_id
    tracker.seen_ids = seen_ids = set()
    positions = await (await session.get(url, params={
        'deviceId': device_id,
        'from': start.isoformat(),
        'to': (end if end else datetime.datetime.now() + datetime.timedelta(days=1)).isoformat()
    })).json()
    points = [traccar_position_translate(position) for position in positions]
    seen_ids.update([position['id'] for position in positions])
    tracker.position_recived = functools.partial(tracker_position_received, tracker)
    server['position_received_callbacks'].setdefault(device_id, []).append(tracker.position_recived)
    await tracker.new_points(points)

    tracker.finished = asyncio.Event()
    tracker.finish_specific = functools.partial(tracker_finish, tracker)
    tracker.stop_specific = functools.partial(tracker_stop, tracker)
    if end:
        asyncio.get_event_loop().call_at(
            asyncio.get_event_loop().time() - time.time() + end.timestamp(), tracker.finished.set)
    return tracker


async def tracker_position_received(tracker, position):
    if position['id'] not in tracker.seen_ids:
        tracker.seen_ids.add(position['id'])
        await tracker.new_points([traccar_position_translate(position)])


async def tracker_stop(tracker):
    tracker.finished.set()


async def tracker_finish(tracker):
    await tracker.finished.wait()
    tracker.server['position_received_callbacks'][tracker.device_id].remove(tracker.position_recived)


def traccar_position_translate(position):
    return {
        'position': [position['latitude'], position['longitude'], position['altitude']],
        'accuracy': position['accuracy'],
        'battery': position['attributes'].get('batteryLevel'),
        'time': parse_datetime(position['fixTime']).astimezone().replace(tzinfo=None),
        # server_time is null in websocket positions :-( Need to log an issue, and fix it.
        'server_time': (
            parse_datetime(position['serverTime']).astimezone().replace(tzinfo=None)
            if position['serverTime'] else datetime.datetime.now()),
    }


async def main():
    app = {}
    settings = {
        'traccar_servers': {
            'trackrace_tk':
                {
                    'url': 'https://traccar.trackrace.tk',
                    'auth': ['admin', ''],
                }
        }
    }
    import signal
    async with config(app, settings):
        tracker = await start_tracker(
            app, settings, 'gary', 'trackrace_tk', '510586', datetime.datetime(2017, 6, 9), datetime.datetime(2017, 7, 30))
        trackers.print_tracker(tracker)
        # await tracker.finish()
        run_fut = asyncio.Future()
        for signame in ('SIGINT', 'SIGTERM'):
            loop.add_signal_handler(getattr(signal, signame), run_fut.set_result, None)
        try:
            await run_fut
        finally:
            for signame in ('SIGINT', 'SIGTERM'):
                loop.remove_signal_handler(getattr(signal, signame))
        await tracker.stop()
        await tracker.finish()

if __name__ == "__main__":
    logging.basicConfig(level=logging.DEBUG)
    loop = asyncio.get_event_loop()
    loop.run_until_complete(main())
