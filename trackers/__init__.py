import asyncio
import functools
import logging


class Tracker(object):

    def __init__(self, name):
        self.name = name
        self.points = []
        self.status = {}
        self.new_points_callbacks = []
        self.logger = logging.getLogger('tracker.{}'.format(name))

    async def new_points(self, new_points):
        self.points.extend(new_points)
        await call_callbacks(self.new_points_callbacks, 'Error calling new_points callback:', self.logger, self, new_points)

    async def stop(self):
        pass


async def call_callbacks(callbacks, error_msg, logger, *args, **kwargs):
    loop = asyncio.get_event_loop()
    tasks = [loop.create_task(callback(*args, **kwargs)) for callback in callbacks]
    done_callback = functools.partial(callback_done_callback, error_msg, logger)
    for task in tasks:
        task.add_done_callback(done_callback)


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
        await task
    except Exception:
        pass


def print_tracker(tracker):

    import pprint
    async def print_callback(callback, source, data):
        print('{} {}: \n{}'.format(source.name, callback, pprint.pformat(data)))

    tracker.new_points_callbacks.append(functools.partial(print_callback, 'new_points'))
