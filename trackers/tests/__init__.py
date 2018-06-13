import socket
import unittest

import arsenic.services
import structlog
from aiocontext import async_contextmanager

import trackers


def suite():
    tests = unittest.defaultTestLoader.discover(trackers.__path__[0])
    return unittest.TestSuite(tests)


def get_test_app_and_settings(repo):
    settings = {}
    app = {}
    app['trackers.settings'] = settings
    app['trackers.data_repo'] = repo
    app['trackers.events'] = {}
    return app, settings


TEST_GOOGLE_API_KEY = 'AIzaSyCDXMpphQfDX44Zqmfzx9qpKJ0bs5NnQ_w'


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
async def web_server_fixture(loop, app, port=None):
    handler = app.make_handler(debug=True)
    if not port:
        port = free_port()
    srv = await loop.create_server(handler, '127.0.0.1', port)
    try:
        yield f'http://127.0.0.1:{port}'
    finally:
        srv.close()
        await srv.wait_closed()
        await app.shutdown()
        await handler.shutdown(10)
        await app.cleanup()


# To make arsenic quite
def dropper(logger, method_name, event_dict):
    raise structlog.DropEvent


structlog.configure(processors=[dropper])


# Monkey patch arsenic to fix DeprecationWarning. Remove when https://github.com/HDE/arsenic/pull/23 is done
def sync_factory(func):
    return func


arsenic.services.sync_factory = sync_factory
