import hashlib
import pprint
import unittest

from trackers.general import index_and_hash_list
from trackers.web_app import get_list_update

source = index_and_hash_list([{'x': l} for l in 'Lorem ipsum dolor sit amet posuere. '], 0, hashlib.sha1())

expected_new_existing = [
    {'index': 9, 'hash': 'cuBt'},
    {'index': 19, 'hash': 'qRd1'},
    {'index': 29, 'hash': 'k9VG'},
    {'index': 35, 'hash': 'oKCY'},
]
# pprint.pprint(source)


class TestListUpdate(unittest.TestCase):
    maxDiff = None

    def check(self, source, existing, expected_update, expected_new_existing):
        update, new_existing = get_list_update(source, existing, 10, compress_item=lambda item: item)
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
            'full_blocks': [{'end_hash': 'cuBt', 'end_index': 9, 'start_index': 0},
                            {'end_hash': 'qRd1', 'end_index': 19, 'start_index': 10},
                            {'end_hash': 'k9VG', 'end_index': 29, 'start_index': 20}],
            'partial_block': [{'hash': 'LaqB', 'index': 30, 'x': 'u'},
                              {'hash': '7is4', 'index': 31, 'x': 'e'},
                              {'hash': 'yG0W', 'index': 32, 'x': 'r'},
                              {'hash': 'qmpi', 'index': 33, 'x': 'e'},
                              {'hash': 'opj8', 'index': 34, 'x': '.'},
                              {'hash': 'oKCY', 'index': 35, 'x': ' '}]
        }
        self.check(source, existing, expected_update, expected_new_existing)

    def test_full(self):
        existing = (
            {'index': 9, 'hash': 'cuBt'},
            {'index': 19, 'hash': 'qRd1'},
            {'index': 29, 'hash': 'k9VG'},
            {'index': 35, 'hash': 'oKCY'},
        )
        expected_update = {}
        self.check(source, existing, expected_update, expected_new_existing)

    def test_some_full_block(self):
        existing = (
            {'index': 9, 'hash': 'cuBt'},
            {'index': 19, 'hash': 'qRd1'},
        )
        expected_update = {
            'full_blocks': [{'end_hash': 'k9VG', 'end_index': 29, 'start_index': 20}],
            'partial_block': [{'hash': 'LaqB', 'index': 30, 'x': 'u'},
                              {'hash': '7is4', 'index': 31, 'x': 'e'},
                              {'hash': 'yG0W', 'index': 32, 'x': 'r'},
                              {'hash': 'qmpi', 'index': 33, 'x': 'e'},
                              {'hash': 'opj8', 'index': 34, 'x': '.'},
                              {'hash': 'oKCY', 'index': 35, 'x': ' '}]
        }
        self.check(source, existing, expected_update, expected_new_existing)

    def test_all_full_block(self):
        existing = (
            {'index': 9, 'hash': 'cuBt'},
            {'index': 19, 'hash': 'qRd1'},
            {'index': 29, 'hash': 'k9VG'},
        )
        expected_update = {
            'partial_block': [{'hash': 'LaqB', 'index': 30, 'x': 'u'},
                              {'hash': '7is4', 'index': 31, 'x': 'e'},
                              {'hash': 'yG0W', 'index': 32, 'x': 'r'},
                              {'hash': 'qmpi', 'index': 33, 'x': 'e'},
                              {'hash': 'opj8', 'index': 34, 'x': '.'},
                              {'hash': 'oKCY', 'index': 35, 'x': ' '}]
        }
        self.check(source, existing, expected_update, expected_new_existing)

    def test_prev_partial_block(self):
        existing = (
            {'index': 9, 'hash': 'cuBt'},
            {'index': 19, 'hash': 'qRd1'},
            {'index': 25, 'hash': 'wARg'},
        )
        expected_update = {
            'full_blocks': [{'end_hash': 'k9VG', 'end_index': 29, 'start_index': 20}],
            'partial_block': [{'hash': 'LaqB', 'index': 30, 'x': 'u'},
                              {'hash': '7is4', 'index': 31, 'x': 'e'},
                              {'hash': 'yG0W', 'index': 32, 'x': 'r'},
                              {'hash': 'qmpi', 'index': 33, 'x': 'e'},
                              {'hash': 'opj8', 'index': 34, 'x': '.'},
                              {'hash': 'oKCY', 'index': 35, 'x': ' '}]
        }
        self.check(source, existing, expected_update, expected_new_existing)

    def test_partial_block(self):
        existing = (
            {'index': 9, 'hash': 'cuBt'},
            {'index': 19, 'hash': 'qRd1'},
            {'index': 29, 'hash': 'k9VG'},
            {'index': 32, 'hash': 'yG0W'},
        )
        expected_update = {
            'partial_block': [{'hash': 'qmpi', 'index': 33, 'x': 'e'},
                              {'hash': 'opj8', 'index': 34, 'x': '.'},
                              {'hash': 'oKCY', 'index': 35, 'x': ' '}]
        }
        self.check(source, existing, expected_update, expected_new_existing)

    def test_block_wrong_hash(self):
        existing = (
            {'index': 9, 'hash': 'cuBt'},
            {'index': 19, 'hash': 'WRONG'},
            {'index': 29, 'hash': 'k9VG'},
        )
        expected_update = {
            'full_blocks': [{'end_hash': 'qRd1', 'end_index': 19, 'start_index': 10},
                            {'end_hash': 'k9VG', 'end_index': 29, 'start_index': 20}],
            'partial_block': [{'hash': 'LaqB', 'index': 30, 'x': 'u'},
                              {'hash': '7is4', 'index': 31, 'x': 'e'},
                              {'hash': 'yG0W', 'index': 32, 'x': 'r'},
                              {'hash': 'qmpi', 'index': 33, 'x': 'e'},
                              {'hash': 'opj8', 'index': 34, 'x': '.'},
                              {'hash': 'oKCY', 'index': 35, 'x': ' '}]
        }
        self.check(source, existing, expected_update, expected_new_existing)

    def test_partial_wrong_hash(self):
        existing = (
            {'index': 9, 'hash': 'cuBt'},
            {'index': 19, 'hash': 'qRd1'},
            {'index': 29, 'hash': 'k9VG'},
            {'index': 32, 'hash': 'WRONG'},
        )
        expected_update = {
            'partial_block': [{'hash': 'LaqB', 'index': 30, 'x': 'u'},
                              {'hash': '7is4', 'index': 31, 'x': 'e'},
                              {'hash': 'yG0W', 'index': 32, 'x': 'r'},
                              {'hash': 'qmpi', 'index': 33, 'x': 'e'},
                              {'hash': 'opj8', 'index': 34, 'x': '.'},
                              {'hash': 'oKCY', 'index': 35, 'x': ' '}]
        }

        self.check(source, existing, expected_update, expected_new_existing)
