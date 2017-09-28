import asyncio
import collections
import concurrent.futures
import copy
import datetime
import functools
import logging
import operator

import attr
from numpy import (
    arccos,
    cross,
    deg2rad,
    dot,
    rad2deg,
    seterr,
)
from numpy.linalg import norm
from nvector import (
    interpolate,
    lat_lon2n_E,
    n_E2lat_lon,
    n_EB_E2p_EB_E,
    unit,
)

from trackers.base import Tracker

logger = logging.getLogger(__name__)
# seterr(all='raise')

analyse_executor = concurrent.futures.ThreadPoolExecutor(max_workers=1, thread_name_prefix='analyse')


async def start_analyse_tracker(tracker, event, event_routes, track_break_time=datetime.timedelta(minutes=20), track_break_dist=10000):
    analyse_tracker = Tracker('analysed.{}'.format(tracker.name))
    analyse_tracker.last_point_with_position = None
    analyse_tracker.last_point_with_position_point = None
    analyse_tracker.current_track_id = 0
    analyse_tracker.status = None
    analyse_tracker.make_inactive_fut = None
    analyse_tracker.stop_specific = functools.partial(stop_analyse_tracker, analyse_tracker)
    analyse_tracker.finish_specific = functools.partial(finish_analyse_tracker, analyse_tracker)
    analyse_tracker.org_tracker = tracker
    analyse_tracker.last_closest = None
    analyse_tracker.dist_ridden = 0
    analyse_tracker.finished = False
    await analyse_tracker_new_points(analyse_tracker, event, event_routes, track_break_time, track_break_dist, tracker, tracker.points)
    tracker.new_points_callbacks.append(
        functools.partial(analyse_tracker_new_points, analyse_tracker, event, event_routes, track_break_time, track_break_dist))
    return analyse_tracker


async def stop_analyse_tracker(analyse_tracker):
    await analyse_tracker.org_tracker.stop()
    if analyse_tracker.make_inactive_fut:
        analyse_tracker.make_inactive_fut.cancel()


async def finish_analyse_tracker(analyse_tracker):
    await analyse_tracker.org_tracker.finish()
    if analyse_tracker.make_inactive_fut:
        try:
            await analyse_tracker.make_inactive_fut
        except asyncio.CancelledError:
            pass
        analyse_tracker.make_inactive_fut = None


async def analyse_tracker_new_points(analyse_tracker, event, event_routes, track_break_time, track_break_dist, tracker, new_points):
    analyse_tracker.logger.debug('analyse_tracker_new_points ({} points)'.format(len(new_points)))

    new_new_points = []
    last_point_with_position = None
    log_time = datetime.datetime.now()
    log_i = 0
    last_route_point = event_routes[0]['points'][-1] if event_routes else None

    # analyze_point_wraped = functools.partial(asyncio.get_event_loop().run_in_executor, analyse_executor, analyze_point)
    analyze_point_wraped = analyze_point_async

    last_point_i = len(new_points) - 1
    did_slow_log = False

    for i, point in enumerate(new_points):
        point = copy.deepcopy(point)
        if 'position' in point:
            if analyse_tracker.make_inactive_fut:
                analyse_tracker.make_inactive_fut.cancel()
                try:
                    await analyse_tracker.make_inactive_fut
                except asyncio.CancelledError:
                    pass
                analyse_tracker.make_inactive_fut = None
            await analyze_point_wraped(analyse_tracker, event, event_routes, track_break_time, last_route_point, track_break_dist, tracker, new_new_points, point)
            analyse_tracker.last_point_with_position = last_point_with_position = point
            point['track_id'] = analyse_tracker.current_track_id

        new_new_points.append(point)

        is_last_point = i == last_point_i
        if i % 10 == 9 or is_last_point:
            now = datetime.datetime.now()
            log_time_delta = (now - log_time).total_seconds()
            if log_time_delta >= 5 or (is_last_point and did_slow_log):
                analyse_tracker.logger.info('{}/{} ({:.1f}%) points analysed at {:.2f} points/second.'.format(
                    i, len(new_points), i / (len(new_points) - 1) * 100, (i - log_i) / log_time_delta))
                log_time = now
                log_i = i
                did_slow_log = True
                if new_new_points:
                    await analyse_tracker.new_points(new_new_points)
                    new_new_points = []

        if analyse_tracker.finished:
            break

    if new_new_points:
        await analyse_tracker.new_points(new_new_points)

    if last_point_with_position:
        analyse_tracker.make_inactive_fut = asyncio.ensure_future(
            make_inactive(analyse_tracker, last_point_with_position, track_break_time))


async def analyze_point_async(*args, **kwargs):
    return analyze_point(*args, **kwargs)


