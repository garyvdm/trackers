import asyncio
import sys
import tempfile
import traceback
from contextlib import AsyncExitStack, asynccontextmanager
from datetime import datetime

import pkg_resources
import pytest
import yaml
from aiohttp import web
from dulwich.repo import MemoryRepo

from trackers.base import Tracker
from trackers.events import Event
from trackers.tests_client import TEST_GOOGLE_API_KEY, web_server_fixture
from trackers.web_app import convert_client_urls_to_paths, make_aio_app, on_new_event

# import logging


@asynccontextmanager
async def tracker_web_server_fixture():
    with tempfile.TemporaryDirectory() as cache_path:
        repo = MemoryRepo()
        settings = {
            "google_api_key": TEST_GOOGLE_API_KEY,
            "cache_path": cache_path,
            "oauth_providers": [],
            "app_url": "",
            "aiosession_encryption_key": "kOKJegsEmRHdOSKsIEW9IiQdB3B6ZaKCx_F6rGfdY2g=",
        }

        async def mock_app_setup(app, settings):
            app["trackers.settings"] = settings
            app["trackers.data_repo"] = repo
            app["trackers.events"] = {}
            app["analyse_processing_lock"] = asyncio.Lock()
            app["start_event_trackers"] = {
                "mock": None,
            }
            return AsyncExitStack()

        client_errors = []

        static_path = pkg_resources.resource_filename("trackers", "/static")

        async def client_error(request):
            body = await request.text()
            body = convert_client_urls_to_paths(static_path, body)
            sys.stderr.write(body + "\n")
            client_errors.append(body)
            return web.Response()

        server_errors = []

        def exception_recorder():
            exc_info = sys.exc_info()
            server_errors.append(exc_info)
            traceback.print_exception(*exc_info)

        app = await make_aio_app(
            settings,
            app_setup=mock_app_setup,
            client_error_handler=client_error,
            exception_recorder=exception_recorder,
        )
        yield app, client_errors, server_errors


def wait_condition(condition, *args, delay=0.1, timeout=2, **kwargs):
    async def wait_condition_inner():
        while True:
            result = await condition(*args, **kwargs)
            if result:
                return
            await asyncio.sleep(delay)

    return asyncio.wait_for(wait_condition_inner(), timeout)


async def ws_ready_is(session, expected_state):
    ready = await session.execute_script("return ws && ws.readyState == 1;")
    return bool(ready) == expected_state


def d(date_string):
    return datetime.strptime(date_string, "%Y/%m/%d %H:%M:%S")


def check_no_errors(client_errors, server_errors):
    if client_errors and server_errors:
        pytest.fail("There were server and client errors.")
    if client_errors:
        pytest.fail("There were client errors.")
    if server_errors:
        pytest.fail("There were server errors.")


# async def test_live_reconnect(browser):
#     port = free_port()

#     async with AsyncExitStack() as stack:
#         app, client_errors, server_errors = await stack.enter_async_context(
#             tracker_web_server_fixture()
#         )
#         app["trackers.events"]["test_event"] = event = Event(
#             app,
#             "test_event",
#             yaml.safe_load(
#                 """
#                 title: Test Event
#                 live: True
#                 riders:
#                     - name: Foo Bar
#                       tracker: null
#                 markers: []
#                 """
#             ),
#             [],
#         )
#         url = await stack.enter_async_context(web_server_fixture(app, port))
#         await on_new_event(event)
#         await browser.get(f"{url}/test_event")
#         await wait_condition(ws_ready_is, browser, True)

#     await wait_condition(ws_ready_is, browser, False)

#     # Bring the server back up, reconnect
#     async with AsyncExitStack() as stack:
#         app, client_errors, server_errors = await stack.enter_async_context(
#             tracker_web_server_fixture()
#         )
#         app["trackers.events"]["test_event"] = event = Event(
#             app,
#             "test_event",
#             yaml.safe_load(
#                 """
#                 title: Test Event
#                 live: True
#                 riders:
#                     - name: Foo Bar
#                         tracker: null
#                 markers: []
#             """
#             ),
#             [],
#         )
#         url = await stack.enter_async_context(web_server_fixture(app, port))
#         await on_new_event(event)
#         await wait_condition(ws_ready_is, browser, True, timeout=10)

