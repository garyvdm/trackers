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
        self.status = None
        self.new_points_callbacks = []
        self.logger = logging.getLogger('trackers.{}'.format(name))

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


async def start_analyse_tracker(tracker, track_break_time=datetime.timedelta(minutes=20), track_break_dist=10000):
    analyse_tracker = Tracker('analysed.{}'.format(tracker.name))
    analyse_tracker.last_point_with_position = None
    analyse_tracker.current_track_id = 0
    analyse_tracker.status = None
    analyse_tracker.make_inactive_fut = None
    analyse_tracker.stop = functools.partial(stop_analyse_tracker, analyse_tracker)
    analyse_tracker.org_tracker = tracker
    await analyse_tracker_new_points(analyse_tracker, track_break_time, track_break_dist, tracker, tracker.points)
    tracker.new_points_callbacks.append(
        functools.partial(analyse_tracker_new_points, analyse_tracker, track_break_time, track_break_dist))
    return analyse_tracker


async def stop_analyse_tracker(analyse_tracker):
    await analyse_tracker.org_tracker.stop()
    if analyse_tracker.make_inactive_fut:
        analyse_tracker.make_inactive_fut.cancel()
        try:
            await analyse_tracker.make_inactive_fut
        except asyncio.CancelledError:
            pass
        analyse_tracker.make_inactive_fut = None


async def analyse_tracker_new_points(analyse_tracker, track_break_time, track_break_dist, tracker, new_points):
    new_new_points = []
    last_point_with_position = None
    for point in new_points:
        point = copy.deepcopy(point)
        if 'position' in point:
            if analyse_tracker.make_inactive_fut:
                analyse_tracker.make_inactive_fut.cancel()
                try:
                    await analyse_tracker.make_inactive_fut
                except asyncio.CancelledError:
                    pass
                analyse_tracker.make_inactive_fut = None

            if analyse_tracker.last_point_with_position:
                last_point = analyse_tracker.last_point_with_position
                dist = geodesic.Inverse(point['position'][0], point['position'][1], last_point['position'][0], last_point['position'][1])['s12']
                time = point['time'] - last_point['time']
                if time > track_break_time and dist > track_break_dist:
                    analyse_tracker.current_track_id += 1
                    analyse_apply_status_to_point(analyse_tracker, {'time': last_point['time'] + track_break_time},
                                                  'Inactive', new_new_points.append)

            # TODO what about status from source tracker?
            analyse_apply_status_to_point(analyse_tracker, point, 'Active')

            analyse_tracker.last_point_with_position = last_point_with_position = point
            point['track_id'] = analyse_tracker.current_track_id
        new_new_points.append(point)
    if new_new_points:
        await analyse_tracker.new_points(new_new_points)

    if last_point_with_position:
        analyse_tracker.make_inactive_fut = asyncio.ensure_future(
            make_inactive(analyse_tracker, last_point_with_position, track_break_time))


async def make_inactive(analyse_tracker, last_point_with_position, track_break_time):
    delay = (last_point_with_position['time'] - datetime.datetime.now() + track_break_time).total_seconds()
    if delay > 0:
        await asyncio.sleep(delay)
    if last_point_with_position == analyse_tracker.last_point_with_position:
        new_new_points = []
        analyse_apply_status_to_point(analyse_tracker, {'time': last_point_with_position['time'] + track_break_time},
                                      'Inactive', new_new_points.append)
        await asyncio.shield(analyse_tracker.new_points(new_new_points))


def analyse_apply_status_to_point(analyse_tracker, point, status, append_to=None):
    if status != analyse_tracker.status:
        point['status'] = status
        analyse_tracker.status = status
        if append_to:
            append_to(point)