def analyze_point(analyse_tracker, event, event_routes, track_break_time, last_route_point, track_break_dist, tracker, new_new_points, point):
    # TODO only search points after the last route point
    point_point = Point(*point['position'][:2])
    closest = find_closest_point_pair_routes(event_routes, point_point, 1000, analyse_tracker.last_closest, 250)
    if closest and closest.dist > 5000:
        closest = None

    if closest:
        prev_route_point = closest.point_pair[0]
        route = closest.route
        if route['is_main']:
            point['dist_route'] = round(prev_route_point.distance + distance(prev_route_point, closest.point))
        else:
            alt_route_dist = prev_route_point.distance + distance(prev_route_point, closest.point)
            point['dist_route'] = round(alt_route_dist * route['dist_factor'] + route['start_distance'])

        if not analyse_tracker.finished:
            if closest.route_i == 0 and closest.point_pair[1].distance - last_route_point.distance < 100 and distance(point_point, last_route_point) < 100:
                analyse_tracker.logger.debug('Finished')
                analyse_tracker.finished = True
                point['finished_time'] = point['time']
                point['rider_status'] = 'Finished'

    analyse_tracker.last_closest = closest

    if analyse_tracker.last_point_with_position:
        last_point = analyse_tracker.last_point_with_position
        dist = distance(point_point, analyse_tracker.last_point_with_position_point)
        time = point['time'] - last_point['time']
        if time > track_break_time and dist > track_break_dist:
            analyse_tracker.current_track_id += 1
            analyse_apply_status_to_point(analyse_tracker, {'time': last_point['time'] + track_break_time},
                                          'Inactive', new_new_points.append)
        point['dist_from_last'] = round(dist)
        analyse_tracker.dist_ridden += dist
        point['dist_ridden'] = round(analyse_tracker.dist_ridden)

    # TODO what about status from source tracker?
    analyse_apply_status_to_point(analyse_tracker, point, 'Active')
    analyse_tracker.last_point_with_position_point = point_point


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


@attr.s(slots=True)
class Point(object):
    lat = attr.ib()
    lng = attr.ib()
    _nv = attr.ib(default=None, repr=False, cmp=False)
    _pv = attr.ib(default=None, repr=False, cmp=False)

    def to_point(self):
        return self

    @property
    def nv(self):
        if self._nv is None:
            self._nv = lat_lon2n_E(deg2rad(self.lat), deg2rad(self.lng))
        return self._nv

    @property
    def pv(self):
        if self._pv is None:
            self._pv = n_EB_E2p_EB_E(self.nv)
        return self._pv


@attr.s(slots=True)
class IndexedPoint(Point):
    index = attr.ib(default=None)
    distance = attr.ib(default=None)

    def to_point(self):
        return Point(self.lat, self.lng)


def get_analyse_routes(org_routes):
    return [get_analyse_route(route) for route in org_routes]


def get_analyse_route(org_route):
    route = copy.copy(org_route)
    route['points'] = route_points = route_with_distance_and_index(org_route['points'])
    route['point_pairs'] = [get_point_pair_precalc(*point_pair) for point_pair in pairs(route_points)]
    route['simplfied_point_pairs'] = [get_point_pair_precalc(*point_pair) for point_pair in pairs(ramer_douglas_peucker(route_points, 500))]
    logger.debug('Route points: {}, simplified points: {}, distance: {}'.format(len(route_points), len(route['simplfied_point_pairs']), route_points[-1].distance))
    return route


def route_with_distance_and_index(route):
    dist = 0
    previous_point = None

    def get_point(i, point):
        nonlocal dist
        nonlocal previous_point
        point = IndexedPoint(*point, index=i)
        if previous_point:
            dist += distance(previous_point, point)
        point.distance = dist
        previous_point = point
        return point
    return [get_point(i, point) for i, point in enumerate(route)]


def distance(point1, point2):
    dist = norm(point1.pv - point2.pv)
    return dist


def pairs(items):
    itr = iter(items)
    item1 = next(itr)
    for item2 in itr:
        yield item1, item2
        item1 = item2


dist_attr_getter = operator.attrgetter('dist')


def ramer_douglas_peucker(points, epsilon):
    if len(points) > 2:
        c_points = (find_c_point(point, points[0], points[-1]) for point in points[1:-1])
        imax, (dmax, _) = max(enumerate(c_points), key=lambda item: item[1].dist)
    else:
        dmax = 0

    if dmax > epsilon:
        r1 = ramer_douglas_peucker(points[:imax + 2], epsilon)
        r2 = ramer_douglas_peucker(points[imax + 1:], epsilon)
        return r1[:-1] + r2
    else:
        return (points[0], points[-1])


find_closest_point_pair_routes_result = collections.namedtuple('closest_point_pair_route', ('route_i', 'route', 'point_pair', 'dist', 'point'))


