import asyncio
import json
import sys

import arsenic
import asynctest
import pkg_resources
import yaml
from aiohttp import web

from trackers.tests import web_server_fixture


class TestQunit(asynctest.TestCase):

    async def test_lib(self):

        app = web.Application()
        app.router.add_static('/static', pkg_resources.resource_filename('trackers', '/static'))

        result_received_fut = asyncio.Future()

        async def receive_result(request):
            result = json.loads(await request.text())
            result_received_fut.set_result(result)
            return web.Response(text='Thanks browser.')

        app.router.add_route('POST', '/results', handler=receive_result, name='receive_result')

        async def receive_log(request):
            result = json.loads(await request.text())
            outfile = sys.stderr if result['failed'] else sys.stdout
            yaml.dump(result, outfile)
            outfile.write('---\n')
            return web.Response(text='Thanks browser.')

        app.router.add_route('POST', '/log', handler=receive_log, name='receive_log')

        async def receive_error(request):
            body = await request.text()
            sys.stderr.write(body + '\n')
            return web.Response(text='Thanks browser.')

        app.router.add_route('POST', '/error', handler=receive_error, name='receive_error')

        async with web_server_fixture(self.loop, app) as url:

            service = arsenic.services.PhantomJS(log_file=arsenic.services.DEVNULL)
            browser = arsenic.browsers.PhantomJS()
            # service = arsenic.services.Geckodriver(log_file=arsenic.services.DEVNULL)
            # browser = arsenic.browsers.Firefox()

            async with arsenic.get_session(service, browser) as driver:

                await driver.get(f'{url}/static/tests/test-lib.html#post_results')

                # result_text = await asyncio.wait_for(result_received_fut, 10)
                result = await result_received_fut
        print(yaml.dump(result))
        if result['failed']:
            self.fail()
