import hashlib
import pprint
import unittest

import asynctest

from trackers.base import BlockedList, get_blocked_list, Tracker
from trackers.general import index_and_hash_list

source = index_and_hash_list([{'x': l} for l in 'Lorem ipsum dolor sit amet posuere.'], 0, hashlib.sha1())
# pprint.pprint(source)

expected_full = {
    'blocks': [{'end_hash': 'qRd1', 'end_index': 19, 'start_index': 0},
               {'end_hash': 'juYX', 'end_index': 24, 'start_index': 20},
               {'end_hash': 'k9VG', 'end_index': 29, 'start_index': 25}],
    'partial_block': [{'hash': 'LaqB', 'index': 30, 'x': 'u'},
                      {'hash': '7is4', 'index': 31, 'x': 'e'},
                      {'hash': 'yG0W', 'index': 32, 'x': 'r'},
                      {'hash': 'qmpi', 'index': 33, 'x': 'e'},
                      {'hash': 'opj8', 'index': 34, 'x': '.'}]
}


class Test(unittest.TestCase):
    maxDiff = None

    def check(self, source, existing, expected_full, expected_update, starting_block_len=5, entire_block=False):
        full, update = get_blocked_list(source, existing, starting_block_len, entire_block=entire_block)
        pprint.pprint(full)
        pprint.pprint(update)

        self.assertEqual(full, expected_full)
        self.assertEqual(update, expected_update)

    def test_empty_source(self):
        source = ()
        existing = {}
        expected_full = {'blocks': [], 'partial_block': []}
        expected_update = expected_full
        self.check(source, existing, expected_full, expected_update)

    def test_none(self):
        existing = {}
        expected_update = expected_full
        self.check(source, existing, expected_full, expected_update)

    def test_full(self):
        existing = expected_full
        expected_update = {}
        self.check(source, existing, expected_full, expected_update)

    def test_some_full_block(self):
        existing = {
            'blocks': [{'start_index': 0, 'end_index': 24, 'end_hash': 'juYX'}],
            'partial_block': []
        }
        expected_update = expected_full
        self.check(source, existing, expected_full, expected_update)

    def test_all_full_block(self):
        existing = {
            'blocks': [{'end_hash': 'qRd1', 'end_index': 19, 'start_index': 0},
                       {'end_hash': 'juYX', 'end_index': 24, 'start_index': 20},
                       {'end_hash': 'k9VG', 'end_index': 29, 'start_index': 25}],
            'partial_block': []
        }
        expected_update = {
            'add_block': [{'hash': 'LaqB', 'index': 30, 'x': 'u'},
                          {'hash': '7is4', 'index': 31, 'x': 'e'},
                          {'hash': 'yG0W', 'index': 32, 'x': 'r'},
                          {'hash': 'qmpi', 'index': 33, 'x': 'e'},
                          {'hash': 'opj8', 'index': 34, 'x': '.'}]
        }
        self.check(source, existing, expected_full, expected_update)

    def test_prev_partial_block(self):
        existing = {
            'blocks': [{'end_hash': 'juYX', 'end_index': 24, 'start_index': 0}],
            'partial_block': [{'hash': 'wARg', 'index': 25, 'x': 't'},
                              {'hash': 'Zwue', 'index': 26, 'x': ' '},
                              {'hash': 'QFbp', 'index': 27, 'x': 'p'}]
        }
        expected_update = expected_full
        self.check(source, existing, expected_full, expected_update)

    def test_partial_block(self):
        existing = {
            'blocks': [{'end_hash': 'qRd1', 'end_index': 19, 'start_index': 0},
                       {'end_hash': 'juYX', 'end_index': 24, 'start_index': 20},
                       {'end_hash': 'k9VG', 'end_index': 29, 'start_index': 25}],
            'partial_block': [{'hash': 'LaqB', 'index': 30, 'x': 'u'},
                              {'hash': '7is4', 'index': 31, 'x': 'e'},
                              {'hash': 'yG0W', 'index': 32, 'x': 'r'}]
        }
        expected_update = {
            'add_block': [{'hash': 'qmpi', 'index': 33, 'x': 'e'},
                          {'hash': 'opj8', 'index': 34, 'x': '.'}]
        }
        self.check(source, existing, expected_full, expected_update)

    def test_block_wrong_hash(self):
        existing = {
            'blocks': [{'start_index': 0, 'end_index': 24, 'end_hash': 'WRONG'},
                       {'start_index': 25, 'end_index': 29, 'end_hash': 'k9VG'}],
            'partial_block': [{'hash': 'LaqB', 'index': 30, 'x': 'u'},
                              {'hash': '7is4', 'index': 31, 'x': 'e'},
                              {'hash': 'yG0W', 'index': 32, 'x': 'r'},
                              {'hash': 'qmpi', 'index': 33, 'x': 'e'},
                              {'hash': 'opj8', 'index': 34, 'x': '.'}]
        }
        expected_update = expected_full
        self.check(source, existing, expected_full, expected_update)

    def test_partial_wrong_hash(self):
        existing = {
            'blocks': [{'end_hash': 'qRd1', 'end_index': 19, 'start_index': 0},
                       {'end_hash': 'juYX', 'end_index': 24, 'start_index': 20},
                       {'end_hash': 'k9VG', 'end_index': 29, 'start_index': 25}],
            'partial_block': [{'hash': 'LaqB', 'index': 30, 'x': 'u'},
                              {'hash': '7is4', 'index': 31, 'x': 'e'},
                              {'hash': 'WRONG', 'index': 32, 'x': 'r'},
                              {'hash': 'qmpi', 'index': 33, 'x': 'e'},
                              {'hash': 'opj8', 'index': 34, 'x': '.'}]
        }
        expected_update = {
            'partial_block': [{'hash': 'LaqB', 'index': 30, 'x': 'u'},
                              {'hash': '7is4', 'index': 31, 'x': 'e'},
                              {'hash': 'yG0W', 'index': 32, 'x': 'r'},
                              {'hash': 'qmpi', 'index': 33, 'x': 'e'},
                              {'hash': 'opj8', 'index': 34, 'x': '.'}]
        }

        self.check(source, existing, expected_full, expected_update)

    def test_existing_index_too_far(self):
        existing = {
            'blocks': [{'end_hash': 'qRd1', 'end_index': 19, 'start_index': 0},
                       {'end_hash': 'juYX', 'end_index': 24, 'start_index': 20},
                       {'end_hash': 'k9VG', 'end_index': 29, 'start_index': 25}],
            'partial_block': [{'hash': 'LaqB', 'index': 30, 'x': 'u'},
                              {'hash': '7is4', 'index': 31, 'x': 'e'},
                              {'hash': 'yG0W', 'index': 32, 'x': 'r'},
                              {'hash': 'qmpi', 'index': 33, 'x': 'e'},
                              {'hash': 'opj8', 'index': 34, 'x': '.'},
                              {'hash': 'fooo', 'index': 35, 'x': 'b'}]
        }
        expected_update = {
            'partial_block': [{'hash': 'LaqB', 'index': 30, 'x': 'u'},
                              {'hash': '7is4', 'index': 31, 'x': 'e'},
                              {'hash': 'yG0W', 'index': 32, 'x': 'r'},
                              {'hash': 'qmpi', 'index': 33, 'x': 'e'},
                              {'hash': 'opj8', 'index': 34, 'x': '.'}]
        }

        self.check(source, existing, expected_full, expected_update)

    def test_entire_block(self):
        existing = {}
        expected = {
            'blocks': [{'end_hash': 'opj8', 'end_index': 34, 'start_index': 0}],
            'partial_block': []
        }
        self.check(source, existing, expected, expected, entire_block=True)

    def test_entire_block_empty_source(self):
        existing = {}
        expected = {
            'blocks': [],
            'partial_block': []
        }
        self.check([], existing, expected, expected, entire_block=True)


class TestBlockedList(asynctest.TestCase):

    async def test_from_tracker(self):
        tracker = Tracker('test')

        new_update_callback = asynctest.CoroutineMock()

        BlockedList.from_tracker(tracker, new_update_callbacks=(new_update_callback, ))

        await tracker.new_points(source[:1])
        tracker.completed.set_result(None)
        await tracker.complete()

        new_update_callback.assert_called_once_with({'add_block': [{'x': 'L', 'index': 0, 'hash': 'u0Zw'}]})
