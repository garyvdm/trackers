import hashlib
import pprint
import unittest

from trackers.general import index_and_hash_list
from trackers.web_app import get_list_update

source = index_and_hash_list([{'x': l} for l in 'Lorem ipsum dolor sit amet posuere.'], 0, hashlib.sha1())
# pprint.pprint(source)

expected_new_existing = [
    {'hash': 'juYX', 'index': 24},
    {'hash': 'k9VG', 'index': 29},
    {'hash': 'opj8', 'index': 34}
]


class TestListUpdate(unittest.TestCase):
    maxDiff = None

    def check(self, source, existing, expected_update, expected_new_existing, starting_block_len=5):
        update, new_existing = get_list_update(source, existing, starting_block_len, compress_item=lambda item: item)
        pprint.pprint(update)
        pprint.pprint(new_existing)

        self.assertEqual(update, expected_update)
        self.assertEqual(new_existing, expected_new_existing)

    def test_empty_source(self):
        source = ()
        existing = ()
        expected_update = {'empty': True}
        expected_new_existing = []
        self.check(source, existing, expected_update, expected_new_existing)

    def test_none(self):
        existing = ()
        expected_update = {
            'blocks': [{'start_index': 0, 'end_index': 24, 'end_hash': 'juYX'},
                       {'start_index': 25, 'end_index': 29, 'end_hash': 'k9VG'}],
            'partial_block': [{'hash': 'LaqB', 'index': 30, 'x': 'u'},
                              {'hash': '7is4', 'index': 31, 'x': 'e'},
                              {'hash': 'yG0W', 'index': 32, 'x': 'r'},
                              {'hash': 'qmpi', 'index': 33, 'x': 'e'},
                              {'hash': 'opj8', 'index': 34, 'x': '.'}]
        }
        self.check(source, existing, expected_update, expected_new_existing)

    def test_full(self):
        existing = [
            {'hash': 'juYX', 'index': 24},
            {'hash': 'k9VG', 'index': 29},
            {'hash': 'opj8', 'index': 34}
        ]
        expected_update = {}
        self.check(source, existing, expected_update, expected_new_existing)

    def test_some_full_block(self):
        existing = [
            {'hash': 'juYX', 'index': 24},
        ]
        expected_update = {
            'blocks': [{'start_index': 25, 'end_index': 29, 'end_hash': 'k9VG'}],
            'partial_block': [{'hash': 'LaqB', 'index': 30, 'x': 'u'},
                              {'hash': '7is4', 'index': 31, 'x': 'e'},
                              {'hash': 'yG0W', 'index': 32, 'x': 'r'},
                              {'hash': 'qmpi', 'index': 33, 'x': 'e'},
                              {'hash': 'opj8', 'index': 34, 'x': '.'}]
        }
        self.check(source, existing, expected_update, expected_new_existing)

    def test_all_full_block(self):
        existing = [
            {'hash': 'juYX', 'index': 24},
            {'hash': 'k9VG', 'index': 29},
        ]
        expected_update = {
            'partial_block': [{'hash': 'LaqB', 'index': 30, 'x': 'u'},
                              {'hash': '7is4', 'index': 31, 'x': 'e'},
                              {'hash': 'yG0W', 'index': 32, 'x': 'r'},
                              {'hash': 'qmpi', 'index': 33, 'x': 'e'},
                              {'hash': 'opj8', 'index': 34, 'x': '.'}]
        }
        self.check(source, existing, expected_update, expected_new_existing)

    def test_prev_partial_block(self):
        existing = [
            {'hash': 'juYX', 'index': 24},
            {'hash': 'Vj-2', 'index': 28},
        ]
        expected_update = {
            'blocks': [{'start_index': 25, 'end_index': 29, 'end_hash': 'k9VG'}],
            'partial_block': [{'hash': 'LaqB', 'index': 30, 'x': 'u'},
                              {'hash': '7is4', 'index': 31, 'x': 'e'},
                              {'hash': 'yG0W', 'index': 32, 'x': 'r'},
                              {'hash': 'qmpi', 'index': 33, 'x': 'e'},
                              {'hash': 'opj8', 'index': 34, 'x': '.'}]
        }
        self.check(source, existing, expected_update, expected_new_existing)

    def test_partial_block(self):
        existing = [
            {'hash': 'juYX', 'index': 24},
            {'hash': 'k9VG', 'index': 29},
            {'hash': 'yG0W', 'index': 32}
        ]
        expected_update = {
            'partial_block': [{'hash': 'qmpi', 'index': 33, 'x': 'e'},
                              {'hash': 'opj8', 'index': 34, 'x': '.'}]
        }
        self.check(source, existing, expected_update, expected_new_existing)

    def test_block_wrong_hash(self):
        existing = [
            {'hash': 'WRONG', 'index': 24},
            {'hash': 'k9VG', 'index': 29},
            {'hash': 'opj8', 'index': 34}
        ]
        expected_update = {
            'blocks': [{'start_index': 0, 'end_index': 24, 'end_hash': 'juYX'},
                       {'start_index': 25, 'end_index': 29, 'end_hash': 'k9VG'}],
            'partial_block': [{'hash': 'LaqB', 'index': 30, 'x': 'u'},
                              {'hash': '7is4', 'index': 31, 'x': 'e'},
                              {'hash': 'yG0W', 'index': 32, 'x': 'r'},
                              {'hash': 'qmpi', 'index': 33, 'x': 'e'},
                              {'hash': 'opj8', 'index': 34, 'x': '.'}]
        }
        self.check(source, existing, expected_update, expected_new_existing)

    def test_partial_wrong_hash(self):
        existing = [
            {'hash': 'juYX', 'index': 24},
            {'hash': 'k9VG', 'index': 29},
            {'hash': 'WRONG', 'index': 34}
        ]
        expected_update = {
            'partial_block': [{'hash': 'LaqB', 'index': 30, 'x': 'u'},
                              {'hash': '7is4', 'index': 31, 'x': 'e'},
                              {'hash': 'yG0W', 'index': 32, 'x': 'r'},
                              {'hash': 'qmpi', 'index': 33, 'x': 'e'},
                              {'hash': 'opj8', 'index': 34, 'x': '.'}]
        }

        self.check(source, existing, expected_update, expected_new_existing)

    def test_existing_index_too_far(self):
        existing = [
            {'hash': 'juYX', 'index': 24},
            {'hash': 'k9VG', 'index': 29},
            {'hash': '', 'index': 50}
        ]
        expected_update = {
            'partial_block': [{'hash': 'LaqB', 'index': 30, 'x': 'u'},
                              {'hash': '7is4', 'index': 31, 'x': 'e'},
                              {'hash': 'yG0W', 'index': 32, 'x': 'r'},
                              {'hash': 'qmpi', 'index': 33, 'x': 'e'},
                              {'hash': 'opj8', 'index': 34, 'x': '.'}]
        }

        self.check(source, existing, expected_update, expected_new_existing)
