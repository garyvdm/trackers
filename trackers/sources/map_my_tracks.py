import asyncio
import datetime
import itertools
import json
import logging
import os
import xml.etree.ElementTree as xml
from urllib.parse import quote

import aiohttp
import bs4

from trackers.base import Tracker


def config(app, settings):
    app['trackers.mapmytracks_session'] = mapmytracks_session = aiohttp.ClientSession(
        auth=aiohttp.BasicAuth(*settings['mapmytracks_auth']),
        connector=aiohttp.TCPConnector(limit=4)
    )
    return mapmytracks_session


async def start_event_tracker(app, event, rider_name, tracker_data, start, end):
    tracker = Tracker('mapmytracks.{}'.format(tracker_data['name']))
    monitor_task = asyncio.ensure_future(monitor_user(
        app['trackers.mapmytracks_session'], tracker_data['name'],
        start, end,
        os.path.join(app['trackers.settings']['cache_path'], event.name, 'mapmytracks'),
        tracker, set(tracker_data.get('exclude', ()))))
    tracker.stop = monitor_task.cancel
    tracker.completed = monitor_task
    return tracker


async def api_call(client_session, request_name, data):
    req_data = (('request', request_name), ) + data
    async with client_session.post('http://www.mapmytracks.com/api/', data=req_data,) as response:
        response.raise_for_status()
        xml_str = await response.text()
    try:
        xml_dom = xml.fromstring(xml_str)
    except Exception:
        logging.exception("Error parsing: \n{}\n".format(xml_str))
        raise
    type_ = xml_dom.find('./type')
    # if type_ is None:
    #     raise RuntimeError('No type in message for {}'.format(request_name))
    if type_ is not None and type_.text == 'error':
        reason = xml_dom.find('./reason').text
        raise RuntimeError('Error in {}: {}'.format(request_name, reason))
    return xml_dom


async def start_activity(client_session, title, points, privacy='public', activity='running', source='garyvdm@gmail.com', tags=()):
    data = (
        ('title', title),
        ('privacy', privacy),
        ('activity', activity),
        ('source', source),
        ('points', points),
        ('tags', ' '.join(tags))
    )
    xml_dom = await api_call(client_session, 'start_activity', data)
    activity_id = xml_dom.find('./activity_id').text
    return activity_id


async def stop_activity(client_session, activity_id):
    await api_call(client_session, 'stop_activity', (('activity_id', activity_id), ))


async def get_activites(client_session, author, logger=None, pages=5, warn_scrape=True):
    if logger is None:
        logger = logging.getLogger('mapmytracks')

    data = (
        ('author', author),
    )
    xml_dom = await api_call(client_session, 'get_activities', data)
    if xml_dom.find('./author').text:
        activites_elements = xml_dom.findall('./activities/*')
        activites = [(int(element.find('id').text), datetime.datetime.fromtimestamp(int(element.find('date').text)))
                     for element in activites_elements]

    else:
        # Hack to pull activites with html scraping. :-(
        if warn_scrape:
            logger.warning('User "{}" is not a username. Using html scrap to get activities.'.format(author))

        activites = []
        for i in range(1, pages):
            if i == 1:
                url = 'http://www.mapmytracks.com/{}'.format(quote(author), i)
            else:
                url = 'http://www.mapmytracks.com/user-embeds/get-tracks/{}/{}'.format(quote(author), i)
            async with client_session.post(url) as response:
                response.raise_for_status()
                text = await response.text()
            if text == '\n\nnomoretracks':
                break
            doc = bs4.BeautifulSoup(text, 'html.parser')

            for track in doc.find_all(class_='grid-entry'):
                link = track.find('a', title='Replay this activity')
                if link:
                    activity_id = int(link['href'].rpartition('/')[2])
                    date_text = track.find(class_='act-local').text.partition(' on ')[2].strip()
                    date = datetime.datetime.strptime(date_text, '%d %b %Y')
                    activites.append((activity_id, date))
    return activites


def point_from_str(s):
    split = s.split(',')
    return [int(split[0]), float(split[1]), float(split[2]), float(split[3]), ]


async def get_activity(client_session, activity_id, from_time):
    data = (
        ('activity_id', activity_id),
        ('from_time', from_time)
    )
    xml_dom = await api_call(client_session, 'get_activity', data)
    complete = xml_dom.find('./complete').text == 'Yes'
    points_str = xml_dom.find('./points').text
    if points_str:
        points = [point_from_str(p) for p in points_str.split(' ')]
    else:
        points = []
    return complete, points


