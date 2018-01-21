import asyncio
import collections
import copy
import datetime
import logging
import operator
from functools import partial
from itertools import chain

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
    lat_lon2n_E,
    n_E2lat_lon,
    n_EB_E2p_EB_E,
    unit,
)

try:
    from nvector import interpolate
except ImportError:
    # Copy paste hack till this gets released: https://github.com/garyvdm/Nvector/pull/1
    from numpy import nan

    def interpolate(path, ti):
        """
        Return the interpolated point along the path

        Parameters
        ----------
        path: tuple of n-vectors (positionA, po)

        ti: real scalar
            interpolation time assuming position A and B is at t0=0 and t1=1,
            respectively.

        Returns
        -------
        point: Nvector
            point of interpolation along path
        """

        n_EB_E_t0, n_EB_E_t1 = path
        n_EB_E_ti = unit(n_EB_E_t0 + ti * (n_EB_E_t1 - n_EB_E_t0), norm_zero_vector=nan)
        return n_EB_E_ti


from trackers.base import Tracker

logger = logging.getLogger(__name__)

seterr(all='raise')


class AnalyseTracker(Tracker):

    @classmethod
    async def start(cls, org_tracker, analyse_start_time, routes, track_break_time=datetime.timedelta(minutes=20),
                    track_break_dist=10000, find_closest_cache=None):
        self = cls('analysed.{}'.format(org_tracker.name))
        self.org_tracker = org_tracker
        self.analyse_start_time = analyse_start_time
        self.routes = routes
        self.track_break_time = track_break_time
        self.track_break_dist = track_break_dist
        if find_closest_cache:
            find_closest_cache.func = find_closest_point_pair_routes
            find_closest_cache.key = find_closest_point_pair_routes_cache_key
            find_closest_cache.unpack = partial(find_closest_point_pair_routes_unpack, routes)
            find_closest_cache.pack = find_closest_point_pair_routes_pack
            self.find_closest = find_closest_cache
        else:
            self.find_closest = find_closest_point_pair_routes

        self.make_inactive_fut = None
        self.prev_point_with_position = None
        self.prev_point_with_position_point = None
        self.current_track_id = 0
        self.status = None
        self.prev_closest_route_point = None
        self.dist_ridden = 0
        self.prev_route_dist = 0
        self.finished = False

        self.completed = asyncio.ensure_future(self._completed())
        await self.on_new_points(org_tracker, org_tracker.points)
        org_tracker.new_points_observable.subscribe(self.on_new_points)

        return self

    def stop(self):
        self.org_tracker.stop()
        if self.make_inactive_fut:
            self.make_inactive_fut.cancel()

    async def _completed(self):
        await self.org_tracker.complete()
        if self.make_inactive_fut:
            try:
                await self.make_inactive_fut
            except asyncio.CancelledError:
                pass
            self.make_inactive_fut = None

    async def on_new_points(self, tracker, new_points):
        self.logger.debug('analyse_tracker_new_points ({} points)'.format(len(new_points)))

        new_new_points = []
        prev_point_with_position = None
        log_time = datetime.datetime.now()
        log_i = 0
        last_route_point = self.routes[0]['points'][-1] if self.routes else None

        last_point_i = len(new_points) - 1
        did_slow_log = False

        for i, point in enumerate(new_points):
            point = copy.deepcopy(point)
            if 'position' in point:
                if self.make_inactive_fut:
                    self.make_inactive_fut.cancel()
                    try:
                        await self.make_inactive_fut
                    except asyncio.CancelledError:
                        pass
                    self.make_inactive_fut = None

                point_point = Point(*point['position'][:2])

                if self.analyse_start_time and self.analyse_start_time <= point['time']:
                    closest = self.find_closest(
                        self.routes, point_point, 1000,
                        self.prev_closest_route_point.route_i if self.prev_closest_route_point else None,
                        250, self.prev_route_dist)
                    if closest and closest.dist > 5000:
                        closest = None

                    if closest:
                        point['dist_route'] = self.prev_route_dist = route_distance(closest.route, closest)

                        if not self.finished:
                            if closest.route_i == 0 and abs(point['dist_route'] - last_route_point.distance) < 100:
                                self.logger.debug('Finished')
                                self.finished = True
                                point['finished_time'] = point['time']
                                point['rider_status'] = 'Finished'

                    self.prev_closest_route_point = closest

                    if self.prev_point_with_position_point:
                        prev_point = self.prev_point_with_position
                        dist = distance(point_point, self.prev_point_with_position_point)
                        time = point['time'] - prev_point['time']
                        if time > self.track_break_time and dist > self.track_break_dist:
                            self.current_track_id += 1
                            self.apply_status_to_point(
                                {'time': prev_point['time'] + self.track_break_time},
                                'Inactive', new_new_points.append)
                        point['dist_from_last'] = round(dist)
                        seconds = time.total_seconds()
                        if seconds != 0:
                            point['speed_from_last'] = round(dist / seconds * 3.6, 1)
                        self.dist_ridden += dist
                        point['dist_ridden'] = round(self.dist_ridden)

                    self.prev_point_with_position_point = point_point

                # TODO what about status from source tracker?
                self.apply_status_to_point(point, 'Active')

                self.prev_point_with_position = prev_point_with_position = point
                point['track_id'] = self.current_track_id

            new_new_points.append(point)

            is_last_point = i == last_point_i
            if i % 10 == 9 or is_last_point:
                now = datetime.datetime.now()
                log_time_delta = (now - log_time).total_seconds()
                if log_time_delta >= 5 or (is_last_point and did_slow_log):
                    self.logger.info('{}/{} ({:.1f}%) points analysed at {:.2f} points/second.'.format(
                        i, len(new_points), i / (len(new_points) - 1) * 100, (i - log_i) / log_time_delta))
                    log_time = now
                    log_i = i
                    did_slow_log = True
                    if new_new_points:
                        await self.new_points(new_new_points)
                        new_new_points = []

            if self.finished:
                break

        if new_new_points:
            await self.new_points(new_new_points)

        if prev_point_with_position:
            self.make_inactive_fut = asyncio.ensure_future(
                self.make_inactive(prev_point_with_position, self.track_break_time))

    async def make_inactive(self, prev_point_with_position, track_break_time):
        delay = (prev_point_with_position['time'] - datetime.datetime.now() + track_break_time).total_seconds()
        if delay > 0:
            await asyncio.sleep(delay)
        if prev_point_with_position == self.prev_point_with_position:
            new_new_points = []
            self.apply_status_to_point(
                {'time': prev_point_with_position['time'] + track_break_time},
                'Inactive', new_new_points.append)
            await asyncio.shield(self.new_points(new_new_points))

    def apply_status_to_point(self, point, status, append_to=None):
        if status != self.status:
            point['status'] = status
            self.status = status
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

    if not route.get('split_at_dist'):
        simplified_points = ramer_douglas_peucker(route_points, 500)
    else:
        simplified_points = ramer_douglas_peucker_sections(route_points, 500, route['split_at_dist'], route['split_point_range'])

    route['simplfied_point_pairs'] = [get_point_pair_precalc(*point_pair) for point_pair in pairs(simplified_points)]
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


