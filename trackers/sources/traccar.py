import asyncio
import datetime
import functools
import logging
import time
from collections import defaultdict
from functools import partial

import aiohttp
import more_itertools
from aiocontext import async_contextmanager
from aiohttp.web import Application as WebApplication
from aniso8601 import parse_datetime

from trackers.base import Observable, Tracker

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
        server['position_received_observables'] = position_received_observables = defaultdict(partial(Observable, logger))
        server['ws_task'] = asyncio.ensure_future(server_ws_task(app, settings, session, server_name, server, position_received_observables))
        server['login_lock'] = asyncio.Lock()

        servers[server_name] = server

        if isinstance(app, WebApplication):
            import trackers.web_app
            app.router.add_route('GET', '/traccar/{unique_id}',
                                 handler=trackers.web_app.individual_page,
                                 name='tarccar_individual_page')
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
    async with server['login_lock']:
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


async def server_ws_task(app, settings, session, server_name, server, position_received_observables):
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
                                    observable = position_received_observables.get(device_id)
                                    if observable:
                                        await observable(position)
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


async def start_event_tracker(app, event, rider_name, tracker_data):
    return await start_tracker(app, rider_name,
                               tracker_data.get('server', 'local'), tracker_data['unique_id'],
                               event.config['tracker_start'], event.config['tracker_end'])


def get_individual_key(request):
    return "traccar-{unique_id}".format_map(request.match_info)


async def start_individual_tracker(app, settings, request):
    unique_id = request.match_info['unique_id']
    server_name = 'local'
    start = datetime.datetime.now() - datetime.timedelta(days=7)
    return await start_tracker(app, unique_id,
                               server_name, unique_id,
                               start, None)


async def start_tracker(app, tracker_name, server_name, device_unique_id, start, end):
    device_unique_id = str(device_unique_id)
    await ensure_login(app, server_name)
    server = app['trackers.traccar_servers'][server_name]
    server_url = server['url']
    session = server['session']
    url = f'{server_url}/api/positions'
    # await (await session.delete(f'{server_url}/api/devices/0', json={})).json()

    devices = await (await session.get(f'{server_url}/api/devices', params={'all': 'true'})).json()
    try:
        device = more_itertools.first((device for device in devices if device['uniqueId'] == device_unique_id))
        device_id = device['id']
        if device['name'] == device['uniqueId']:
            # Update name on traccar
            await (await session.put(f'{server_url}/api/devices/{device_id}',
                                     json={'name': tracker_name, 'uniqueId': device['uniqueId'], 'id': device['id']})).json()
    except ValueError:
        device_id = (await (await session.post(f'{server_url}/api/devices',
                                               json={'uniqueId': device_unique_id, 'name': tracker_name})).json())['id']
    await (await session.post(f'{server_url}/api/permissions/devices',
                              json={'userId': server['user_id'], 'deviceId': device_id})).json()

    tracker = Tracker('traccar.{}.{}-{}'.format(server_name, device_unique_id, tracker_name))
    tracker.server = server
    tracker.device_id = device_id
    tracker.start = start
    tracker.end = end
    tracker.seen_ids = seen_ids = set()
    positions = await (await session.get(url, params={
        'deviceId': device_id,
        'from': start.isoformat(),
        'to': (end if end else datetime.datetime.now() + datetime.timedelta(days=1)).isoformat()
    })).json()
    points = [traccar_position_translate(position) for position in positions]
    seen_ids.update([position['id'] for position in positions])
    tracker.position_recived = functools.partial(tracker_position_received, tracker)
    server['position_received_observables'][device_id].subscribe(tracker.position_recived)
    await tracker.new_points(points)

    tracker.finished = asyncio.Event()
    tracker.stop = functools.partial(tracker_stop, tracker)
    tracker.completed = asyncio.ensure_future(tracker.finished.wait())
    tracker.completed.add_done_callback(functools.partial(tracker_on_completed, tracker))
    if end:
        asyncio.get_event_loop().call_at(
            asyncio.get_event_loop().time() - time.time() + end.timestamp(), tracker.finished.set)
    return tracker


async def tracker_position_received(tracker, position):
    if position['id'] not in tracker.seen_ids:
        tracker.seen_ids.add(position['id'])
        point = traccar_position_translate(position)
        if (not tracker.start or tracker.start < point['time']) and (not tracker.end or point['time'] < tracker.end):
            await tracker.new_points([point])


def tracker_stop(tracker):
    tracker.finished.set()


def tracker_on_completed(tracker, fut):
    tracker.server['position_received_observables'][tracker.device_id].unsubscribe(tracker.position_recived)


async def tracker_finish(tracker):
    await tracker.finished.wait()
    tracker.server['position_received_observables'][tracker.device_id].unsubscribe(tracker.position_recived)


def traccar_position_translate(position):
    if position['altitude']:
        p = [position['latitude'], position['longitude'], position['altitude']]
    else:
        p = [position['latitude'], position['longitude']]
    return {
        'position': p,
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
        # await tracker.finish()
        run_fut = asyncio.Future()
        for signame in ('SIGINT', 'SIGTERM'):
            loop.add_signal_handler(getattr(signal, signame), run_fut.set_result, None)
        try:
            await run_fut
        finally:
            for signame in ('SIGINT', 'SIGTERM'):
                loop.remove_signal_handler(getattr(signal, signame))
        tracker.stop()
        await tracker.complete()

if __name__ == "__main__":
    logging.basicConfig(level=logging.DEBUG)
    loop = asyncio.get_event_loop()
    loop.run_until_complete(main())
