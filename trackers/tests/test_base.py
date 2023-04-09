import asyncio
import logging
from unittest import IsolatedAsyncioTestCase, TestCase
from unittest.mock import AsyncMock, Mock

from trackers.base import Observable, Tracker, cancel_and_wait_task, list_register


class TestObservable(IsolatedAsyncioTestCase):
    async def test_normal(self):
        callback = AsyncMock()
        await Observable("test", callbacks=[callback])(foo="bar")
        callback.assert_called_once_with(foo="bar")

    async def test_error(self):
        logger = logging.getLogger("observable.test")
        normal_callback = AsyncMock()

        async def raise_error_callback():
            raise Exception("foo")

        with self.assertLogs(logger) as (_, log_output):
            await Observable(
                "test",
                callbacks=[raise_error_callback, normal_callback],
                error_msg="error_msg",
            )()

        # the error callback should not stop the following callbacks
        normal_callback.assert_called_once_with()
        self.assertEqual(len(log_output), 1)
        self.assertTrue(log_output[0].startswith("ERROR:observable.test:error_msg "))

    async def test_cancel_still_raises(self):
        logger = logging.getLogger("call_callbacks")

        async def slow_callback():
            await asyncio.sleep(1)

        observable = Observable(logger, callbacks=[slow_callback])

        with self.assertRaises(asyncio.CancelledError):
            fut = asyncio.ensure_future(observable())
            await asyncio.sleep(0.1)
            fut.cancel()
            await fut


class TestCancelAndWait(IsolatedAsyncioTestCase):
    async def test(self):
        task = asyncio.ensure_future(asyncio.sleep(1))
        await cancel_and_wait_task(task)
        self.assertTrue(task.cancelled())


class TestListRegister(TestCase):
    def test(self):
        register = []
        with list_register(register, 1, yield_item=2) as yield_item:
            self.assertEqual(register, [1])
            self.assertEqual(yield_item, 2)
        self.assertEqual(register, [])

    def test_on_empty(self):
        register = []
        on_empty = Mock()
        with list_register(register, 1, on_empty=on_empty):
            on_empty.assert_not_called()
        on_empty.assert_called_once_with()


class TestTracker(IsolatedAsyncioTestCase):
    async def test(self):
        new_points_callback = AsyncMock()
        tracker = Tracker("test", new_points_callbacks=(new_points_callback,))
        tracker.stop = lambda: tracker.completed.set_result(None)

        await tracker.new_points([{"foo": "bar"}])
        new_points_callback.assert_called_once_with(tracker, [{"foo": "bar"}])

        tracker.stop()
        await tracker.complete()
        self.assertTrue(tracker.completed.done())
