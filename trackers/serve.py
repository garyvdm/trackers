import asyncio
import logging.config
import os
import shutil
import signal
import socket
import sys

import uvloop

import trackers.bin_utils
import trackers.web_app

defaults_yaml = """
    server_type: inet
    inet_host: ''
    inet_port: 6841
    debugtoolbar: False
    aioserver_debug: False
"""


def main():
    parser = trackers.bin_utils.get_base_argparser()
    parser.add_argument('--inet', action='store',
                        help='Host address and port to listen on. (format: host:port)')
    parser.add_argument('--unix', action='store',
                        help='Route of unix socket to listen on. ')
    parser.add_argument('--dev', action='store_true',
                        help='Enable development tools (e.g. debug toolbar.)')
    args = parser.parse_args()

    settings = trackers.bin_utils.get_combined_settings(defaults_yaml, args)

    try:

        if args.inet:
            host, _, port_str = args.inet.split(':')
            port = int(port_str)
            settings['server_type'] = 'inet'
            settings['inet_host'] = host
            settings['inet_port'] = port
        if args.unix:
            settings['server_type'] = 'unix'
            settings['unix_path'] = args.unix
        if args.dev:
            settings['debugtoolbar'] = True
            settings['aioserver_debug'] = True
        if args.google_api_key:
            settings['google_api_key'] = args.google_api_key

        asyncio.set_event_loop_policy(uvloop.EventLoopPolicy())
        loop = asyncio.get_event_loop()
        try:
            loop.run_until_complete(serve(loop, settings))
        finally:
            loop.close()
    except Exception:
        logging.exception('Unhandled exception:')
        sys.exit(3)


async def serve(loop, settings):

    app = await trackers.web_app.make_aio_app(settings)

    if settings['debugtoolbar']:
        try:
            import aiohttp_debugtoolbar
        except ImportError:
            logging.error('aiohttp_debugtoolbar is enabled, but not installed.')
        else:
            aiohttp_debugtoolbar.setup(app, **settings.get('debugtoolbar_settings', {}))

    handler = app.make_handler(debug=settings.get('aioserver_debug', False),
                               access_log_format='%l %u %t "%r" %s %b "%{Referrer}i" "%{User-Agent}i"')

    if settings['server_type'] == 'inet':
        srv = await loop.create_server(handler, settings['inet_host'], settings['inet_port'])
    elif settings['server_type'] == 'unix':
        unix_path = settings['unix_path']
        if os.path.exists(unix_path):
            try:
                os.unlink(unix_path)
            except OSError:
                logging.exception("Could not unlink socket '{}'".format(unix_path))
        srv = await loop.create_unix_server(handler, unix_path)
        if 'unix_chown' in settings:
            os.chmod(unix_path, settings['unix_chmod'])
        if 'unix_chown' in settings:
            shutil.chown(unix_path, **settings['unix_chown'])

    for sock in srv.sockets:
        if sock.family in (socket.AF_INET, socket.AF_INET6):
            print('Serving on http://{}:{}'.format(*sock.getsockname()))
            app.setdefault('host_urls', []).append('http://{}:{}'.format(*sock.getsockname()))
        else:
            print('Serving on {!r}'.format(sock))

    try:
        # Run forever (or we get interupt)
        run_fut = asyncio.Future()
        for signame in ('SIGINT', 'SIGTERM'):
            loop.add_signal_handler(getattr(signal, signame), run_fut.set_result, None)
        try:
            await run_fut
        finally:
            for signame in ('SIGINT', 'SIGTERM'):
                loop.remove_signal_handler(getattr(signal, signame))
    finally:
        srv.close()
        await srv.wait_closed()
        await app.shutdown()
        await handler.shutdown(10)
        await app.cleanup()
