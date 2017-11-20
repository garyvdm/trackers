import asyncio
import logging
import unittest
import unittest.mock
from functools import partial

import asynctest

from trackers.base import (
    call_callbacks,
    cancel_and_wait_task,
    list_register,
    log_error_callback,
    Tracker,
)


class TestCallCallbacks(asynctest.TestCase):

    async def test_normal(self):
        logger = logging.getLogger('call_callbacks')
        callback = asynctest.CoroutineMock()
        await call_callbacks([callback], 'error_msg', logger, foo='bar')
        callback.assert_called_once_with(foo='bar')

    async def test_error(self):
        logger = logging.getLogger('call_callbacks')
        normal_callback = asynctest.CoroutineMock()

        async def raise_error_callback():
            raise Exception('foo')

        with self.assertLogs(logger) as (_, log_output):
            await call_callbacks([raise_error_callback, normal_callback], 'error_msg', logger)

        # the error callback should not stop the following callbacks
        normal_callback.assert_called_once_with()
        self.assertEqual(len(log_output), 1)
        self.assertTrue(log_output[0].startswith('ERROR:call_callbacks:error_msg'))

    async def test_cancel_still_raises(self):
        logger = logging.getLogger('call_callbacks')

        async def slow_callback():
            await asyncio.sleep(1)

        with self.assertRaises(asyncio.CancelledError):
            fut = asyncio.ensure_future(call_callbacks([slow_callback], 'error_msg', logger))
            await asyncio.sleep(0.1)
            fut.cancel()
            await fut


class TestLogErrorCallback(asynctest.TestCase):

    async def test_success(self):
        fut = asyncio.Future()
        logger = logging.getLogger('callbacks')
        fut.add_done_callback(partial(log_error_callback, logger, 'error_msg'))
        fut.set_result(None)
        # yield to event loop, soo that it call's the callback
        await asyncio.sleep(0)

    async def test_error(self):
        logger = logging.getLogger('callbacks')
        with self.assertLogs(logger, level=logging.ERROR) as (_, log_output):
            fut = asyncio.Future()
            fut.add_done_callback(partial(log_error_callback, logger, 'error_msg'))
            fut.set_exception(Exception('foo'))

            # yield to event loop, soo that it call's the callback
            await asyncio.sleep(0)

        self.assertEqual(len(log_output), 1)
        self.assertTrue(log_output[0].startswith('ERROR:callbacks:error_msg'))


class TestCancelAndWait(asynctest.TestCase):

    async def test(self):
        task = asyncio.ensure_future(asyncio.sleep(1))
        await cancel_and_wait_task(task)
        self.assertTrue(task.cancelled())


class TestListRegister(unittest.TestCase):

    def test(self):
        register = []
        with list_register(register, 1, yield_item=2) as yield_item:
            self.assertEqual(register, [1])
            self.assertEqual(yield_item, 2)
        self.assertEqual(register, [])

    def test_on_empty(self):
        register = []
        on_empty = unittest.mock.Mock()
        with list_register(register, 1, on_empty=on_empty):
            on_empty.assert_not_called()
        on_empty.assert_called_once_with()


class TestTracker(asynctest.TestCase):

    async def test(self):

        tracker = Tracker('test')
        tracker.stop_specific = asynctest.CoroutineMock()
        tracker.finish_specific = asynctest.CoroutineMock()
        new_points_callback = asynctest.CoroutineMock()
        tracker.new_points_callbacks.append(new_points_callback)

        await tracker.new_points([{'foo': 'bar'}])
        new_points_callback.assert_called_once_with(tracker, [{'foo': 'bar'}])

        await tracker.stop()
        tracker.stop_specific.assert_called_once_with()

        self.assertFalse(tracker.is_finished)
        await tracker.finish()
        tracker.stop_specific.assert_called_once_with()
        self.assertTrue(tracker.is_finished)
