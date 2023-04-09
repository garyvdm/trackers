import unittest

from trackers.analyse import (
    Point,
    distance,
    find_c_point,
    get_equal_spaced_points,
    move_along_route,
    ramer_douglas_peucker,
    route_with_distance_and_index,
)


def round_point(point: Point, digits):
    return Point(lat=round(point.lat, digits), lng=round(point.lng, digits))


def test_find_c_point_between():
    result = find_c_point(Point(0.0001, 15), Point(0, 0), Point(0, 30))
    assert round(result.dist, 4) == 11.0574
    assert round_point(result.point, 4) == Point(lat=0.0, lng=15.0)


def test_find_c_point_outside():
    result = find_c_point(Point(0.0001, 40), Point(0, 0), Point(0, 30))
    assert result.point == Point(lat=0.0, lng=30)


def test_find_c_point_same():
    result = find_c_point(Point(0, 0), Point(0, 0), Point(0, 30))
    result.point == Point(lat=0, lng=0)


def test_ramer_douglas_peucker():
    points = [
        Point(0, 0),
        Point(0.0001, 15),
        Point(0, 30),
        Point(15, 45),
        Point(0, 60),
    ]

    simple_points = ramer_douglas_peucker(points, 20)
    assert simple_points == (
        Point(0, 0),
        Point(0, 30),
        Point(15, 45),
        Point(0, 60),
    )


def test_distance():
    dist = distance(Point(0, 0), Point(0.0001, 0))
    assert round(dist, 4) == 11.0574


def test_get_equal_spaced_points():
    points = list(
        get_equal_spaced_points(
            [
                Point(0, 0),
                Point(0, 0.001),
                Point(0.001, 0.001),
            ],
            50,
        )
    )
    assert [(point, round(cum_dist, 4)) for point, cum_dist in points] == [
        (Point(lat=0, lng=0), 0),
        (Point(lat=0.0, lng=0.000449), 50),
        (Point(lat=0.0, lng=0.000898), 100),
        (Point(lat=0.000350, lng=0.001), 150),
        (Point(lat=0.000802, lng=0.001), 200),
        (Point(lat=0.001, lng=0.001), 221.8938),
    ]


class TestMoveAlongRoute(unittest.TestCase):
    def test_non_indexed(self):
        point = move_along_route([Point(0, 0), Point(0, 0.2), Point(0, 1)], 100000)
        self.assertEqual(point, Point(lat=0.0, lng=0.898323))

    def test_indexed(self):
        point = move_along_route(route_with_distance_and_index([(0, 0), (0, 0.2), (0, 1)]), 100000)
        self.assertEqual(point, Point(lat=0.0, lng=0.898323))
