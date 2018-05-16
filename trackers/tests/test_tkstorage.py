import datetime
import unittest

from trackers.sources.tkstorage import data_split, msg_item_to_point


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

    # def test_battery(self):
    #     self.assertEqual(
    #         msg_item_to_point([1, 1525366678, 1, [b'864768011199921', b'ZC20', b'030518', b'165713', b'6', b'402', b'65535', b'255'], b'TK00']),
    #         {
    #             'tk_id': 'TK00',
    #             'server_time': datetime.datetime(2018, 5, 3, 18, 57, 58),
    #             'time': datetime.datetime(2018, 5, 3, 16, 57, 13),
    #         },  # We going to use *status* updates for better accuracy.
    #     )

    def test_pos1(self):
        self.assertEqual(
            msg_item_to_point([0, 1526394347, 1, b'(864768011193965,DW30,150518,A,2605.6699S,02756.5543E,0.20,142539,0.00,1604.20,12)', b'TK01']),
            {
                'tk_id': 'TK01',
                'num_sat': 12,
                'position': (-26.094498333333334, 27.942571666666666, 1604.2),
                'server_time': datetime.datetime(2018, 5, 15, 16, 25, 47),
                'time': datetime.datetime(2018, 5, 15, 16, 25, 39),
            },
        )

    def test_status_msg(self):
        self.assertEqual(
            msg_item_to_point([0, 1526394226, 1, b'(864768011193965,ZC03,150518,142343,$1 .GPS is positioning,0 Satellite\r\n2 .Sensor sensitivity: 1\r\n3 .Alert status: CALL\r\n4 .Check interval is set to 300 minute(s).\r\n5 .Routetrack data is uploading, Period is set to 99\r\n6 . Power: 100%$)', b'TK01']),
            {'server_time': datetime.datetime(2018, 5, 15, 16, 23, 46),
             'tk_id': 'TK01',
             'time': datetime.datetime(2018, 5, 15, 16, 23, 43),
             'battery': 100,
             'tk_status':
                 '1 .GPS is positioning,0 Satellite\r\n'
                 '2 .Sensor sensitivity: 1\r\n'
                 '3 .Alert status: CALL\r\n'
                 '4 .Check interval is set to 300 minute(s).\r\n'
                 '5 .Routetrack data is uploading, Period is set to 99\r\n'
                 '6 . Power: 100%'
             },
        )
