import asyncio
import functools
import logging
import copy
import geographiclib.geodesic
import datetime

geodesic = geographiclib.geodesic.Geodesic.WGS84

class Tracker(object):

    def __init__(self, name):
        self.name = name
        self.points = []
        self.status = {}
        self.new_points_callbacks = []
        self.logger = logging.getLogger('tracker.{}'.format(name))

    async def new_points(self, new_points):
        self.points.extend(new_points)
        await call_callbacks(self.new_points_callbacks, 'Error calling new_points callback:', self.logger, self, new_points)

    async def stop(self):
        pass


async def call_callbacks(callbacks, error_msg, logger, *args, **kwargs):
    loop = asyncio.get_event_loop()
    tasks = [loop.create_task(callback(*args, **kwargs)) for callback in callbacks]
    done_callback = functools.partial(callback_done_callback, error_msg, logger)
    for task in tasks:
        task.add_done_callback(done_callback)


def callback_done_callback(error_msg, logger, fut):
    try:
        fut.result()
    except asyncio.CancelledError:
        pass
    except Exception:
        logger.exception(error_msg)


async def cancel_and_wait_task(task):
    task.cancel()
    try:
        await task
    except Exception:
        pass


def print_tracker(tracker):

    import pprint
    async def print_callback(callback, source, data):
        print('{} {}: \n{}'.format(source.name, callback, pprint.pformat(data)))

    tracker.new_points_callbacks.append(functools.partial(print_callback, 'new_points'))


async def start_analyse_tracker(tracker):
    new_tracker = Tracker('analysed.{}'.format(tracker.name))
    new_tracker.last_point_with_position = None
    new_tracker.current_track_id = 0
    await analyse_tracker_new_points(new_tracker, tracker, tracker.points)
    tracker.new_points_callbacks.append(functools.partial(analyse_tracker_new_points, new_tracker))
    return new_tracker


async def analyse_tracker_new_points(new_tracker, tracker, new_points):
    new_new_points = []
    track_break_time = datetime.timedelta(minutes=21)
    track_break_dist = 10000
    for point in new_points:
        point = copy.deepcopy(point)
        if 'position' in point:
            if new_tracker.last_point_with_position:
                last_point = new_tracker.last_point_with_position
                dist = geodesic.Inverse(point['position'][0], point['position'][1], last_point['position'][0], last_point['position'][1])['s12']
                time = point['time'] - last_point['time']
                # speed = dist/time.total_seconds()
                if time > track_break_time and dist > track_break_dist:
                    # print((str(time), dist))
                    new_tracker.current_track_id += 1
            new_tracker.last_point_with_position = point
            point['track_id'] = new_tracker.current_track_id
        new_new_points.append(point)
    if new_new_points:
        await new_tracker.new_points(new_new_points)
