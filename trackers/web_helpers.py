import asyncio
import base64
import hashlib
import logging
import os.path
from collections import namedtuple
from contextlib import closing, suppress
from copy import copy
from functools import partial

import aionotify
import magic
import pkg_resources
import sass
from aiohttp.web import HTTPFound, HTTPNotModified, Response
from slugify import slugify

from trackers.base import cancel_and_wait_task, Observable

immutable_cache_control = 'public,max-age=31536000,immutable'
mutable_cache_control = 'public'

logger = logging.getLogger(__name__)


def etag_response(request, response, etag, cache_control=None):
    if cache_control is None:
        cache_control = mutable_cache_control
    headers = {'ETag': etag, 'Cache-Control': cache_control}
    if request.headers.get('If-None-Match', '') == etag:
        return HTTPNotModified(headers=headers)
    else:
        if callable(response):
            response = response()
        response.headers.update(headers)
        return response


def etag_query_hash_response(request, response, etag):
    query_hash = request.query.get('hash')
    if query_hash and query_hash != etag:
        # Redirect to same url with correct hash query
        return HTTPFound(request.rel_url.with_query({'hash': etag}))
    else:
        cache_control = immutable_cache_control if query_hash else mutable_cache_control
        return etag_response(request, response, etag, cache_control=cache_control)


def coro_partial(func, *args, **keywords):
    """ Creates partial that pretends to be a coroutine if the func is a coroutine. """
    p = partial(func, *args, **keywords)
    if asyncio.iscoroutinefunction(func):
        p._is_coroutine = asyncio.coroutines._is_coroutine
    return p


