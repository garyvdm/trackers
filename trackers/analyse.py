import asyncio
import bisect
import collections
import copy
import logging
import operator
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from functools import partial
from itertools import chain
from operator import itemgetter
from typing import Any

from more_itertools import spy, take
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


from trackers.base import cancel_and_wait_task, general_fut_done_callback, Observable, Tracker
from trackers.contrib.dataclass_tools import add_slots

logger = logging.getLogger(__name__)

seterr(all='raise')


class AnalyseTracker(Tracker):

    @classmethod
    async def start(cls, org_tracker, analyse_start_time, routes,
                    track_break_time=timedelta(minutes=30),
                    track_break_dist=10000,
                    find_closest_cache=None,
                    processing_lock=None,
                    ):
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

        self.processing_lock = processing_lock if processing_lock else asyncio.Lock()

        self.completed = asyncio.ensure_future(self._completed())

        self.off_route_tracker = Tracker(f'offroute.{self.name}', completed=self.completed)
        self.pre_post_tracker = Tracker(f'prepost.{self.name}', completed=self.completed)
        self.not_pre_post_observable = Observable(self.logger)

        self.reset()
        self.do_est_finish_fut = None

        self.process_initial_points_fut = asyncio.ensure_future(self.on_new_points(self.org_tracker, self.org_tracker.points))
        self.process_initial_points_fut.add_done_callback(general_fut_done_callback)
        self.org_tracker.new_points_observable.subscribe(self.on_new_points)
        self.org_tracker.reset_points_observable.subscribe(self.on_reset_points)

        return self

    def reset(self):
        self.current_track_id = 0
        self.off_route_track_id = 0
        self.pre_post_track_id = 0

        self.finished = False
        self.going_forward = None
        self.total_dist = 0
        self.is_off_route = None
        self.pre_post = None

        self.prev_point_with_position = None
        self.prev_point_with_position_point = None
        self.prev_closest = None
        self.prev_route_dist = 0
        self.prev_route_dist_time = None
        self.prev_on_route = None

    def stop(self):
        self.process_initial_points_fut.cancel()
        if self.do_est_finish_fut:
            self.do_est_finish_fut.cancel()

        self.org_tracker.stop()

    async def _completed(self):
        try:
            await self.org_tracker.complete()
            await self.process_initial_points_fut
        finally:
            if self.do_est_finish_fut:
                try:
                    await self.do_est_finish_fut
                except asyncio.CancelledError:
                    pass

    async def on_reset_points(self, tracker):
        async with self.processing_lock:
            await self.reset_points()
            await self.off_route_tracker.reset_points()
            await self.pre_post_tracker.reset_points()
            self.reset()

    async def on_new_points(self, tracker, new_points):
        self.logger.debug(
            'analyse_tracker_new_points ({} points)'.format(len(new_points)))
        if self.do_est_finish_fut:
            cancel_and_wait_task(self.do_est_finish_fut)

        async with self.processing_lock:
            new_new_points = []
            new_off_route_points = []
            new_pre_post_points = []
            prev_submited_pre_post = self.pre_post

            async def submit_points():
                nonlocal new_new_points, new_off_route_points, new_pre_post_points, prev_submited_pre_post
                if new_new_points:
                    await self.new_points(new_new_points)
                    new_new_points = []
                if new_off_route_points:
                    await self.off_route_tracker.new_points(new_off_route_points)
                    new_off_route_points = []
                if new_pre_post_points:
                    await self.pre_post_tracker.new_points(new_pre_post_points)
                    new_pre_post_points = []
                if not self.pre_post and prev_submited_pre_post != self.pre_post:
                    await self.not_pre_post_observable()
                prev_submited_pre_post = self.pre_post

            log_time = datetime.now()
            log_i = 0
            last_route_point = self.routes[0]['points'][-1] if self.routes else None

            last_point_i = len(new_points) - 1
            did_slow_log = False

            for i, point in enumerate(new_points):
                point = copy.deepcopy(point)
                self.pre_post = pre_post = self.finished or (self.analyse_start_time and self.analyse_start_time > point['time'])
                if 'position' in point:
                    point_point = Point(*point['position'][:2])

                    if not pre_post:
                        if self.prev_route_dist_time:
                            time_from_prev_route_dist = point['time'] - self.prev_route_dist_time
                            max_travel_dist = 300000 * max(time_from_prev_route_dist.total_seconds(), 20) / 3600
                        elif self.analyse_start_time:
                            time_from_start = point['time'] - self.analyse_start_time
                            max_travel_dist = 300000 * max(time_from_start.total_seconds(), 20) / 3600
                        else:
                            max_travel_dist = None

                        closest = self.find_closest(
                            self.routes, point_point, 5000,
                            self.prev_closest.route_i if self.prev_closest else None,
                            250, self.prev_route_dist or 0, max_travel_dist)
                        if closest and closest.dist > 100000:
                            closest = None
                    else:
                        closest = None

                    if closest:
                        on_route = closest.dist < 250
                        route_dist = route_distance(closest.route, closest)
                        point['dist_route'] = round(route_dist)
                        self.going_forward = (not self.prev_route_dist) or route_dist > self.prev_route_dist

                        if on_route and 'elevation' in closest.route:
                            point['route_elevation'] = round(route_elevation(closest.route, route_dist))
                            if len(point['position']) > 2 and abs(point['route_elevation'] - point['position'][2]) > 500:
                                self.logger.debug('Removing inaccurate elevation.')
                                point['position'] = point['position'][:2]

                        if closest.route_i == 0 and abs(route_dist - last_route_point.distance) < 100:
                            # This is for when we get a point at the finish.
                            self.logger.debug('Finished')
                            self.set_finished()
                            point['finished_time'] = point['time']
                            point['rider_status'] = 'Finished'
                    else:
                        self.going_forward = None
                        on_route = False
                        route_dist = None

                    time_from_last = None
                    if self.prev_point_with_position:
                        prev_point = self.prev_point_with_position
                        point['time_from_last'] = time_from_last = point['time'] - prev_point['time']
                        if 'server_time' in point and 'server_time' in prev_point:
                            point['server_time_from_last'] = point['server_time'] - prev_point['server_time']

                    time = None
                    dist_from_last = None

                    if on_route and self.prev_on_route and closest.route_i == self.prev_closest.route_i:
                        dist_from_last = abs(
                            route_distance_no_adjust(closest.route, closest) -
                            route_distance_no_adjust(self.prev_closest.route, self.prev_closest))
                        time = time_from_last
                    elif on_route and not self.total_dist:
                        # Assume the last point was at the start of the route, and at the start of the event.
                        time = point['time'] - self.analyse_start_time
                        dist_from_last = route_dist
                    elif self.prev_point_with_position:
                        # as the crow flys distance
                        dist_from_last = distance(point_point, self.prev_point_with_position_point)
                        time = time_from_last

                    if dist_from_last:
                        if not pre_post:
                            self.total_dist += dist_from_last
                            point['dist'] = round(self.total_dist)
                        point['dist_from_last'] = round(dist_from_last)

                    speed_from_last = None
                    if time and dist_from_last:
                        seconds = time.total_seconds()
                        if seconds != 0:
                            speed_from_last = dist_from_last / seconds * 3.6
                            point['speed_from_last'] = round(speed_from_last, 1)

                        if time > self.track_break_time and dist_from_last > self.track_break_dist:
                            if pre_post:
                                self.pre_post_track_id += 1
                            else:
                                self.current_track_id += 1
                                self.off_route_track_id += 1

                        if not self.finished and i == last_point_i and closest and closest.route_i == 0 and abs(route_dist - last_route_point.distance) < 2000:
                            # This is for when they are near to the finish, and the tracker turns off too soon.
                            seconds_to_finish = abs(route_dist - last_route_point.distance) / (dist_from_last / seconds)
                            est_finish_time = point['time'] + timedelta(seconds=seconds_to_finish)
                            self.do_est_finish_fut = asyncio.ensure_future(self.do_est_finish(est_finish_time))

                    if not pre_post:
                        if not on_route or (not self.going_forward and speed_from_last and speed_from_last > 3):
                            # self.logger.info('off_route')
                            if not self.is_off_route and self.prev_point_with_position and self.prev_point_with_position['track_id'] == self.current_track_id:
                                new_off_route_points.append({'position': self.prev_point_with_position['position'], 'track_id': self.off_route_track_id})
                            self.is_off_route = True
                            new_off_route_points.append({'position': point['position'], 'track_id': self.off_route_track_id})
                        elif self.is_off_route:
                            new_off_route_points.append({'position': point['position'], 'track_id': self.off_route_track_id})
                            self.is_off_route = False
                            self.off_route_track_id += 1

                    self.prev_point_with_position = point
                    self.prev_point_with_position_point = point_point
                    self.prev_closest = closest
                    if route_dist:
                        self.prev_route_dist = route_dist
                        self.prev_route_dist_time = point['time']
                    else:
                        self.prev_route_dist_time = None
                    self.prev_on_route = on_route

                    point['track_id'] = self.pre_post_track_id if pre_post else self.current_track_id

                if not pre_post or 'rider_status' in point:
                    new_new_points.append(point)
                else:
                    new_pre_post_points.append(point)

                if 'rider_status' in point:
                    self.set_finished()
                    self.pre_post_track_id += 1

                is_last_point = i == last_point_i
                if i % 10 == 9 or is_last_point:
                    now = datetime.now()
                    log_time_delta = (now - log_time).total_seconds()
                    if log_time_delta >= 0.1:
                        await asyncio.sleep(0)
                    if log_time_delta >= 1 or (is_last_point and did_slow_log):
                        self.logger.info('{}/{} ({:.1f}%) points analysed at {:.2f} points/second.'.format(
                            i + 1, len(new_points), (i + 1) / len(new_points) * 100, (i - log_i) / log_time_delta))
                        log_time = now
                        log_i = i
                        did_slow_log = True
                        await submit_points()

            await submit_points()

    async def do_est_finish(self, time):
        delay = (time - datetime.now() + timedelta(minutes=5)).total_seconds()
        if delay > 0:
            await asyncio.sleep(delay)
        self.set_finished()
        point = {
            'finished_time': time,
            'time': time,
            'rider_status': 'Finished',
        }
        await self.new_points([point])

    def set_finished(self):
        super().set_finished()
        self.org_tracker.set_finished()

    def get_predicted_position(self, time):
        # TODO if time > a position received - then interpolate between those positions.
        pp = self.prev_point_with_position
        closest = self.prev_closest

        if (
                not self.pre_post and pp and time - pp['time'] < self.track_break_time and
                pp.get('speed_from_last', 0) > 3 and self.prev_on_route and self.going_forward):
            time_from_last = (time - pp['time']).total_seconds()
            dist_moved_from_last = pp['speed_from_last'] / 3.6 * time_from_last

            # Predicted to follow route if they are on the route and going forward.
            dist_route = pp['dist_route'] + dist_moved_from_last
            proceeding_route = list(chain(
                (closest.point, ),
                closest.route['points'][closest.point_pair[1].index:],
            ))
            # TODO continue main route
            point_point = move_along_route(proceeding_route, dist_moved_from_last)
            point = {
                'position': [point_point.lat, point_point.lng],
                'dist_route': round(dist_route),
            }
            if 'elevation' in closest.route:
                point['route_elevation'] = round(route_elevation(closest.route, dist_route))

            return point

        if not self.pre_post and pp:
            point = {
                'position': pp['position'],
            }
            if 'dist_route' in pp:
                point['dist_route'] = pp['dist_route']
            if 'route_elevation' in pp:
                point['route_elevation'] = pp['route_elevation']
            return point


