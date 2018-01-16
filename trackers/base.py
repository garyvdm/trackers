import asyncio
import contextlib
import functools
import logging
import pprint


logger = logging.getLogger(__name__)


class Tracker(object):

    def __init__(self, name, completed=None, new_points_callbacks=()):
        self.name = name
        self.points = []
        self.status = None
        self.logger = logging.getLogger('trackers.{}'.format(name))
        self.new_points_observable = Observable(self.logger, callbacks=new_points_callbacks)
        self.callback_tasks = []
        if completed is None:
            completed = asyncio.Future()
        else:
            completed = asyncio.ensure_future(completed)
        self.completed = completed

    async def new_points(self, new_points):
        self.points.extend(new_points)
        await self.new_points_observable(self, new_points)

    def stop(self):
        pass

    async def complete(self):
        try:
            await self.completed
        except asyncio.CancelledError:
            pass


class Observable(object):

    def __init__(self, logger, callbacks=(), error_msg='Error calling callback: '):
        self.callbacks = []
        self.callbacks.extend(callbacks)
        self.error_msg = error_msg
        self.logger = logger

    def subscribe(self, callback):
        self.callbacks.append(callback)

    def unsubscribe(self, callback):
        self.callbacks.remove(callback)

    async def __call__(self, *args, **kwargs):
        for callback in self.callbacks:
            try:
                await callback(*args, **kwargs)
            except asyncio.CancelledError:
                raise
            except Exception:
                print((args, kwargs))
                self.logger.exception(self.error_msg)


async def cancel_and_wait_task(task):
    task.cancel()
    try:
        return await task
    except asyncio.CancelledError:
        pass


@contextlib.contextmanager
def list_register(list, item, on_empty=None, yield_item=None):
    list.append(item)
    try:
        yield yield_item
    finally:
        list.remove(item)
        if not list and on_empty:
            on_empty()


def print_tracker(tracker):

    async def print_callback(callback, source, data):
        print('{} {}: \n{}'.format(source.name, callback, pprint.pformat(data)))

    tracker.new_points_callbacks.append(functools.partial(print_callback, 'new_points'))

    for point in tracker.points:
        print('{} {}: \n{}'.format(tracker.name, None, pprint.pformat(point)))


def get_blocked_list(source, existing, smallest_block_len=8, entire_block=False):
    source_len = len(source)

    if not entire_block:
        block_i = 0
        blocks = []
        for mul in (16, 8, 4, 1):
            block_len = smallest_block_len * mul

            while block_i + block_len < source_len:
                end_index = block_i + block_len - 1
                blocks.append({'start_index': block_i, 'end_index': end_index, 'end_hash': source[end_index]['hash']})
                block_i += block_len

        partial_block = list(source[block_i:])
    else:
        if source:
            blocks = [{'start_index': 0, 'end_index': source[-1]['index'], 'end_hash': source[-1]['hash'], }]
        else:
            blocks = []
        partial_block = []

    full = {'blocks': blocks, 'partial_block': partial_block}

    if existing.get('blocks') != blocks:
        update = full
    else:
        existing_partial_block = existing.get('partial_block', ())
        if len(existing_partial_block) > len(partial_block):
            update = {'partial_block': partial_block}
        else:
            for existing_item, item in zip(existing_partial_block, partial_block[:len(existing_partial_block)]):
                if (existing_item['hash'], existing_item['hash']) != (item['hash'], item['hash']):
                    update = {'partial_block': partial_block}
                    break
            else:
                add_block = partial_block[len(existing_partial_block):]
                if add_block:
                    update = {'add_block': add_block}
                else:
                    update = {}
    return full, update


class BlockedList(object):

    def __init__(self, source, new_update_callbacks=(), **kwargs):
        self.source = source
        self.kwargs = kwargs
        self.full, _ = get_blocked_list(self.source, {}, **self.kwargs)
        self.new_update_observable = Observable(logger, callbacks=new_update_callbacks)

    @staticmethod
    def from_tracker(tracker, **kwargs):
        blocked_list = BlockedList(tracker.points, **kwargs)

        def tracker_callback(tracker, newpoints):
            return blocked_list.on_new_items()

        tracker.new_points_observable.subscribe(tracker_callback)
        return blocked_list

    async def on_new_items(self):
        self.full, update = get_blocked_list(self.source, self.full, **self.kwargs)
        await self.new_update_observable(self, update)
