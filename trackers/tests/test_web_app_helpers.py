import logging
import unittest
import unittest.mock

import asynctest
from aiohttp.test_utils import make_mocked_request
from aiohttp.web import HTTPException, HTTPOk, Response

from trackers import web_app


class TestETagHelpers(unittest.TestCase):
    def test_etag_response_no_cache(self):
        request = make_mocked_request("GET", "/")
        response = Response(text="hello")
        etag = "mock_ETag"

        new_response = web_app.etag_response(request, response, etag)

        self.assertEqual(new_response.status, 200)
        self.assertEqual(new_response.headers["ETag"], "mock_ETag")
        self.assertEqual(new_response.headers["Cache-Control"], "public")

    def test_etag_response_correct_etag(self):
        request = make_mocked_request("GET", "/", headers={"If-None-Match": "mock_ETag"})
        response = Response(text="hello")
        etag = "mock_ETag"

        new_response = web_app.etag_response(request, response, etag)

        self.assertEqual(new_response.status, 304)

    def test_etag_response_wrong_etag(self):
        request = make_mocked_request("GET", "/", headers={"If-None-Match": "wrond_ETag"})
        response = Response(text="hello")
        etag = "mock_ETag"

        new_response = web_app.etag_response(request, response, etag)

        self.assertEqual(new_response.status, 200)

    def test_etag_response_callable_response(self):
        request = make_mocked_request("GET", "/")
        etag = "mock_ETag"

        new_response = web_app.etag_response(request, lambda: Response(text="hello"), etag)

        self.assertEqual(new_response.status, 200)
        self.assertEqual(new_response.headers["ETag"], "mock_ETag")
        self.assertEqual(new_response.headers["Cache-Control"], "public")

    def test_etag_response_correct_etag_callable_response(self):
        request = make_mocked_request("GET", "/", headers={"If-None-Match": "mock_ETag"})
        response = unittest.mock.Mock()
        etag = "mock_ETag"

        web_app.etag_response(request, response, etag)

        response.assert_not_called()

    def test_etag_query_hash_response_correct_hash(self):
        request = make_mocked_request("GET", "/?hash=mock_ETag")
        response = Response(text="hello")
        etag = "mock_ETag"

        new_response = web_app.etag_query_hash_response(request, response, etag)
        self.assertEqual(new_response.status, 200)
        self.assertEqual(new_response.headers["ETag"], "mock_ETag")
        self.assertEqual(new_response.headers["Cache-Control"], "public,max-age=31536000,immutable")

    def test_etag_query_hash_response_wrong_hash(self):
        request = make_mocked_request("GET", "/?hash=wrong_ETag")
        response = Response(text="hello")
        etag = "mock_ETag"

        new_response = web_app.etag_query_hash_response(request, response, etag)
        self.assertEqual(new_response.status, 302)
        self.assertEqual(new_response.headers["Location"], "/?hash=mock_ETag")

    def test_etag_query_hash_response_no_hash(self):
        request = make_mocked_request("GET", "/")
        response = Response(text="hello")
        etag = "mock_ETag"

        new_response = web_app.etag_query_hash_response(request, response, etag)
        self.assertEqual(new_response.status, 200)


class TestSayErrorHandler(asynctest.TestCase):
    async def test_no_error(self):
        @web_app.say_error_handler
        async def no_error(request):
            return Response(text="hello")

        request = make_mocked_request("GET", "/")
        response = await no_error(request)
        self.assertEqual(response.text, "hello")

    async def test_error(self):
        @web_app.say_error_handler
        async def error(request):
            raise Exception("an error")

        exception_recorder = unittest.mock.Mock()

        request = make_mocked_request("GET", "/", app={"exception_recorder": exception_recorder})
        with self.assertLogs("trackers.web_app", level=logging.ERROR):
            response = await error(request)

        self.assertEqual(response.status, 500)
        self.assertEqual(response.text, "Exception: an error")
        exception_recorder.assert_called_once_with()

    async def test_pass_on_weberror(self):
        @web_app.say_error_handler
        async def error(request):
            raise HTTPOk(text="hello")

        request = make_mocked_request("GET", "/")
        try:
            await error(request)
        except HTTPException as response:
            self.assertEqual(response.status, 200)