@add_slots
@dataclass
class Point(object):
    lat: float
    lng: float
    _nv: Any = field(default=None, repr=False, compare=False)
    _pv: Any = field(default=None, repr=False, compare=False)

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

    @classmethod
    def from_nv(cls, nv, round_digits=6):
        lat, lng = n_E2lat_lon(nv)
        point = cls(round(rad2deg(lat[0]), round_digits), round(rad2deg(lng[0]), round_digits))
        point._nv = nv
        return point


@add_slots
@dataclass
class IndexedPoint(Point):
    index: int = field(default=None)
    distance: float = field(default=None)

    def to_point(self):
        return Point(self.lat, self.lng)


def get_analyse_routes(org_routes):
    return [get_analyse_route(route) for route in org_routes]


def get_analyse_route(org_route):
    route = copy.copy(org_route)
    route['points'] = route_points = route_with_distance_and_index(org_route['points'])
    route['point_pairs'] = [get_point_pair_precalc(*point_pair) for point_pair in pairs(route_points)]

    if route.get('simplified_points_indexes'):
        simplified_points = [route_points[i] for i in route['simplified_points_indexes']]
    else:
        logging.info('No pre-calculated simplified_points. Please run process_event_routes on event for faster start up.')
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
        # TODO extend split_point_range if no points found
        simplified_close_points = ramer_douglas_peucker(close_points, epsilon)
        closest_point = min(simplified_close_points, key=lambda point: abs(dist - point.distance))
        closest_index = closest_point.index
        simplified_points_section = ramer_douglas_peucker(points[last_index:closest_index + 1], epsilon)
        simplified_points_sections.append(simplified_points_section[:-1])
        last_index = closest_index

    simplified_points_sections.append(ramer_douglas_peucker(points[last_index:], epsilon))
    return list(chain.from_iterable(simplified_points_sections))