def ramer_douglas_peucker_sections(points, epsilon, split_at_dist, split_point_range):
    simplified_points_sections = []
    last_index = 0
    for dist in split_at_dist:
        min_dist = dist - split_point_range
        max_dist = dist + split_point_range
        close_points = [point for point in points if min_dist <= point.distance < max_dist]
        simplified_close_points = ramer_douglas_peucker(close_points, epsilon)
        closest_point = min(simplified_close_points, key=lambda point: abs(dist - point.distance))
        closest_index = closest_point.index
        simplified_points_section = ramer_douglas_peucker(points[last_index:closest_index + 1], epsilon)
        simplified_points_sections.append(simplified_points_section[:-1])
        last_index = closest_index

    simplified_points_sections.append(ramer_douglas_peucker(points[last_index:], epsilon))
    return list(chain.from_iterable(simplified_points_sections))


find_closest_point_pair_routes_result = collections.namedtuple('closest_point_pair_route', ('route_i', 'route', 'point_pair', 'dist', 'point'))


def find_closest_point_pair_routes_cache_key(routes, to_point, min_search_complex_dist, prev_closest_route_i, break_out_dist, prev_dist):
    return to_point.lat, to_point.lng, min_search_complex_dist, prev_closest_route_i, break_out_dist, prev_dist


def find_closest_point_pair_routes_pack(result):
    return result.route_i, result.point_pair[0].index, result.dist, result.point.lat, result.point.lng


