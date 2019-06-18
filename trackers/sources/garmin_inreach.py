import asyncio
import datetime
import logging
import xml.etree.ElementTree as xml
from contextlib import asynccontextmanager

import aiohttp

from trackers.base import print_tracker, Tracker

logger = logging.getLogger(__name__)


@asynccontextmanager
async def config(app, settings):
    app['garmin_inreach.session'] = session = aiohttp.ClientSession(
        connector=aiohttp.TCPConnector(limit=1),
        raise_for_status=True,
    )

    try:
        yield
    finally:
        await session.close()


async def start_event_tracker(app, event, rider_name, tracker_data, start, end):
    return await start_tracker(app, rider_name, tracker_data['feed_id'], tracker_data['password'], start, end)


async def start_tracker(app, tracker_name, feed_id, password, start, end):
    tracker = Tracker('garmin_inreach.{}-{}'.format(feed_id, tracker_name))
    monitor_task = asyncio.ensure_future(monitor_feed(app, tracker, feed_id, password, start, end))
    tracker.stop = monitor_task.cancel
    tracker.completed = monitor_task
    return tracker


async def monitor_feed(app, tracker, feed_id, password, start, end):
    try:
        seen_ids = set()
        session: aiohttp.ClientSession = app['garmin_inreach.session']
        auth = aiohttp.BasicAuth(feed_id, password)
        url = f'https://eur.inreach.garmin.com/Feed/Share/{feed_id}'
        if not start:
            start = datetime.datetime.utcnow()
        else:
            start = start.astimezone(datetime.timezone.utc).replace(tzinfo=None)
        if end:
            end = end.astimezone(datetime.timezone.utc).replace(tzinfo=None)
        last = start
        # From this point on now, last, start, and end are all utc and tz naive.

        while True:
            try:
                now = datetime.datetime.utcnow()
                if now > start:
                    params = {'d1': last.isoformat(timespec='seconds') + 'z'}
                    if end and now > end:
                        params['d2'] = end.isoformat(timespec='seconds') + 'z'
                    async with session.get(url, params=params, auth=auth) as response:
                        kml_text = await response.text()
                    last = now
                    await process_data(tracker, kml_text, now, seen_ids)

                if end and now >= end:
                    break
            except asyncio.CancelledError:
                raise
            except (aiohttp.client_exceptions.ClientError, RuntimeError) as e:
                tracker.logger.error('Error in monitor_feed: {!r}'.format(e))
            except Exception:
                tracker.logger.exception('Error in monitor_feed:')

            await wait_for_next_check(tracker)

    except asyncio.CancelledError:
        raise
    except Exception:
        tracker.logger.exception('Error in monitor_feed:')


async def process_data(tracker, kml_text, now, seen_ids):
    xml_doc = xml.fromstring(kml_text)

    kml_ns = {
        'kml': 'http://www.opengis.net/kml/2.2',
    }

    placemarks = xml_doc.findall('./kml:Document/kml:Folder/kml:Placemark', kml_ns)

    new_points = []
    for placemark in placemarks:
        extended_data = {data_el.attrib['name']: data_el.find('kml:value', kml_ns).text
                         for data_el in placemark.findall('kml:ExtendedData/kml:Data', kml_ns)}
        if extended_data and extended_data['Id'] not in seen_ids:
            seen_ids.add(extended_data['Id'])
            lat = float(extended_data['Latitude'])
            lng = float(extended_data['Longitude'])
            elevation = float(extended_data['Elevation'].partition(' ')[0])
            time_utc = datetime.datetime.fromisoformat(placemark.find('kml:TimeStamp/kml:when', kml_ns).text[:-1]).replace(tzinfo=datetime.timezone.utc)
            time = time_utc.astimezone().replace(tzinfo=None)
            point = {
                'position': [lat, lng, elevation],
                'time': time,
                'battery': None,
            }
            if extended_data['Event'] == 'Tracking turned off from device.':
                point['tk_config'] = 'Off'
            if extended_data['Event'] == 'Tracking turned on from device.':
                point['tk_config'] = 'On'
            new_points.append(point)
    if new_points:
        await tracker.new_points(new_points)


async def wait_for_next_check(tracker):
    now = datetime.datetime.now()
    if tracker.points:
        next_check_on_last_point_time = tracker.points[-1]['time'] + datetime.timedelta(minutes=5, seconds=15)
    else:
        next_check_on_last_point_time = datetime.datetime(year=1980, month=1, day=1)

    next_check_on_now = now + datetime.timedelta(minutes=2, seconds=30)
    next_check = max(next_check_on_now, next_check_on_last_point_time)
    next_check_sec = (next_check - now).total_seconds()
    tracker.logger.debug(f'Next check: {next_check_sec} sec -- {next_check}')
    await asyncio.sleep(next_check_sec)


async def main():
    import signal

    app = {}
    settings = {}
    async with config(app, settings):
        tracker = await start_tracker(
            app, 'JanV', 'JanV', '', datetime.datetime(2019, 6, 17), None)
        print_tracker(tracker)

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
