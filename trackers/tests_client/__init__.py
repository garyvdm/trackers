import asyncio
import os.path
import socket
import unittest
from contextlib import asynccontextmanager

import arsenic
import structlog
import testresources
import testscenarios
from aiohttp import web


def suite():
    import trackers
    tests = unittest.defaultTestLoader.discover(os.path.split(__file__)[0], top_level_dir=trackers.__path__[0])
    tests_with_scenarios = testscenarios.generate_scenarios(tests)
    return testresources.OptimisingTestSuite(tests_with_scenarios)


TEST_GOOGLE_API_KEY = 'AIzaSyD8qJMJRAfOvyG0J_LT2WNzBnem8s3vqPw'


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


@asynccontextmanager
async def web_server_fixture(loop, app, port=None):
    if not port:
        port = free_port()
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, 'localhost', port)
    await site.start()
    try:
        yield f'http://localhost:{port}'
    finally:
        await runner.cleanup()


# To make arsenic quite
def dropper(logger, method_name, event_dict):
    raise structlog.DropEvent


structlog.configure(processors=[dropper])


class WebDriverSession(testresources.TestResourceManager):

    def __init__(self, service, browser):
        super().__init__()
        self.service = service
        self.browser = browser

    def make(self, dependency_resources):
        loop = asyncio.get_event_loop()
        return loop.run_until_complete(self.amake(dependency_resources))

    async def amake(self, dependency_resources):
        service = await self.service.start()
        session = await service.new_session(self.browser)
        return session

    def clean(self, session):
        loop = asyncio.get_event_loop()
        loop.run_until_complete(self.aclean(session))

    async def aclean(self, session):
        await session.close()
        await session.driver.close()


browser_scenarios = [
    # ('phantomjs', dict(
    #     browser_session_resource_manager=WebDriverSession(
    #         arsenic.services.PhantomJS(log_file=os.devnull),
    #         arsenic.browsers.PhantomJS(),
    #     ),
    # )),
    # ('firefox', dict(
    #     browser_session_resource_manager=WebDriverSession(
    #         arsenic.services.Geckodriver(log_file=os.devnull),
    #         arsenic.browsers.Firefox(),
    #     ),
    # )),
    ('chrome', dict(
        browser_session_resource_manager=WebDriverSession(
            arsenic.services.Chromedriver(log_file=os.devnull),
            arsenic.browsers.Chrome(),
        ),
    )),
    # ('chrome-headless', dict(
    #     browser_session_resource_manager=WebDriverSession(
    #         arsenic.services.Chromedriver(log_file=os.devnull),
    #         arsenic.browsers.Chrome(chromeOptions={
    #             'args': ['--headless', '--disable-gpu']
    #         }),
    #     ),
    # )),
]