find_closest_point_pair_routes_result = collections.namedtuple('closest_point_pair_route', ('route_i', 'route', 'point_pair', 'dist', 'point'))


def find_closest_point_pair_routes_cache_key(routes, to_point, min_search_complex_dist, prev_closest_route_i, break_out_dist, prev_dist, max_travel_dist):
    return to_point.lat, to_point.lng, min_search_complex_dist, prev_closest_route_i, break_out_dist, prev_dist, max_travel_dist


def find_closest_point_pair_routes_pack(result):
    if result:
        return result.route_i, result.point_pair[0].index, result.dist, result.point.lat, result.point.lng


def find_closest_point_pair_routes_unpack(routes, packed):
    if packed:
        route_i, point_pair_index, dist, lat, lng = packed
        route = routes[route_i]
        point_pair = route['point_pairs'][point_pair_index]
        point = Point(lat, lng)
        return find_closest_point_pair_routes_result(route_i, route, point_pair, dist, point)


def find_closest_point_pair_routes(routes, to_point, min_search_complex_dist, prev_closest_route_i, break_out_dist, prev_dist, max_travel_dist):

    # print(to_point)
    # import math
    # debug = math.isclose(to_point.lat, -28.281551, rel_tol=0.000001) and math.isclose(to_point.lng, 28.93720, rel_tol=0.000001)
    # debug = to_point == Point(lat=-27.88121972370371, lng=27.919258810579777)
    # if debug:
    #     print('---------------------')
    #     print(prev_dist, max_travel_dist)

    len_routes = len(routes)
    if len_routes == 1:
        raw_result = find_closest_point_pair_route(routes[0], to_point, prev_dist, max_travel_dist)
        if raw_result:
            return find_closest_point_pair_routes_result(0, routes[0], *raw_result)
    elif len_routes > 1:
        results = []
        special_routes = (0, )
        if prev_closest_route_i:
            special_routes += (prev_closest_route_i, )

        for route_i in reversed(special_routes):
            route = routes[route_i]
            raw_result = find_closest_point_pair_route(route, to_point, prev_dist, max_travel_dist)
            if raw_result:
                result = find_closest_point_pair_routes_result(route_i, route, *raw_result)
                if result.dist < break_out_dist:
                    return result
                results.append(result)

        for route_i, route in enumerate(routes):
            if route_i not in special_routes:
                raw_result = find_closest_point_pair_route(route, to_point, prev_dist, max_travel_dist)
                if raw_result:
                    results.append(find_closest_point_pair_routes_result(route_i, route, *raw_result))

        if results:
            return min(results, key=dist_attr_getter)