def find_closest_point_pair_routes_unpack(routes, packed):
    route_i, point_pair_index, dist, lat, lng = packed
    route = routes[route_i]
    point_pair = route['point_pairs'][point_pair_index]
    point = Point(lat, lng)
    return find_closest_point_pair_routes_result(route_i, route, point_pair, dist, point)


def find_closest_point_pair_routes(routes, to_point, min_search_complex_dist, prev_closest_route_i, break_out_dist, prev_dist):
    results = []
    if routes:
        special_routes = (0, )
        if prev_closest_route_i:
            special_routes += (prev_closest_route_i, )

        for route_i in reversed(special_routes):
            route = routes[route_i]
            result = find_closest_point_pair_routes_result(
                route_i, route, *find_closest_point_pair_route(route, to_point, min_search_complex_dist, prev_dist))
            if result.dist < break_out_dist:
                return result
            results.append(result)

        for route_i, route in enumerate(routes):
            if route_i not in special_routes:
                results.append(find_closest_point_pair_routes_result(
                    route_i, route, *find_closest_point_pair_route(route, to_point, min_search_complex_dist, prev_dist)))

        return min(results, key=dist_attr_getter)


find_closest_point_pair_result = collections.namedtuple('closest_point_pair', ('point_pair', 'dist', 'point'))


def find_closest_point_pair_route(route, to_point, min_search_complex_dist, prev_dist=None):
    simplified_closest = find_closest_point_pair(route, route['simplfied_point_pairs'], to_point, prev_dist)
    if simplified_closest.dist > min_search_complex_dist or simplified_closest.point_pair[0].index == simplified_closest.point_pair[1].index - 1:
        return simplified_closest
    else:
        return find_closest_point_pair(route, route['point_pairs'][simplified_closest.point_pair[0].index: simplified_closest.point_pair[1].index + 1], to_point, prev_dist)


def find_closest_point_pair(route, point_pairs, to_point, prev_dist):
    with_c_points = [find_closest_point_pair_result(point_pair[:2], *find_c_point_from_precalc(to_point, *point_pair))
                     for point_pair in point_pairs]

    if prev_dist is not None:
        def min_key(closest):
            move_distance = abs(route_distance(route, closest) - prev_dist)
            move_distnace_adj = pow(2, (move_distance - 50000) / 1000)
            rank = closest.dist + move_distnace_adj
            # print(move_distance, n, move_distnace_adj, closest.dist, rank)
            return rank
    else:
        min_key = dist_attr_getter

    r = min(with_c_points, key=min_key)
    # print(f'return {r}')
    return r


def route_distance(route, closest):
    prev_route_point = closest.point_pair[0]
    if route['main']:
        return round(prev_route_point.distance + distance(prev_route_point, closest.point))
    else:
        alt_route_dist = prev_route_point.distance + distance(prev_route_point, closest.point)
        return round(alt_route_dist * route['dist_factor'] + route['start_distance'])


find_c_point_result = collections.namedtuple('c_point', ('dist', 'point'))


def find_c_point(to_point, point1, point2):
    return find_c_point_from_precalc(to_point, *get_point_pair_precalc(point1, point2))


def arccos_limit(n):
    if n > 1:
        return 1
    if n < -1:
        return -1
    return n


def find_c_point_from_precalc(to_point, point1, point2, c12, p1h, p2h, dp1p2):
    tpn = to_point.nv
    ctp = cross(tpn, c12, axis=0)
    try:
        c = unit(cross(ctp, c12, axis=0))
    except Exception:
        print((to_point, point1, point2))
        raise
    sutable_c = None
    for co in (c, 0 - c):
        co_rs = co.reshape((3, ))
        dp1co = arccos(arccos_limit(dot(p1h, co_rs)))
        dp2co = arccos(arccos_limit(dot(p2h, co_rs)))
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
    dp1p2 = arccos(dot(p1h, p2h))
    return point1, point2, c12, p1h, p2h, dp1p2


def get_equal_spaced_points(points, dist_between_points, start_dist=0, round_digits=6):
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
            new_point = Point(round(rad2deg(new_point_lat[0]), round_digits), round(rad2deg(new_point_lng[0]), round_digits))
            new_point._nv = new_point_nv
            yield (new_point, cum_dist)
        dist_from_last_step = point_dist_remaining
        last_point = point
    cum_dist += dist_from_last_step
    yield (points[-1], cum_dist)
