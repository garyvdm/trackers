import os.path
import unittest

import trackers


def suite():
    tests = unittest.defaultTestLoader.discover(os.path.split(__file__)[0])
    return unittest.TestSuite(tests)


def full_suite():
    tests = unittest.defaultTestLoader.discover(trackers.__path__[0])
    return unittest.TestSuite(tests)


def get_test_app_and_settings(repo):
    settings = {}
    app = {}
    app['trackers.settings'] = settings
    app['trackers.data_repo'] = repo
    app['trackers.events'] = {}
    return app, settings