find_closest_point_pair_result = collections.namedtuple('closest_point_pair', ('point_pair', 'dist', 'point'))


def find_closest_point_pair_route(route, to_point, prev_dist, max_travel_dist):
    if max_travel_dist:
        min_route_dist = prev_dist - max_travel_dist
        max_route_dist = prev_dist + max_travel_dist
        if route['main']:
            get_point_distance = lambda point: point.distance
        else:
            get_point_distance = lambda point: point.distance * route['dist_factor'] + route['start_distance']

        test_point_pair = lambda point_pair: get_point_distance(point_pair[1]) > min_route_dist and get_point_distance(point_pair[0]) <= max_route_dist
    else:
        test_point_pair = lambda point_pair: True

    simplified_c_points = (
        find_closest_point_pair_result(point_pair[:2], *find_c_point_from_precalc(to_point, *point_pair))
        for point_pair in route['simplfied_point_pairs']
        if test_point_pair(point_pair)
    )
    simplified_c_points_sorted = sorted(simplified_c_points, key=dist_attr_getter)
    simplified_c_points_top = take(4, simplified_c_points_sorted)
    simplified_c_points_filtered = filter(lambda closest: closest.dist < 100000, simplified_c_points_top)

    route_point_pairs = route['point_pairs']
    route_point_pairs_filtered = chain.from_iterable((
        route_point_pairs[simplified_c_point.point_pair[0].index: simplified_c_point.point_pair[1].index + 1]
        for simplified_c_point in simplified_c_points_filtered))

    with_c_points = (
        find_closest_point_pair_result(point_pair[:2], *find_c_point_from_precalc(to_point, *point_pair))
        for point_pair in route_point_pairs_filtered
        if test_point_pair(point_pair)
    )

    # debug = to_point == Point(lat=-27.88121972370371, lng=27.919258810579777)
    # if math.isclose(to_point.lat, -28.041518, rel_tol=0.000001) and math.isclose(to_point.lng, 27.911506, rel_tol=0.000001):
    #     pprint.pprint(with_c_points)
    #     print(len(point_pairs) , len(with_c_points), max_travel_dist)

    head, with_c_points = spy(with_c_points)

    if head:
        circular_range = route.get('circular_range')
        if prev_dist is not None and circular_range:
            def min_key(closest):
                if closest.dist > 100000:
                    # Short cut to avoid unnecessary route_distance calls
                    return float("inf")
                rd = route_distance(route, closest)
                move_distance = rd - prev_dist
                if move_distance < 0:
                    move_distance = move_distance * -10
                try:
                    move_distance_penalty = pow(3, move_distance / 5000)
                except FloatingPointError:
                    move_distance_penalty = float("inf")
                rank = closest.dist + min(move_distance_penalty, 100000)
                # if debug:
                #     print(rank, move_distance, move_distance_penalty, closest, )
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


