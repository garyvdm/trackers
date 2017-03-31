import xml.etree.ElementTree as xml
import asyncio
import datetime
import logging
import os
import itertools

import aiohttp
import bs4
import yaml

import trackers

async def api_call(client_session, request_name, data):
    req_data = (('request', request_name), ) + data
    async with client_session.post('http://www.mapmytracks.com/api/', data=req_data) as response:
        response.raise_for_status()
        xml_str = await response.text()
    xml_dom = xml.fromstring(xml_str)
    type_ = xml_dom.find('./type')
    # if type_ is None:
    #     raise RuntimeError('No type in message for {}'.format(request_name))
    if type_ is not None and type_.text == 'error':
        print(xml_str)
        reason = xml_dom.find('./reason').text
        raise RuntimeError('Error in {}: {}'.format(request_name, reason))
    return xml_dom


async def start_activity(client_session, title, privacy='public', activity='running', source='me', version='0.1.0'):
    data = (
        ('title', title),
        ('privacy', privacy),
        ('activity', activity),
        ('source', source),
        ('version', version),
        ('points', ''),
        ('tags', '')
    )
    xml_dom = await api_call(client_session, 'start_activity', data)
    activity_id = xml_dom.find('./activity_id').text()
    return activity_id

async def stop_activity(client_session, activity_id):
    await api_call(client_session, 'stop_activity', (('activity_id', activity_id), ))


async def get_activites(client_session, author):
    data = (
        ('author', author),
    )
    xml_dom = await api_call(client_session, 'get_activities', data)
    if xml_dom.find('./author').text:
        activites_elements = xml_dom.findall('./activities/*')
        activites = [( int(element.find('id').text), datetime.datetime.fromtimestamp(int(element.find('date').text)))
                     for element in activites_elements]

    else:
        # Hack to pull activites with html scraping. :-(
        logging.getLogger('mapmytracks').warning('User "{}" is not a username. Using html scrap to get activities.'.format(author))

        activites = []
        for i in range(1, 5):
            if i == 1:
                url = 'http://www.mapmytracks.com/{}'.format(author, i)
            else:
                url = 'http://www.mapmytracks.com/user-embeds/get-tracks/{}/{}'.format(author, i)
            async with client_session.post(url) as response:
                response.raise_for_status()
                text = await response.text()
            if text == '\n\nnomoretracks':
                break
            doc = bs4.BeautifulSoup(text, 'html.parser')
            if i == 1:
                tracks = doc.find(id='tracks-list').find_all(class_='grid-entry')
            else:
                tracks = doc.find_all(class_='grid-entry')

            for track in tracks:
                activity_id = int(track.find('a', title='Replay this activity')['href'].rpartition('/')[2])
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


async def start_monitor_user(client_session, user, start_date, end_date, cache_path):
    tracker = trackers.Tracker('mapmytracks.multiactivity.{}'.format(user))
    monitor_task = asyncio.ensure_future(monitor_user(client_session, user, start_date, end_date, cache_path, tracker))
    return tracker, monitor_task


async def monitor_user(client_session, user, start_date, end_date, cache_path, tracker):
    full_cache_path = os.path.join(cache_path, user)
    # todo have append state/cache storage
    try:
        with open(full_cache_path) as f:
            state = yaml.load(f)
        if state is None:
            state = {}
    except FileNotFoundError:
        state = {}

    activites = set(state.get('activites', []))
    completed_activites = set(state.get('completed_activites', ()))
    activities_points = state.setdefault('activities_points', {})

    tracker_point = lambda point: {'time': datetime.datetime.fromtimestamp(point[0]), 'position': point[1:], }

    old_points = [tracker_point(point) for point in sorted(itertools.chain.from_iterable(activities_points.values()))]
    if old_points:
        await tracker.new_points(old_points)

    while True:
        uncompleted_activities = completed_activites.difference(activites)

        if not uncompleted_activities:
            all_activites = await  get_activites(client_session, user)
            activites.update([activity[0] for activity in all_activites if start_date <= activity[1] < end_date])
            uncompleted_activities = activites.difference(completed_activites)

        new_points = []
        for activity_id in uncompleted_activities:
            points = activities_points.setdefault(activity_id, [])
            while True:
                max_timestamp = points[-1][0] if points else 0

                complete, update_points = await get_activity(client_session, activity_id, max_timestamp)
                if len(update_points) == 0:
                    break
                points.extend(update_points)
                new_points.append(update_points)
            if complete:
                completed_activites.add(activity_id)

        new_tracker_points = [tracker_point(point) for point in sorted(itertools.chain.from_iterable(new_points))]
        if new_tracker_points:
            await tracker.new_points(new_tracker_points)

        state['activites'] = list(activites)
        state['completed_activites'] = list(completed_activites)
        with open(full_cache_path, 'w') as f:
            yaml.dump(state, f)
        await asyncio.sleep(60)


async def main():

    async with aiohttp.ClientSession(auth=aiohttp.BasicAuth('USERNAME', 'PASSWORD')) as client_session:
        # activity_id = await start_activity(client_session, 'Testing')
        # await stop_activity(client_session, activity_id)
        # print(await get_activites(client_session, 'garyvdm'))

        tracker, monitor_task = await start_monitor_user(client_session, 'garyvdm',
                                                         datetime.datetime(2017, 3, 21), datetime.datetime(2017, 4, 10), '/tmp/')
        trackers.print_tracker(tracker)
        await monitor_task

if __name__ == "__main__":
    loop = asyncio.get_event_loop()
    loop.run_until_complete(main())