def find_closest_point_pair_routes(routes, to_point, min_search_complex_dist, last_closest, break_out_dist):
    results = []
    if routes:

        special_routes = (0, )
        if last_closest and last_closest.route_i != 0:
            special_routes += (last_closest.route_i, )

        for route_i in reversed(special_routes):
            result = find_closest_point_pair_routes_result(
                route_i, routes[route_i], *find_closest_point_pair_route(routes[route_i], to_point, min_search_complex_dist))
            if result.dist < break_out_dist:
                return result
            results.append(result)

        for route_i, route in enumerate(routes):
            if route_i not in special_routes:
                results.append(find_closest_point_pair_routes_result(
                    route_i, route, *find_closest_point_pair_route(route, to_point, min_search_complex_dist)))

        return min(results, key=dist_attr_getter)


find_closest_point_pair_result = collections.namedtuple('closest_point_pair', ('point_pair', 'dist', 'point'))


def find_closest_point_pair_route(route, to_point, min_search_complex_dist):
    simplified_closest = find_closest_point_pair(route['simplfied_point_pairs'], to_point)
    if simplified_closest.dist > min_search_complex_dist or simplified_closest.point_pair[0].index == simplified_closest.point_pair[1].index - 1:
        return simplified_closest
    else:
        return find_closest_point_pair(route['point_pairs'][simplified_closest.point_pair[0].index: simplified_closest.point_pair[1].index + 1], to_point)


def find_closest_point_pair(point_pairs, to_point):
    with_c_points = [find_closest_point_pair_result(point_pair[:2], *find_c_point_from_precalc(to_point, *point_pair))
                     for point_pair in point_pairs]
    return min(with_c_points, key=dist_attr_getter)


find_c_point_result = collections.namedtuple('c_point', ('dist', 'point'))


def find_c_point(to_point, point1, point2):
    return find_c_point_from_precalc(to_point, *get_point_pair_precalc(point1, point2))


def find_c_point_from_precalc(to_point, point1, point2, c12, p1h, p2h, dp1p2):
    if (to_point.lat, to_point.lng) == (point1.lat, point1.lng):
        return find_c_point_result(0, point1)
    if (to_point.lat, to_point.lng) == (point2.lat, point2.lng):
        return find_c_point_result(0, point2)

    tpn = to_point.nv
    ctp = cross(tpn, c12, axis=0)
    c = unit(cross(ctp, c12, axis=0))
    sutable_c = None
    for co in (c, 0 - c):
        co_rs = co.reshape((3, ))
        dp1co = arccos(dot(p1h, co_rs))
        dp2co = arccos(dot(p2h, co_rs))
        if abs(dp1co + dp2co - dp1p2) < 0.000001:
            sutable_c = co
            break

    if sutable_c is not None:
        c_point_lat, c_point_lng = n_E2lat_lon(sutable_c)
        c_point = Point(lat=rad2deg(c_point_lat[0]), lng=rad2deg(c_point_lng[0]))
        c_dist = distance(to_point, c_point)
    else:
        c_dist, c_point = min(((distance(to_point, p), p) for p in (point1, point2)))

    return find_c_point_result(c_dist, c_point)


def get_point_pair_precalc(point1, point2):
    p1 = point1.nv
    p2 = point2.nv
    c12 = cross(p1, p2, axis=0)
    p1h = p1.reshape((3, ))
    p2h = p2.reshape((3, ))
    try:
        dp1p2 = arccos(dot(p1h, p2h))
    except Exception:
        print(p1h)
        print(p2h)
        print(dot(p1h, p2h))
        print((point1, point2))

        print(arccos(1))
        raise
    return point1, point2, c12, p1h, p2h, dp1p2


def get_equal_spaced_points(points, dist_between_points, start_dist=0):
    cum_dist = start_dist
    yield (points[0], cum_dist)
    dist_from_last_step = 0
    last_point = points[0]
    for point in points[1:]:
        point_distance = distance(last_point, point)
        point_dist_remaining = point_distance + dist_from_last_step
        while point_dist_remaining > dist_between_points:
            point_dist_remaining -= dist_between_points
            cum_dist += dist_between_points
            new_point_nv = interpolate((last_point.nv, point.nv), (point_distance - point_dist_remaining) / point_distance)
            new_point_lat, new_point_lng = n_E2lat_lon(new_point_nv)
            new_point = Point(rad2deg(new_point_lat[0]), rad2deg(new_point_lng[0]))
            new_point._nv = new_point_nv
            yield (new_point, cum_dist)
        dist_from_last_step = point_dist_remaining
        last_point = point
    cum_dist += dist_from_last_step
    yield (points[-1], cum_dist)
