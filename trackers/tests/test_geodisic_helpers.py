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


class TestFindCPoint(unittest.TestCase):
    def test_between(self):
        result = find_c_point(Point(0.0001, 15), Point(0, 0), Point(0, 30))
        self.assertEqual(result.dist, 11.057427582158148)
        self.assertEqual(result.point, Point(lat=0.0, lng=14.999999999999998))  # Rounding wtf?

    def test_outside(self):
        result = find_c_point(Point(0.0001, 40), Point(0, 0), Point(0, 30))
        self.assertEqual(result.point, Point(lat=0.0, lng=30))

    def test_same(self):
        result = find_c_point(Point(0, 0), Point(0, 0), Point(0, 30))
        self.assertEqual(result.point, Point(lat=0, lng=0))


class TestRamerDouglasPeucker(unittest.TestCase):
    def test_ramer_douglas_peucker(self):
        points = [
            Point(0, 0),
            Point(0.0001, 15),
            Point(0, 30),
            Point(15, 45),
            Point(0, 60),
        ]

        simple_points = ramer_douglas_peucker(points, 20)
        self.assertEqual(
            simple_points,
            (
                Point(0, 0),
                Point(0, 30),
                Point(15, 45),
                Point(0, 60),
            ),
        )


class TestDistance(unittest.TestCase):
    def test_distance(self):
        dist = distance(Point(0, 0), Point(0.0001, 0))
        self.assertEqual(dist, 11.057427582158146)


class TestGetEqualSpacedPoints(unittest.TestCase):
    def test(self):
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
        self.assertEqual(
            points,
            [
                (Point(lat=0, lng=0), 0),
                (Point(lat=0.0, lng=0.000449), 50),
                (Point(lat=0.0, lng=0.000898), 100),
                (Point(lat=0.000350, lng=0.001), 150),
                (Point(lat=0.000802, lng=0.001), 200),
                (Point(lat=0.001, lng=0.001), 221.89376661216434),
            ],
        )


class TestMoveAlongRoute(unittest.TestCase):
    def test_non_indexed(self):
        point = move_along_route([Point(0, 0), Point(0, 0.2), Point(0, 1)], 100000)
        self.assertEqual(point, Point(lat=0.0, lng=0.898323))

    def test_indexed(self):
        point = move_along_route(route_with_distance_and_index([(0, 0), (0, 0.2), (0, 1)]), 100000)
        self.assertEqual(point, Point(lat=0.0, lng=0.898323))
