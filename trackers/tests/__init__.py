import asyncio
import os.path
import unittest

import trackers


def suite():
    tests = unittest.defaultTestLoader.discover(
        os.path.split(__file__)[0], top_level_dir=trackers.__path__[0]
    )
    return unittest.TestSuite(tests)


def full_suite():
    import trackers.tests_client

    full_suite = unittest.TestSuite()
    full_suite.addTest(suite())
    full_suite.addTest(trackers.tests_client.suite())
    return full_suite


def get_test_app_and_settings(repo):
    settings = {}
    app = {}
    app["trackers.settings"] = settings
    app["trackers.data_repo"] = repo
    app["trackers.events"] = {}
    app["analyse_processing_lock"] = asyncio.Lock()
    return app, settings
