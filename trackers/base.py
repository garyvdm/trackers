import asyncio
import functools
import logging
import pprint


logger = logging.getLogger(__name__)


class Tracker(object):

    def __init__(self, name):
        self.name = name
        self.points = []
        self.status = None
        self.new_points_callbacks = []
        self.logger = logging.getLogger('trackers.{}'.format(name))
        self.callback_tasks = []

    async def new_points(self, new_points):
        self.points.extend(new_points)
        await call_callbacks(self.new_points_callbacks, 'Error calling new_points callback:', self.logger, self, new_points)

    async def stop(self):
        await self.stop_specific()

    async def stop_specific(self):
        pass

    async def finish(self):
        await self.finish_specific()

    async def finish_specific(self):
        pass


async def call_callbacks(callbacks, error_msg, logger, *args, **kwargs):
    for callback in callbacks:
        try:
            await callback(*args, **kwargs)
        except asyncio.CancelledError:
            pass
        except Exception:
            logger.exception(error_msg)


def callback_done_callback(error_msg, logger, fut):
    try:
        fut.result()
    except asyncio.CancelledError:
        pass
    except Exception:
        logger.exception(error_msg)


async def cancel_and_wait_task(task):
    task.cancel()
    try:
        return await task
    except asyncio.CancelledError:
        pass


async def wait_task(task):
    return await task


def print_tracker(tracker):

    async def print_callback(callback, source, data):
        print('{} {}: \n{}'.format(source.name, callback, pprint.pformat(data)))

    tracker.new_points_callbacks.append(functools.partial(print_callback, 'new_points'))

    for point in tracker.points:
        print('{} {}: \n{}'.format(tracker.name, None, pprint.pformat(point)))