class ProcessedStaticManager(object):

    Resource = namedtuple('Resource', ['resource_name', 'route_name', 'resources_key', 'urls_key', 'url_for_kwargs',
                                       'body_processor', 'body_loader', 'use_hased_url', 'cache_control', 'response_kwarg', ])
    ResourceDir = namedtuple('ResourceDir', ['resource_name', 'route_name', 'use_hased_url', 'cache_control', 'response_kwarg', ])
    ResourceProcessed = namedtuple('ResourceProcessed', ['use_hased_url', 'cache_control', 'url', 'hash', 'response_kwarg'])

    def __init__(self, app, package, resources_processed=()):
        self.app = app
        self.package = package
        self.magic = magic.Magic(flags=magic.MAGIC_MIME_TYPE)
        app.on_shutdown.append(self.on_app_shutdown)
        self.resources = []
        self.processed_resources = {}
        self.urls = {}
        self.resources_processed = Observable(logger, resources_processed)
        self.monitor_task = None
        self.watcher_availible = asyncio.Event()

    def add_resource(self, resource_name, route=None, route_name=None, body_processor=None, body_loader=None,
                     use_hased_url=True, cache_control=None, **response_kwarg):
        if route is None:
            route = resource_name
        urls_key = route_name if route_name is not None else resource_name
        if route_name is None:
            route_name = slugify(resource_name)
        self.resources.append(self.Resource(resource_name, route_name, route_name, urls_key, {}, body_processor,
                                            body_loader, use_hased_url, cache_control, response_kwarg))
        self.app.router.add_route('GET', route, coro_partial(self.resource_handler, route_name), name=route_name)

    def add_resource_dir(self, resource_name, route=None, route_name=None, use_hased_url=True,
                         cache_control=None, **response_kwarg):
        if route is None:
            route = f'{resource_name}/{{path}}'
        if route_name is None:
            route_name = slugify(resource_name)
        self.resources.append(self.ResourceDir(resource_name, route_name, use_hased_url, cache_control, response_kwarg))
        self.app.router.add_route('GET', route, coro_partial(self.resource_handler, route_name), name=route_name)

    def start_monitor_and_process_resources(self):
        self.monitor_task = asyncio.ensure_future(self.monitor_and_process_resources())

    async def monitor_and_process_resources(self):
        while True:
            with closing(aionotify.Watcher()) as watcher:
                self.current_watcher = watcher
                self.watcher_availible.set()
                try:
                    try:
                        await self.process_resources(watcher)
                    except Exception as e:
                        if logger.isEnabledFor(logging.DEBUG):
                            logger.exception('Error in process_resources: ')
                        else:
                            logger.error('Error in process_resources: %s', e)
                    await watcher.setup(asyncio.get_event_loop())
                    await watcher.get_event()
                except OSError as e:
                    logger.error(e)
                    break
                self.watcher_availible.clear()
                self.current_watcher = None
                logger.info('Reprocessing static resources.')
                await asyncio.sleep(1)

    async def process_resources(self, watcher=None):
        self.processed_resources = {}
        self.urls = {}

        expanded_resources = []
        for resource in self.resources:
            if isinstance(resource, self.ResourceDir):
                for name in pkg_resources.resource_listdir(self.package, resource.resource_name):
                    resource_name = f'{resource.resource_name}/{name}'
                    if not pkg_resources.resource_isdir(self.package, resource_name):
                        resources_key = (resource.route_name, name)
                        expanded_resources.append(self.Resource(
                            resource_name, resource.route_name, resources_key, resource_name, {'path': name}, None,
                            None, resource.use_hased_url, resource.cache_control, resource.response_kwarg))
            else:
                expanded_resources.append(resource)

        for resource in expanded_resources:
            response_kwarg = copy(resource.response_kwarg)
            body, hash = await self.get_static_processed_resource(
                resource.resource_name, resource.body_processor, resource.body_loader, watcher)

            if 'content_type' not in response_kwarg:
                response_kwarg['content_type'] = self.magic.id_buffer(body)
            response_kwarg['body'] = body

            if resource.use_hased_url:
                url = self.app.router[resource.route_name].url_for(**resource.url_for_kwargs).with_query({'hash': hash})
            else:
                url = self.app.router[resource.route_name].url_for(**resource.url_for_kwargs)

            self.processed_resources[resource.resources_key] = self.ResourceProcessed(
                resource.use_hased_url, resource.cache_control, url, hash, response_kwarg)
            self.urls[resource.urls_key] = url

        # if logger.isEnabledFor(logging.DEBUG):
        #     import pprint
        #     logger.debug('Urls: \n{}'.format(pprint.pformat(self.urls)))

        await self.resources_processed(self)

    async def resource_handler(self, resources_key, request):
        if 'path' in request.match_info:
            key = (resources_key, request.match_info['path'])
        else:
            key = resources_key
        resource = self.processed_resources[key]
        response_factory = lambda: Response(**resource.response_kwarg)  # NOQA
        if resource.use_hased_url:
            return etag_query_hash_response(request, response_factory, resource.hash)
        else:
            return etag_response(request, response_factory, resource.hash, resource.cache_control)

    async def get_static_processed_resource(self, resource_name, body_processor=None, body_loader=None, watcher=None):
        if body_loader:
            body = body_loader(self, resource_name, watcher)
        else:
            if watcher is None:
                await self.watcher_availible.wait()
                watcher = self.current_watcher
            file_name = pkg_resources.resource_filename(self.package, resource_name)
            with open(file_name, 'rb') as f:
                body = f.read()
            with suppress(ValueError):
                watcher.watch(file_name, flags=aionotify.Flags.MODIFY + aionotify.Flags.DELETE_SELF + aionotify.Flags.MOVE_SELF)

        hash = None
        if body_processor:
            body, hash = body_processor(self, body)
        if not hash:
            hash = base64.urlsafe_b64encode(hashlib.sha1(body).digest()).decode('ascii')
        return body, hash

    async def on_app_shutdown(self, app):
        if self.monitor_task:
            await cancel_and_wait_task(self.monitor_task)
        self.magic.close()


def sass_body_loader(static_manager, resource_name, watcher, **kwargs):

    def importer(path):
        file_name = os.path.abspath(pkg_resources.resource_filename(static_manager.package, resource_name))
        if watcher:
            with suppress(ValueError):
                watcher.watch(file_name, flags=aionotify.Flags.MODIFY + aionotify.Flags.DELETE_SELF + aionotify.Flags.MOVE_SELF)
        return [(file_name, )]

    file_name = importer(resource_name)[0][0]

    return sass.compile(filename=file_name, importers=((0, importer), ), **kwargs).encode()
