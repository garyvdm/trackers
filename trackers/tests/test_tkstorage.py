import datetime
import unittest
import unittest.mock

from trackers.sources.tkstorage import data_split, msg_item_to_point, ZC03_parse


class TestDataSplit(unittest.TestCase):

    def test_simple(self):
        self.assertEqual(data_split('foo,bar'), ['foo', 'bar'])

    def test_quoted(self):
        self.assertEqual(data_split('foo,$moo,cow$,bar'), ['foo', 'moo,cow', 'bar'])

    def test_quoted_last(self):
        self.assertEqual(data_split('foo,bar,$moo,cow$'), ['foo', 'bar', 'moo,cow'])


class TestToPoint(unittest.TestCase):

    maxDiff = None

    def test_connect(self):
        self.assertEqual(
            msg_item_to_point([0, 1525366636, 0, [b'::1', 60652, 0, 0], None]),
            None,
        )

    def test_battery(self):
        self.assertEqual(
            msg_item_to_point([1, 1525366678, 1, '(864768011199921,ZC20,030518,165713,6,402,65535,255)', 'TK00']),
            {
                'tk_id': 'TK00',
                'server_time': datetime.datetime(2018, 5, 3, 18, 57, 58),
                'time': datetime.datetime(2018, 5, 3, 18, 57, 13),
                'battery': 82.66666666666674,
                'battery_voltage': 4.0200000000000005,
            },
        )

    def test_pos1(self):
        self.assertEqual(
            msg_item_to_point([0, 1526394347, 1, '(864768011193965,DW30,150518,A,2605.6699S,02756.5543E,0.20,142539,0.00,1604.20,12)', 'TK01']),
            {
                'tk_id': 'TK01',
                'num_sat': 12,
                'position': (-26.094498333333334, 27.942571666666666, 1604.2),
                'server_time': datetime.datetime(2018, 5, 15, 16, 25, 47),
                'time': datetime.datetime(2018, 5, 15, 16, 25, 39),
            },
        )

    def test_pos2(self):
        self.assertEqual(
            msg_item_to_point([0, 1526394347, 1, '(864768011468102,DW30,080618,A,2752.87996S,02755.19665E,1.910,202919,000.0,1538.20,12,0)', 'TK01']),
            {
                'tk_id': 'TK01',
                'num_sat': 12,
                'position': (-27.881332666666665, 27.919944166666667, 1538.2),
                'server_time': datetime.datetime(2018, 5, 15, 16, 25, 47),
                'time': datetime.datetime(2018, 6, 8, 22, 29, 19),
            },
        )

    def test_pos3(self):
        self.assertEqual(
            msg_item_to_point([1990, 1528548291.011041, 1, '(864768011199962,DW3B,050316,A,2754.4558S,02759.0423E,6.82,124449,279.65,1537.50,10,0)', 'TK05']),
            {
                'tk_id': 'TK05',
                'num_sat': 10,
                'position': (-27.907596666666667, 27.984038333333334, 1537.5),
                'server_time': datetime.datetime(2018, 6, 9, 14, 44, 51, 11041),
                'time':        datetime.datetime(2018, 6, 9, 14, 44, 49),  # NOQA
            },
        )

    def test_zero_alt(self):
        self.assertEqual(
            msg_item_to_point([1990, 1528548291.011041, 1, '(864768011199962,DW3B,050316,A,2754.4558S,02759.0423E,6.82,124449,279.65,0,10,0)', 'TK05']),
            {
                'tk_id': 'TK05',
                'num_sat': 10,
                'position': (-27.907596666666667, 27.984038333333334),
                'server_time': datetime.datetime(2018, 6, 9, 14, 44, 51, 11041),
                'time':        datetime.datetime(2018, 6, 9, 14, 44, 49),  # NOQA
            },
        )

    def test_ZC03_msg(self):

        def stub_ZC03_parse(msg):
            yield 'foo', 'bar'

        with unittest.mock.patch('trackers.sources.tkstorage.ZC03_parse', stub_ZC03_parse):
            self.assertEqual(
                msg_item_to_point([0, 1526394226, 1, '(864768011193965,ZC03,150518,142343,$stuff$)', 'TK01']),
                {'server_time': datetime.datetime(2018, 5, 15, 16, 23, 46),
                 'tk_id': 'TK01',
                 'time': datetime.datetime(2018, 5, 15, 16, 23, 43),
                 'foo': 'bar',
                 },
            )


class TestZC03Parse(unittest.TestCase):

    def test_status(self):
        self.assertEqual(
            dict(ZC03_parse('1 .GPS is positioning,0 Satellite\r\n2 .Sensor sensitivity: 1\r\n3 .Alert status: CALL\r\n4 .Check interval is set to 5 minute(s).\r\n5 .Routetrack data is uploading, Period is set to 99\r\n6 . Power: 98%')),
            {
                'battery': 98,
                'tk_check': 5,
                'tk_routetrack': True,
                'tk_status':
                    '1 .GPS is positioning,0 Satellite\r\n'
                    '2 .Sensor sensitivity: 1\r\n'
                    '3 .Alert status: CALL\r\n'
                    '4 .Check interval is set to 5 minute(s).\r\n'
                    '5 .Routetrack data is uploading, Period is set to 99\r\n'
                    '6 . Power: 98%'
            }
        )

    def test_routetrackoff(self):
        self.assertEqual(
            dict(ZC03_parse('Notice: System has ended routetrack function.')),
            {'tk_routetrack': False, },
        )

    def test_routetrackon(self):
        self.assertEqual(
            dict(ZC03_parse('Notice: Routetrack function is set to always on')),
            {'tk_routetrack': True, },
        )

    def test_routetrack_time(self):
        self.assertEqual(
            dict(ZC03_parse('Notice: System has entered routetrack function for 10 hour(s).')),
            {'tk_routetrack': 10, },
        )

    def test_rsampling(self):
        self.assertEqual(
            dict(ZC03_parse('Notice: Track sampling interval is 60 second(s).')),
            {'tk_rsampling': 60, },
        )

    def test_rupload(self):
        self.assertEqual(
            dict(ZC03_parse('Notice: Upload time interval is set to 60 second(s)')),
            {'tk_rupload': 60, },
        )

    def test_checkoff(self):
        self.assertEqual(
            dict(ZC03_parse('Notice: System has ended check function.')),
            {'tk_check': False, },
        )

    def test_checkon(self):
        self.assertEqual(
            dict(ZC03_parse('Notice: Check interval is set to 5 minute(s).')),
            {'tk_check': 5, },
        )
