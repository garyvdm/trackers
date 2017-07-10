import asyncio
import datetime
import logging
import functools
import time

import aiohttp
from aniso8601 import parse_datetime
from aiocontext import async_contextmanager

import trackers

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
        await session.post('{}/api/session'.format(server['url']), data={'email': [server['auth'][0]], 'password': [server['auth'][1]]})

        server['position_received_callbacks'] = position_received_callbacks = {}
        server['ws_task'] = asyncio.ensure_future(server_ws_task(app, settings, session, server_name, server, position_received_callbacks))

        servers[server_name] = server
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
            await session.delete('{}/api/session'.format(server['url']))
            await server['session'].close()


async def server_ws_task(app, settings, session, server_name, server, position_received_callbacks):
    try:
        url = '{}/api/socket'.format(server['url'])
        logger = logging.getLogger('{}.{}'.format(__name__, server_name))
        reconnect_sleep_time = 1
        while True:
            try:
                logger.info('Connecting to ws {}'.format(url))

                async with session.ws_connect(url) as ws:
                    reconnect_sleep_time = 1
                    async for msg in ws:
                        if msg.type == aiohttp.WSMsgType.TEXT:
                            data = msg.json()
                            if 'positions' in data:
                                for position in data['positions']:
                                    device_id = position['deviceId']
                                    callbacks = position_received_callbacks.get(device_id, ())
                                    await trackers.call_callbacks(callbacks, 'Error in position_received_callback:', logger, position)
                        elif msg.type == aiohttp.WSMsgType.CLOSED:
                            break
                        elif msg.type == aiohttp.WSMsgType.ERROR:
                            break
            except asyncio.CancelledError:
                break
            except Exception:
                logger.exception('Error in ws_task: ')
            logger.info('Reconnecting in {} sec'.format(reconnect_sleep_time))
            await asyncio.sleep(reconnect_sleep_time)
            reconnect_sleep_time = min((reconnect_sleep_time * 2, 60))
    except Exception:
        logger.exception('Error in ws_task: ')


async def start_event_tracker(app, settings, event_name, event_data, rider_name, tracker_data):
    server_name = tracker_data.get('server', 'local')
    device_id = tracker_data['device_id']
    server = app['trackers.traccar_servers'][server_name]
    session = server['session']
    url = '{}/api/positions'.format(server['url'])
    tracker = trackers.Tracker('traccar.{}.{}-{}'.format(server_name, device_id, rider_name))
    tracker.server = server
    tracker.device_id = device_id
    tracker.seen_ids = seen_ids = set()
    positions = await (await session.get(url, params={
        'deviceId': device_id,
        'from': event_data['tracker_start'].isoformat(),
        'to': event_data['tracker_end'].isoformat()
    })).json()
    points = [traccar_position_translate(position) for position in positions]
    seen_ids.update([position['id'] for position in positions])
    tracker.position_recived = functools.partial(tracker_position_received, tracker)
    server['position_received_callbacks'].setdefault(device_id, []).append(tracker.position_recived)
    await tracker.new_points(points)

    tracker.finished = asyncio.Event()
    tracker.finish_specific = functools.partial(tracker_finish, tracker)
    tracker.stop_specific = functools.partial(tracker_stop, tracker)
    asyncio.get_event_loop().call_at(
        asyncio.get_event_loop().time() - time.time() + event_data['tracker_end'].timestamp(), tracker.finished.set )
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
        'server_time': parse_datetime(position['serverTime']).astimezone().replace(tzinfo=None)
            if position['serverTime'] else datetime.datetime.now(),
    }


async def main():
    app = {}
    settings = {
        'traccar_servers':{
            'trackrace_tk':
                {
                    'url': 'https://traccar.trackrace.tk',
                    'auth': ['email', 'password'],
                }
        }
    }
    import signal
    async with config(app, settings):
        tracker = await start_event_tracker(
            app, settings, 'foo',
            {'tracker_start': datetime.datetime(2017, 6, 9), 'tracker_end': datetime.datetime(2017, 7, 30)},
            'Gary', {'device_id': 2, 'server': 'trackrace_tk'})
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
