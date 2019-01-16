import datetime
import functools
import io
import logging

import aioauth_client
import aiohttp_session
import aiohttp_session.cookie_storage
from aiohttp import web
from cryptography import fernet
from htmlwrite import Markup, Tag, Writer


logger = logging.getLogger(__name__)


def gen_key():
    print(fernet.Fernet.generate_key().decode('ascii'))


async def config_aio_app(app, settings):

    app['oauth_providers'] = settings['oauth_providers']
    app['oauth_base_url'] = settings['app_url']
    app['oauth_providers_by_name'] = {provider['name']: provider for provider in settings['oauth_providers']}
    app['authorization'] = settings.get('authorization', True)

    max_age = int(datetime.timedelta(days=30).total_seconds())
    storage = aiohttp_session.cookie_storage.EncryptedCookieStorage(settings['aiosession_encryption_key'], max_age=max_age)
    aiohttp_session.setup(app, storage)

    app.router.add_route('GET', '/oauth/{provider}', oauth, name='oauth_login')
    app.router.add_route('GET', '/logout', logout, name='logout')
    app.router.add_route('GET', '/get_identity', get_identity, name='get_identity')


async def oauth(request):
    providers_by_name = request.app['oauth_providers_by_name']
    provider = request.match_info.get('provider')
    if provider not in providers_by_name:
        raise web.HTTPNotFound(reason='Unknown provider')

    # Create OAuth1/2 client
    Client = aioauth_client.ClientRegistry.clients[provider]
    params = providers_by_name[provider]['init']
    client = Client(**params)
    client.params['redirect_uri'] = '{}{}'.format(request.app['oauth_base_url'], request.path)

    if client.shared_key not in request.query:
        # Redirect client to provider
        if request.query.get('return_to'):
            client.params['state'] = request.query['return_to']
        return web.HTTPFound(client.get_authorize_url())

    await client.get_access_token(request.query[client.shared_key])
    user, user_data = await client.user_info()

    session = await aiohttp_session.get_session(request)

    session['identity'] = dict(client.user_parse(user_data))

    return_to = request.query.get('state')
    if not return_to:
        return_to = '/'
    return web.HTTPFound(return_to)


async def logout(request):
    session = await aiohttp_session.get_session(request)
    session['identity'] = None
    return web.HTTPFound(request.query.get('return_to', '/'))


async def get_identity(request):
    session = await aiohttp_session.get_session(request)
    return session.get('identity')


async def get_git_author(request):
    identity = await get_identity(request)
    if identity:
        return f"{identity['first_name']} <{identity['email']}>"


sentinel = object()


async def show_identity(request, writer, identity=sentinel):
    if identity == sentinel:
        identity = await get_identity(request)
    c = writer.context
    w = writer.write

    if identity:
        with c(Tag('div')):
            if identity.get('picture'):
                w(Tag('img', src=identity['picture'], s_height="4em", s_margin_right="8px"))
            with c(Tag('div', s_display='inline-block')):
                w('You are logged in as:')
                w(Tag('br'))
                w(f"{identity['first_name']} <{identity['email']}>")
                w(Tag('br'))

        w(Tag('a', class_="waves-effect waves-light btn",
              href=request.app.router['logout'].url_for().with_query(return_to=request.path)),
          'Logout')
    else:
        with c(Tag('div')):
            w('Not logged in. Login with: ')

            for provider in request.app['oauth_providers']:
                w(Tag('a', class_="waves-effect waves-light btn",
                      href=request.app.router['oauth_login'].url_for(provider=provider['name']).with_query(return_to=request.path)),
                  provider['display_name'])


async def ensure_authorized_handler(request, allowed_principals, handler):
    if not request.app['authorization']:
        return await handler(request)
    else:
        identity = await get_identity(request)
        if identity and identity['email'] in allowed_principals:
            return await handler(request)
        else:
            body = io.StringIO()
            writer = Writer(body)
            w = writer.w
            c = writer.c
            error = 'Not Authorised.' if identity else 'Not Authenticated'
            w(Markup('<!DOCTYPE html>'))
            with c(Tag('html')):
                with c(Tag('head')):
                    w(Tag('title'), error)
                    w(Tag('meta', name="viewport", content="initial-scale=1.0, user-scalable=no"))
                    w(Tag('link', rel="stylesheet",
                          href="https://cdnjs.cloudflare.com/ajax/libs/materialize/1.0.0/css/materialize.min.css"))
                with c(Tag('body', s_padding="24px", s_width="100%", )):
                    with c(Tag('div', s_margin="auto", s_max_width="800px", )):
                        w(Tag('h1'), error)
                        await show_identity(request, writer)
            return web.Response(body=body.getvalue(), headers={'Content-Type': 'text/html; charset=utf-8'},
                                status=403 if identity else 401)


def ensure_authorized(handler, allowed_principals):

    async def ensure_authorized_inner(request):
        return await ensure_authorized_handler(request, allowed_principals, handler)

    return ensure_authorized_inner


def ensure_authorized_event(handler):

    async def ensure_authorized_inner(request, event):
        allowed_principals = event.admin_allowed_principals
        logger.debug(f'allowed_principals: {allowed_principals}')
        return await ensure_authorized_handler(request, allowed_principals, functools.partial(handler, event=event))

    return ensure_authorized_inner
