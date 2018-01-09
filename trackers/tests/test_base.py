import asyncio
import logging
import unittest
import unittest.mock

import asynctest

from trackers.base import (
    call_callbacks,
    cancel_and_wait_task,
    list_register,
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
        tracker.stop = lambda: tracker.completed.set_result(None)
        new_points_callback = asynctest.CoroutineMock()
        tracker.new_points_callbacks.append(new_points_callback)

        await tracker.new_points([{'foo': 'bar'}])
        new_points_callback.assert_called_once_with(tracker, [{'foo': 'bar'}])

        tracker.stop()
        await tracker.complete()
        self.assertTrue(tracker.completed.done())
