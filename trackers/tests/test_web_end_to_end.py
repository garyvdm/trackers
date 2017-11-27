import asyncio
import socket
import sys
import unittest

import arsenic
import asynctest
import structlog
import testresources
import testscenarios
import yaml
from aiocontext import async_contextmanager
from aiohttp import web

from trackers.async_exit_stack import AsyncExitStack
from trackers.events import Event
from trackers.tests import temp_repo, TEST_GOOGLE_API_KEY
from trackers.web_app import make_aio_app


def load_tests(loader, tests, pattern):
    scenarios = testscenarios.generate_scenarios(tests)
    return testresources.OptimisingTestSuite(scenarios)


# To make arsenic quite
def dropper(logger, method_name, event_dict):
    raise structlog.DropEvent


structlog.configure(processors=[dropper])


class WebDriverService(testresources.TestResourceManager):

    def __init__(self, service):
        super().__init__()
        self.service = service

    def make(self, dependency_resources):
        loop = asyncio.get_event_loop()
        return loop.run_until_complete(self.service.start())

    def clean(self, driver):
        loop = asyncio.get_event_loop()
        loop.run_until_complete(driver.close())


def free_port():
    """
    Determines a free port using sockets.
    """
    free_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    free_socket.bind(('0.0.0.0', 0))
    free_socket.listen(5)
    port = free_socket.getsockname()[1]
    free_socket.close()
    return port


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
            sys.stderr.write(body + '\n')
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
    use_default_loop = True

    scenarios = [
        ('phantomjs', dict(
            driver_resource_manager=WebDriverService(arsenic.services.PhantomJS(log_file=arsenic.services.DEVNULL)),
            browser=arsenic.browsers.PhantomJS(),
        )),
        # ('firefox', dict(
        #     driver_resource_manager=WebDriverService(arsenic.services.Geckodriver(log_file=arsenic.services.DEVNULL)),
        #     browser=arsenic.browsers.Firefox(),
        # )),
        # ('chrome', dict(
        #     driver_resource_manager=WebDriverService(arsenic.services.Chromedriver(log_file=arsenic.services.DEVNULL)),
        #     browser=arsenic.browsers.Chrome(),
        # )),
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

            async with self.driver.session(self.browser) as session:
                await session.get(f'{url}/test_event')

        self.check_no_errors(client_errors, server_errors)