def route_distance_no_adjust(route, closest):
    prev_route_point = closest.point_pair[0]
    start_distance = 0 if route['main'] else route['start_distance']
    return round(prev_route_point.distance + distance(prev_route_point, closest.point) + start_distance)


def route_elevation(route, route_dist):
    elevation = route['elevation']
    if route['main']:
        dist_on_route = route_dist
    else:
        dist_on_route = (route_dist - route['start_distance']) / route['dist_factor']
    point2_i = bisect.bisect(KeyifyList(elevation, itemgetter(3)), dist_on_route)
    if point2_i == len(elevation):
        point2_i -= 1

    point2 = elevation[point2_i]
    point1 = elevation[point2_i - 1]

    dist_factor = (dist_on_route - point2[3]) / (point2[3] - point1[3])
    return ((point2[2] - point1[2]) * dist_factor) + point2[2]


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
        c_dist, c_point = min(((distance(to_point, p), p) for p in (point1, point2)), key=itemgetter(0))

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
            new_point = Point.from_nv(new_point_nv, round_digits=round_digits)
            yield (new_point, cum_dist)
        dist_from_last_step = point_dist_remaining
        last_point = point
    cum_dist += dist_from_last_step
    yield (points[-1], cum_dist)


def move_along_route(route, dist):
    for i, (point1, point2) in enumerate(pairs(route)):
        dist_between = point2.distance - point1.distance if isinstance(point1, IndexedPoint) and isinstance(point2, IndexedPoint) else distance(point1, point2)
        if dist > dist_between:
            dist -= dist_between
        else:
            ti = dist / dist_between
            nv = interpolate((point1.nv, point2.nv), ti)
            return Point.from_nv(nv)
    else:
        return point2


class KeyifyList(object):
    def __init__(self, inner, key):
        self.inner = inner
        self.key = key

    def __len__(self):
        return len(self.inner)

    def __getitem__(self, k):
        return self.key(self.inner[k])
