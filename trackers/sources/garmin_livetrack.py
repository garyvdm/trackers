import asyncio
import datetime
import logging
import re

import aiohttp

# from trackers.base import print_tracker, Tracker


def config(app, settings):
    app['trackers.garmin_livetrack.session'] = garmin_livetrack_session = aiohttp.ClientSession()
    app.router.add_route('POST', '/modules/garmin_livetrack/email', handler=email_recive, name='garmin_livetrack_email')
    return garmin_livetrack_session


# async def start_event_tracker(app, event, rider_name, tracker_data):
#     # TODO
#     session_token_match = url_session_token_matcher(url).groupdict()
#     tracker = Tracker('garmin_livetrack.{}'.format(session_token_match['session']))
#     monitor_task = asyncio.ensure_future(monitor_session(
#         app['trackers.garmin_livetrack.session'], session_token_match, tracker))
#     return tracker, monitor_task


async def get_service_config(client_session):
    async with client_session.get('http://livetrack.garmin.com/services/config') as resp:
        return await resp.json()


url_session_token_matcher = re.compile('http://livetrack.garmin.com/session/(?P<session>.*)/token/(?P<token>.*)').match


async def email_recive(request):
    # using https://www.cloudmailin.com/ to get email to me.
    body = await request.content.read()
    url_m = re.search(br'<a href="(http:\/\/livetrack\.garmin\.com\/session\/[\w\d-]+\/token\/[\w\d-]+)"', body)
    name_m = re.search(br'An invitation from (.*)\n', body)
    logging.debug('Garmin email: {}, {}'.format(url_m, name_m))
    if url_m and name_m:
        logging.debug('Garmin email matches: {}, {}'.format(url_m.group(1), name_m.group(1)))
    return aiohttp.web.Response(text="Thanks for the mail.")


async def monitor_session(client_session, session_token_match, tracker):
    service_config = await get_service_config(client_session)
    session_url = 'http://livetrack.garmin.com/services/session/{session}/token/{token}'.format_map(session_token_match)
    tracklog_url = 'http://livetrack.garmin.com/services/trackLog/{session}/token/{token}'.format_map(session_token_match)
    last_status = None

    async def monitor_status():
        nonlocal last_status
        while True:
            try:
                response = await client_session.get(session_url)
                session = await response.json()
                status = session['sessionStatus']
                if status != last_status:
                    time = datetime.datetime.fromtimestamp(session['endTime'] / 1000)
                    await tracker.new_points([{'time': time, 'status': status}])
                    last_status = status

                if status == 'Complete':
                    break
            except Exception:
                tracker.logger.exception('Error getting session:')
            await asyncio.sleep(service_config['sessionRefreshRate'] / 1000)

    monitor_status_task = asyncio.ensure_future(monitor_status())

    last_timestamp = 0
    while True:
        try:
            reqs = await client_session.get(tracklog_url, params=(('from', str(last_timestamp)), ), )
            tracklog = await reqs.json()
            if tracklog:
                points = []
                for item in tracklog:
                    time = datetime.datetime.fromtimestamp(item['timestamp'] / 1000)
                    point = {'time': time}
                    if item['latitude'] != 0 and item['longitude'] != 0:  # Filter out null island
                        point['position'] = (item['latitude'], item['longitude'], float(item['metaData']['ELEVATION']))
                    # TODO hr, power cad
                    points.append(point)
                await tracker.new_points(points)
                last_timestamp = tracklog[-1]['timestamp']

        except Exception:
            tracker.logger.exception('Error getting tracklog:')

        if last_status == 'Complete':
            break
        await asyncio.sleep(service_config['tracklogRefreshRate'] / 1000)

    await monitor_status_task


# async def main(url):
#
#     async with aiohttp.ClientSession() as client_session:
#         service_config = await get_service_config(client_session)
#         tracker, monitor_task = await start_monitor_session(client_session, service_config, url)
#         print_tracker(tracker)
#         await monitor_task
#
# if __name__ == "__main__":
#     loop = asyncio.get_event_loop()
#     loop.run_until_complete(main('http://livetrack.garmin.com/session/cad74921-29af-4fe9-99f2-896b5972fbed/token/84D3B791E6C43ED2179CB59FB37CA24'))