#     check_no_errors(client_errors, server_errors)


async def test_tracker_points_show_and_change(browser):
    step_sleep_time = 0.5

    async with AsyncExitStack() as stack:
        app, client_errors, server_errors = await stack.enter_async_context(
            tracker_web_server_fixture()
        )

        mock_tracker = Tracker("mock_tracker")

        async def start_mock_event_tracker(app, event, rider_name, tracker_data, start, end):
            return mock_tracker

        app["start_event_trackers"] = {
            "mock": start_mock_event_tracker,
        }
        url = await stack.enter_async_context(web_server_fixture(app))

        app["trackers.events"]["test_event"] = event = Event(
            app,
            "test_event",
            yaml.safe_load(
                """
                title: Test Event
                event_start: 2017-01-01 05:00:00
                live: True
                riders:
                  - name: Foo Bar
                    name_short: Foo
                    tracker: {type: mock}
                markers: []
                batch_update_interval: 0.1
                predicted_update_interval: 5
                bounds: {'north': -26.300822, 'south': -27.28287, 'east': 28.051139, 'west': 27.969365}
                """
            ),
            [],
        )
        await on_new_event(event)
        # await event.start_trackers()
        await browser.get(f"{url}/test_event")
        await wait_condition(ws_ready_is, browser, True)
        await asyncio.sleep(step_sleep_time)

        await mock_tracker.new_points(
            [
                {
                    "time": d("2017/01/01 05:00:00"),
                    "position": (-26.300822, 28.049444, 1800),
                },
                {
                    "time": d("2017/01/01 05:01:00"),
                    "position": (-26.351581, 28.100281, 1800),
                },
            ]
        )
        await asyncio.sleep(step_sleep_time)
        # await browser.execute_script('console.log(riders_client_items["Foo Bar"].marker);')
        # await asyncio.sleep(100)

        assert await browser.execute_script(
            'return riders_client_items["Foo Bar"].marker !== null;'
        )
        assert (
            await browser.execute_script(
                'return riders_client_items["Foo Bar"].paths.riders_off_route.length;'
            )
            == 1
        )
        assert (
            await browser.execute_script(
                'return riders_client_items["Foo Bar"].paths.riders_off_route[0].getPath().length;'
            )
            == 2
        )

        await mock_tracker.reset_points()
        await asyncio.sleep(step_sleep_time)
        # await asyncio.sleep(100)
        assert await browser.execute_script(
            'return riders_client_items["Foo Bar"].marker === null;'
        )
        assert (
            await browser.execute_script(
                'return riders_client_items["Foo Bar"].paths.riders_off_route.length;'
            )
            == 0
        )

        await mock_tracker.new_points(
            [
                {
                    "time": d("2017/01/01 05:30:00"),
                    "position": (-26.351581, 28.100281, 1800),
                },
                {
                    "time": d("2017/01/01 05:31:00"),
                    "position": (-27.282870, 27.970620, 1800),
                },
            ]
        )
        await asyncio.sleep(step_sleep_time)
        # await asyncio.sleep(100)
        assert await browser.execute_script(
            'return riders_client_items["Foo Bar"].marker !== null;'
        )
        assert (
            await browser.execute_script(
                'return riders_client_items["Foo Bar"].paths.riders_off_route.length;'
            )
            == 1
        )
        assert (
            await browser.execute_script(
                'return riders_client_items["Foo Bar"].paths.riders_off_route[0].getPath().length;'
            )
            == 2
        )

    check_no_errors(client_errors, server_errors)


# TODO:
# * http blocked list download
# * Config reload
# * graphs
# * event with route
