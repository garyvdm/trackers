import socket
from contextlib import asynccontextmanager

from aiohttp import web

TEST_GOOGLE_API_KEY = "AIzaSyD8qJMJRAfOvyG0J_LT2WNzBnem8s3vqPw"


def free_port():
    """
    Determines a free port using sockets.
    """
    free_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    free_socket.bind(("0.0.0.0", 0))
    free_socket.listen(5)
    port = free_socket.getsockname()[1]
    free_socket.close()
    return port


@asynccontextmanager
async def web_server_fixture(app, port=None):
    if not port:
        port = free_port()
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "localhost", port)
    await site.start()
    try:
        yield f"http://localhost:{port}"
    finally:
        await runner.cleanup()
