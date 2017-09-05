import unittest

from trackers.analyse import distance, find_c_point, Point, ramer_douglas_peucker


class TestFindCPoint(unittest.TestCase):

    def test_between(self):
        result = find_c_point(Point(0.0001, 15), Point(0, 0), Point(0, 30))
        self.assertEquals(result.dist, 11.057427582158148)
        self.assertEquals(result.point, Point(lat=0.0, lng=14.999999999999998))  # Rounding wtf?

    def test_outside(self):
        result = find_c_point(Point(0.0001, 40), Point(0, 0), Point(0, 30))
        self.assertEquals(result.point, Point(lat=0.0, lng=30))


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
        self.assertEquals(
            simple_points,
            (
                Point(0, 0),
                Point(0, 30),
                Point(15, 45),
                Point(0, 60),
            )
        )


class TestDistance(unittest.TestCase):

    def test_distance(self):
        dist = distance(Point(0, 0), Point(0.0001, 0))
        self.assertEqual(dist, 11.057427582158146)
