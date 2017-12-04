import asyncio
import sys
import traceback

import arsenic
import asynctest
import pkg_resources
import testresources
import testscenarios
import yaml
from aiocontext import async_contextmanager
from aiohttp import web

from trackers.async_exit_stack import AsyncExitStack
from trackers.events import Event
from trackers.tests import free_port, temp_repo, TEST_GOOGLE_API_KEY, web_server_fixture
from trackers.web_app import convert_client_urls_to_paths, make_aio_app


def load_tests(loader, tests, pattern):
    scenarios = testscenarios.generate_scenarios(tests)
    return testresources.OptimisingTestSuite(scenarios)


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


@async_contextmanager
async def tracker_web_server_fixture(loop, port=None):

    with temp_repo() as repo:
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

        static_path = pkg_resources.resource_filename('trackers', '/static')

        async def client_error(request):
            body = await request.text()
            body = convert_client_urls_to_paths(static_path, body)
            sys.stderr.write(body + '\n')
            client_errors.append(body)
            return web.Response()

        server_errors = []

        def exception_recorder():
            exc_info = sys.exc_info()
            server_errors.append(exc_info)
            traceback.print_exception(*exc_info)

        app = await make_aio_app(settings, app_setup=mock_app_setup, client_error_handler=client_error,
                                 exception_recorder=exception_recorder)
        async with web_server_fixture(loop, app, port) as url:
            yield app, url, client_errors, server_errors


def wait_condition(condition, *args, delay=0.1, timeout=1, **kwargs):
    async def wait_condition_inner():
        while True:
            result = await condition(*args, **kwargs)
            if result:
                return
            await asyncio.sleep(delay)

    return asyncio.wait_for(wait_condition_inner(), timeout)


async def ws_ready_is(session, expected_state):
    ready = await session.execute_script('return ws && ws.readyState == 1;')
    return bool(ready) == expected_state


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

    async def test_live_reconnect(self):

        port = free_port()

        async with self.driver.session(self.browser) as session:
            async with tracker_web_server_fixture(self.loop, port=port) as (app, url, client_errors, server_errors):
                app['trackers.events']['test_event'] = Event(
                    app, 'test_event',
                    yaml.load("""
                        title: Test Event
                        live: True
                        riders:
                            - name: Foo Bar
                              tracker: null
                        markers: []
                    """),
                    []
                )
                await session.get(f'{url}/test_event')
                await wait_condition(ws_ready_is, session, True)

            await wait_condition(ws_ready_is, session, False)

            # Bring the server back up, reconnect
            async with tracker_web_server_fixture(self.loop, port=port) as (app, url, client_errors, server_errors):
                app['trackers.events']['test_event'] = Event(
                    app, 'test_event',
                    yaml.load("""
                        title: Test Event
                        live: True
                        riders:
                            - name: Foo Bar
                              tracker: null
                        markers: []
                    """),
                    []
                )
                await wait_condition(ws_ready_is, session, True, timeout=10)

        self.check_no_errors(client_errors, server_errors)
