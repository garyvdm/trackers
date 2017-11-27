import argparse
import asyncio
import json
import logging
import os.path
import signal
import sys
from contextlib import closing, suppress
from functools import partial

import aionotify
import arsenic
import pkg_resources
import yaml
from aiocontext import async_contextmanager
from aiohttp import web

from trackers.tests import web_server_fixture


log = logging.getLogger(__name__)


@async_contextmanager
async def watch_path(loop, path):
    watcher = aionotify.Watcher()
    watcher.watch(path=path, flags=aionotify.Flags.MODIFY)

    # TODO: ideally need a recursive setup.
    watcher.watch(path=os.path.join(path, 'tests'), flags=aionotify.Flags.MODIFY)
    await watcher.setup(loop)
    try:
        yield watcher
    finally:
        watcher.close()


@async_contextmanager
async def on_signals_set_event(loop, signals):
    event = asyncio.Event()

    def handler(signame):
        log.info(f'{signame} received')
        event.set()

    for signame in signals:
        loop.add_signal_handler(getattr(signal, signame), partial(handler, signame))
    try:
        yield event
    finally:
        for signame in signals:
            loop.remove_signal_handler(getattr(signal, signame))


def main():
    parser = argparse.ArgumentParser(description='Run qunit tests. Watch for changes.')
    args = parser.parse_args()
    logging.basicConfig(stream=sys.stdout)
    with closing(asyncio.get_event_loop()) as loop:
        loop.run_until_complete(main_async(args, loop))


async def main_async(args, loop):
    app = web.Application()
    static_path = pkg_resources.resource_filename('trackers', '/static')
    app.router.add_static('/static', static_path)

    async def receive_log(request):
        result = json.loads(await request.text())
        outfile = sys.stderr if result['failed'] else sys.stdout
        yaml.dump(result, outfile)
        outfile.write('---\n')
        return web.Response(text='Thanks browser.')

    app.router.add_route('POST', '/results', handler=receive_log, name='receive_result')
    app.router.add_route('POST', '/log', handler=receive_log, name='receive_log')

    async def receive_error(request):
        body = await request.text()
        sys.stderr.write(body + '\n')
        return web.Response(text='Thanks browser.')

    app.router.add_route('POST', '/error', handler=receive_error, name='receive_error')

    async with web_server_fixture(loop, app) as url:
        service = arsenic.services.Geckodriver(log_file=arsenic.services.DEVNULL)
        browser = arsenic.browsers.Firefox()

        async with arsenic.get_session(service, browser) as driver:
            async with watch_path(loop, static_path) as watcher:

                async with on_signals_set_event(loop, ('SIGINT', 'SIGTERM')) as stop_event:

                    stop_event_wait = asyncio.ensure_future(stop_event.wait())

                    while not stop_event.is_set():
                        await driver.get(f'{url}/static/tests/test-lib.html#post_results')

                        get_watcher_event = asyncio.ensure_future(watcher.get_event())
                        await asyncio.wait((get_watcher_event, stop_event_wait), return_when=asyncio.FIRST_COMPLETED)
                        if not stop_event.is_set():
                            await get_watcher_event
                            await asyncio.sleep(1)
                            await driver.get('about:blank')
                        else:
                            get_watcher_event.cancel()
                            with suppress(asyncio.CancelledError):
                                await get_watcher_event
