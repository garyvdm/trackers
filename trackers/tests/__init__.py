import tempfile
import unittest
from contextlib import contextmanager

import fixtures
from dulwich.repo import Repo

import trackers


def suite():
    tests = unittest.defaultTestLoader.discover(trackers.__path__[0])
    return unittest.TestSuite(tests)


class TempRepoFixture(fixtures.TempDir):
    def _setUp(self):
        super()._setUp()
        self.repo = Repo.init_bare(self.path)
        self.addCleanup(self.repo.close)


@contextmanager
def temp_repo():
    with tempfile.TemporaryDirectory() as path:
        yield Repo.init_bare(path)


def get_test_app_and_settings(repo):
    settings = {}
    app = {}
    app['trackers.settings'] = settings
    app['trackers.data_repo'] = repo
    return app, settings


TEST_GOOGLE_API_KEY = 'AIzaSyCDXMpphQfDX44Zqmfzx9qpKJ0bs5NnQ_w'
