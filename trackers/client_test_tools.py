import argparse
import asyncio
import json
import logging
import os.path
import shutil
import signal
import subprocess
import sys
import tempfile
from contextlib import AsyncExitStack, asynccontextmanager, closing, suppress
from functools import partial

import arsenic
import pkg_resources
import yaml
from aiohttp import web
from asyncinotify import Inotify, Mask

from trackers.tests import web_server_fixture
from trackers.web_app import convert_client_urls_to_paths

log = logging.getLogger(__name__)


@asynccontextmanager
async def watch_path(path):
    with Inotify() as inotify:
        inotify.add_watch(path, Mask.MODIFY)
        # TODO: ideally need a recursive setup.
        inotify.add_watch(os.path.join(path, "tests"), Mask.MODIFY)
        yield inotify


@asynccontextmanager
async def on_signals_set_event(loop, signals):
    event = asyncio.Event()

    def handler(signame):
        log.info(f"{signame} received")
        event.set()

    for signame in signals:
        loop.add_signal_handler(getattr(signal, signame), partial(handler, signame))
    try:
        yield event
    finally:
        for signame in signals:
            loop.remove_signal_handler(getattr(signal, signame))


def qunit_runner():
    parser = argparse.ArgumentParser(description="Run qunit tests. Watch for changes.")
    parser.add_argument("-c", "--coverage", action="store_true")
    args = parser.parse_args()
    logging.basicConfig(stream=sys.stdout)
    with closing(asyncio.get_event_loop()) as loop:
        loop.run_until_complete(qunit_runner_async(args, loop))


async def qunit_runner_async(args, loop):
    async with AsyncExitStack() as stack:
        org_static_path = pkg_resources.resource_filename("trackers", "/static")
        if args.coverage:
            static_path = os.path.join(
                await stack.enter_async_context(tempfile.TemporaryDirectory()), "static"
            )
            os.mkdir(static_path)
        else:
            static_path = org_static_path

        app = web.Application()

        app.router.add_static("/static", static_path)

        async def receive_log(request):
            text = await request.text()
            try:
                result = json.loads(text)
            except json.JSONDecodeError:
                outfile = sys.stderr
                outfile.write(text)
            else:
                if isinstance(result, dict) and "source" in result:
                    result["source"] = literal_str(
                        convert_client_urls_to_paths(static_path, result["source"])
                        .strip()
                        .strip("@")
                        .strip("\n")
                    )
                outfile = (
                    sys.stderr if isinstance(result, dict) and result.get("failed") else sys.stdout
                )
                yaml.dump(result, outfile, default_flow_style=False, Dumper=DumperWithLiteral)
            outfile.write("---\n")
            return web.Response(text="Thanks browser.")

        app.router.add_route("POST", "/results", handler=receive_log, name="receive_result")
        app.router.add_route("POST", "/log", handler=receive_log, name="receive_log")

        app.router.add_route("POST", "/coverage", handler=receive_coverage, name="receive_coverage")

        async def receive_error(request):
            body = await request.text()
            sys.stderr.write(body + "\n")
            return web.Response(text="Thanks browser.")

        app.router.add_route("POST", "/error", handler=receive_error, name="receive_error")

        url = await stack.enter_async_context(web_server_fixture(loop, app))

        service = arsenic.services.Geckodriver(log_file=os.devnull)
        browser = arsenic.browsers.Firefox()

        driver = await stack.enter_async_context(arsenic.get_session(service, browser))
        if args.coverage:
            app["coverage_driver"] = await stack.enter_async_context(
                arsenic.get_session(service, browser)
            )

        inotify = await stack.enter_async_context(watch_path(org_static_path))
        inotify_iter = aiter(inotify)

        stop_event = await stack.enter_async_context(
            on_signals_set_event(loop, ("SIGINT", "SIGTERM"))
        )

        stop_event_wait = asyncio.ensure_future(stop_event.wait())

        while not stop_event.is_set():
            if args.coverage:
                make_instrumented_static(org_static_path, static_path, ("lib.js",))

            await driver.get(f"{url}/static/tests/test-lib.html#post_results")

            get_watcher_event = asyncio.ensure_future(aiter(inotify_iter))
            await asyncio.wait(
                (get_watcher_event, stop_event_wait),
                return_when=asyncio.FIRST_COMPLETED,
            )
            if not stop_event.is_set():
                await get_watcher_event
                await asyncio.sleep(1)
                await driver.get("about:blank")
            else:
                get_watcher_event.cancel()
                with suppress(asyncio.CancelledError):
                    await get_watcher_event


class literal_str(str):
    pass


def literal_str_representer(dumper, data):
    return dumper.represent_scalar("tag:yaml.org,2002:str", data, style="|")


class DumperWithLiteral(yaml.Dumper):
    pass


DumperWithLiteral.add_representer(literal_str, literal_str_representer)


def make_instrumented_static(src_path, dest_path, instrumented_files):
    shutil.rmtree(dest_path)
    shutil.copytree(src_path, dest_path)

    for item in instrumented_files:
        subprocess.check_call(
            [
                os.path.abspath("node_modules/nyc/bin/nyc.js"),
                "instrument",
                item,
                dest_path,
                "--produce-source-map",
                "true",
            ],
            cwd=src_path,
        )


async def receive_coverage(request):
    coverage = await request.text()
    src_path = pkg_resources.resource_filename("trackers", "/static")
    nyc = os.path.abspath("node_modules/nyc/bin/nyc.js")

    with tempfile.TemporaryDirectory() as tempdir:
        with open(os.path.join(tempdir, "out.json"), "w") as f:
            f.write(coverage)
        subprocess.check_call([nyc, "report", "--temp-directory", tempdir, src_path], cwd=src_path)
        subprocess.check_call(
            [nyc, "report", "--temp-directory", tempdir, "--reporter", "html"],
            cwd=src_path,
        )

    await request.app["coverage_driver"].get(
        "file://{}".format(os.path.join(src_path, "coverage/index.html"))
    )

    return web.Response(text="Thanks browser.")