async def monitor_user(client_session, user, start_date, end_date, cache_path, tracker, exclude):
    os.makedirs(cache_path, exist_ok=True)

    full_cache_path = os.path.join(cache_path, user)
    # todo have append state/cache storage
    state = {}
    try:
        with open(full_cache_path, 'r') as f:
            state = json.load(f)
    except FileNotFoundError:
        tracker.logger.info("Cache file not found: '{}'".format(full_cache_path))
    except Exception:
        tracker.logger.exception("Error loading cache file: '{}' :".format(full_cache_path))

    activites = set([activity for activity in state.get('activites', []) if activity not in exclude])
    completed_activites = set(state.get('completed_activites', ()))
    activities_points = state.setdefault('activities_points', {})

    def tracker_point(point):
        return {'time': datetime.datetime.fromtimestamp(point[0]), 'position': point[1:], }
    last_point = None

    old_points = [tracker_point(point) for point in sorted(itertools.chain.from_iterable(activities_points.values()))]
    if old_points:
        await tracker.new_points(old_points)
        last_point = old_points[-1]

    def save():
        state = {}
        state['activites'] = list(activites)
        state['completed_activites'] = list(completed_activites)
        state['activities_points'] = activities_points
        tracker.logger.debug('Activity point counts: {}'.format({key: len(values) for key, values in activities_points.items()}))

        with open(full_cache_path, 'w') as f:
            json.dump(state, f)

    first_get_activites = True

    inactive_time = datetime.timedelta(minutes=20)

    for i in itertools.count():
        try:
            now = datetime.datetime.now()
            last_slow_log = now
            slow = False

            if now >= start_date:
                uncompleted_activities = activites.difference(completed_activites)

                tracker.logger.debug('uncompleted_activities: {}'.format(uncompleted_activities))
                if len(uncompleted_activities) == 0 or i % 10 == 0 or (not last_point or now - last_point['time'] > inactive_time):
                    tracker.logger.debug('Getting activities')
                    all_activites = await get_activites(client_session, user, logger=tracker.logger,
                                                        pages=2 if activites else 5, warn_scrape=first_get_activites)
                    first_get_activites = False
                    activites.update([activity[0] for activity in all_activites if start_date <= activity[1] < end_date and activity[0] not in exclude])
                    uncompleted_activities = activites.difference(completed_activites)

                    # Hack to get the incorrectly completed activity out of completed.
                    if activites:
                        uncompleted_activities.add(max(activites))
                    save()

                completed_changes = False

                new_points = []
                for activity_id in sorted(uncompleted_activities):
                    points = activities_points.setdefault(str(activity_id), [])
                    while True:
                        max_timestamp = points[-1][0] if points else 0
                        tracker.logger.debug('Getting points for {} ({})'.format(activity_id, max_timestamp))

                        complete, update_points = await get_activity(client_session, activity_id, max_timestamp)
                        tracker.logger.debug('Got {} points'.format(len(update_points)))
                        points.extend(update_points)
                        new_points.append(update_points)
                        # if len(update_points) == 0:
                        #     break
                        break  # Document says api will only return 100 rows at a time - but it seems to send down all rows.

                    if complete != (activity_id in completed_activites):
                        completed_changes = True
                        if complete:
                            completed_activites.add(activity_id)
                            tracker.logger.debug('Activity {} completed'.format(activity_id))
                        else:
                            completed_activites.remove(activity_id)
                            tracker.logger.debug('Activity {} uncompleted????'.format(activity_id))

                    if datetime.datetime.now() - last_slow_log > datetime.timedelta(seconds=10):
                        tracker.logger.info('Still downloading. ({} points)'.format(sum((len(p) for p in new_points))))
                        slow = True
                        last_slow_log = datetime.datetime.now()
                        save()

                if slow:
                    tracker.logger.info('Done downloading. ({} points)'.format(sum((len(p) for p in new_points))))

                if new_points or completed_changes:
                    save()

                new_tracker_points = [tracker_point(point) for point in sorted(itertools.chain.from_iterable(new_points))]
                if new_tracker_points:
                    await tracker.new_points(new_tracker_points)
                    last_point = new_tracker_points[-1]

            if now > end_date:
                break

            await asyncio.sleep(30)
        except asyncio.CancelledError:
            raise
        except aiohttp.client_exceptions.ClientError as e:
            tracker.logger.error('Error in monitor_user: {!r}'.format(e))
        except Exception:
            tracker.logger.exception('Error in monitor_user:')
            await asyncio.sleep(10)


async def main():
    pass
    # async with aiohttp.ClientSession(auth=aiohttp.BasicAuth('USERNAME', 'PASSWORD')) as client_session:
    #     pass
    #     activity_id = await start_activity(client_session, 'My activity', points='51.3704583333333 1.15737333333333 1.345 1198052842')
    #     print(activity_id)
    #     await stop_activity(client_session, activity_id)
    #     print(await get_activites(client_session, 'garyvdm'))
    #
    #     tracker, monitor_task = await start_monitor_user(client_session, 'garyvdm',
    #                                                      datetime.datetime(2017, 3, 21), datetime.datetime(2017, 4, 10), '/tmp/')
    #     trackers.print_tracker(tracker)
    #     await monitor_task

if __name__ == "__main__":
    logging.basicConfig(level=logging.DEBUG)
    loop = asyncio.get_event_loop()
    loop.run_until_complete(main())
