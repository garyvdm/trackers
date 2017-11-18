import unittest

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
