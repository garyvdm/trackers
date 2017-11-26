import sys
import unittest

import asynctest
import testresources
import testscenarios
import yaml
from aiocontext import async_contextmanager
from aiohttp import web
from selenium import webdriver
from selenium.webdriver.common.utils import free_port

from trackers.async_exit_stack import AsyncExitStack
from trackers.events import Event
from trackers.tests import temp_repo, TEST_GOOGLE_API_KEY
from trackers.web_app import make_aio_app


def load_tests(loader, tests, pattern):
    scenarios = testscenarios.generate_scenarios(tests)
    return testresources.OptimisingTestSuite(scenarios)


class WebDriverResource(testresources.TestResourceManager):

    def __init__(self, driver_cls, *args, **kwargs):
        super().__init__()
        self.driver_cls = driver_cls
        self.args = args
        self.kwargs = kwargs

    def make(self, dependency_resources):
        return self.driver_cls(*self.args, **self.kwargs)

    def clean(self, driver):
        driver.quit()

    def _reset(self, driver, dependency_resources):
        driver.get('about:blank')
        driver.delete_all_cookies()
        return driver

    def isDirty(self):
        return True


@async_contextmanager
async def web_server_fixture(loop):

    with temp_repo() as repo:
        # This kind of should be a fixtures.Fixture, but it needs to be async, so it's an async_contextmanager instead.
        settings = {
            'data_path': repo.path,
            'google_api_key': TEST_GOOGLE_API_KEY,
        }

        async def mock_app_setup(app, settings):
            app['trackers.settings'] = settings
            app['trackers.data_repo'] = repo
            app['start_event_trackers'] = {
                'mock': None,
            }
            return AsyncExitStack()

        client_errors = []

        async def client_error(request):
            body = await request.text()
            sys.stderr.write(body)
            client_errors.append(body)
            return web.Response()

        server_errors = []

        def exception_recorder():
            exc_info = sys.exc_info()
            server_errors.append(exc_info)

        app = await make_aio_app(settings, app_setup=mock_app_setup, client_error_handler=client_error,
                                 exception_recorder=exception_recorder)
        handler = app.make_handler(debug=True)
        port = free_port()
        srv = await loop.create_server(handler, '127.0.0.1', port)
        try:
            yield app, f'http://127.0.0.1:{port}', client_errors, server_errors
        finally:
            srv.close()
            await srv.wait_closed()
            await app.shutdown()
            await handler.shutdown(10)
            await app.cleanup()


class TestWebEndToEnd(testresources.ResourcedTestCase, asynctest.TestCase):

    scenarios = [
        ('phantomjs', dict(driver_resource_manager=WebDriverResource(webdriver.PhantomJS, service_log_path='/dev/null'))),
        # ('firefox', dict(driver_resource_manager=WebDriverResource(webdriver.Firefox, log_path='/dev/null'))),
        # ('chrome', dict(driver_resource_manager=WebDriverResource(webdriver.Chrome))),
    ]

    @property
    def resources(self):
        return [("driver", self.driver_resource_manager)]

    def check_no_errors(self, client_errors, server_errors):
        if client_errors and server_errors:
            self.fail('There were server and client errors.')
        if client_errors:
            self.fail('There were client errors.')
        if server_errors:
            self.fail('There were server errors.')

    @unittest.expectedFailure
    async def test(self):

        async with web_server_fixture(self.loop) as (app, url, client_errors, server_errors):
            app['trackers.events']['test_event'] = Event(
                app, 'test_event',
                yaml.load("""
                    title: Test Event
                    live: True
                    riders:
                        - name: Foo Bar
                          tracker: null
                """),
                []
            )

            driver = self.driver
            await self.loop.run_in_executor(None, driver.get, f'{url}/test_event')

        self.check_no_errors(client_errors, server_errors)
