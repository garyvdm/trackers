import asyncio
import contextlib
import functools
import logging
import pprint
from collections import deque
from itertools import chain
from pathlib import Path

import msgpack
import msgpack.fallback

logger = logging.getLogger(__name__)


class Tracker(object):
    def __init__(self, name, completed=None, new_points_callbacks=(), reset_points_callbacks=()):
        self.name = name
        self.points = []
        self.status = None
        self.logger = logging.getLogger("trackers.{}".format(name))
        self.new_points_observable = Observable(
            f"{self.name}.new_points", callbacks=new_points_callbacks
        )
        self.reset_points_observable = Observable(
            f"{self.name}.reset_points", callbacks=reset_points_callbacks
        )

        self.callback_tasks = []
        if completed is None:
            completed = asyncio.Future()
        else:
            completed = asyncio.ensure_future(completed)
        self.completed = completed
        self.finished = False

    def __repr__(self):
        return f"<{type(self).__name__}({self.name})>"

    async def new_points(self, new_points):
        self.points.extend(new_points)
        await self.new_points_observable(self, new_points)

    async def reset_points(self):
        self.points = []
        self.finished = False
        await self.reset_points_observable(self)

    def stop(self):
        if not self.completed.done():
            self.completed.set_result(None)

    async def complete(self):
        try:
            await self.completed
        except asyncio.CancelledError:
            raise

    def set_finished(self):
        self.finished = True


class Observable(object):
    def __init__(self, name, callbacks=(), error_msg="Error calling callback: "):
        self.name = name
        self.callbacks = []
        self.callbacks.extend(callbacks)
        self.error_msg = error_msg
        self.logger = logging.getLogger(f"observable.{name}")

    def subscribe(self, callback):
        self.callbacks.append(callback)

    def unsubscribe(self, callback):
        self.callbacks.remove(callback)

    async def __call__(self, *args, **kwargs):
        if self.logger.isEnabledFor(logging.DEBUG):
            self.logger.debug(f"Calling {self.callbacks}(*{args}, **{kwargs})"[:1000])
        for callback in self.callbacks:
            try:
                await callback(*args, **kwargs)
            except asyncio.CancelledError:
                raise
            except Exception:
                self.logger.exception(f"{self.error_msg} ({callback, args, kwargs})")


async def cancel_and_wait_task(task):
    task.cancel()
    try:
        return await task
    except asyncio.CancelledError:
        pass


def general_fut_done_callback(fut):
    try:
        fut.result()
    except asyncio.CancelledError:
        pass
    except Exception:
        logging.exception("")


def run_forget_task(coro):
    fut = asyncio.ensure_future(coro)
    fut.add_done_callback(general_fut_done_callback)
    return fut


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
        print("{} {}: \n{}".format(source.name, callback, pprint.pformat(data)))

    tracker.new_points_observable.subscribe(functools.partial(print_callback, "new_points"))

    for point in tracker.points:
        print("{} {}: \n{}".format(tracker.name, None, pprint.pformat(point)))


def get_blocked_list(source, existing, smallest_block_len=8, entire_block=False):
    source_len = len(source)

    if not entire_block:
        block_i = 0
        blocks = []
        for mul in (16, 8, 4, 1):
            block_len = smallest_block_len * mul

            while block_i + block_len < source_len:
                end_index = block_i + block_len - 1
                blocks.append(
                    {
                        "start_index": block_i,
                        "end_index": end_index,
                        "end_hash": source[end_index]["hash"],
                    }
                )
                block_i += block_len

        partial_block = list(source[block_i:])
    else:
        if source:
            blocks = [
                {
                    "start_index": 0,
                    "end_index": source[-1]["index"],
                    "end_hash": source[-1]["hash"],
                }
            ]
        else:
            blocks = []
        partial_block = []

    full = {"blocks": blocks, "partial_block": partial_block}

    if existing.get("blocks") != blocks:
        update = full
    else:
        existing_partial_block = existing.get("partial_block", ())
        if len(existing_partial_block) > len(partial_block):
            update = {"partial_block": partial_block}
        else:
            for existing_item, item in zip(
                existing_partial_block, partial_block[: len(existing_partial_block)]
            ):
                if (existing_item["hash"], existing_item["hash"]) != (
                    item["hash"],
                    item["hash"],
                ):
                    update = {"partial_block": partial_block}
                    break
            else:
                add_block = partial_block[len(existing_partial_block) :]
                if add_block:
                    update = {"add_block": add_block}
                else:
                    update = {}
    return full, update


class BlockedList(object):
    def __init__(self, source_name, get_source, new_update_callbacks=(), **kwargs):
        self.get_source = get_source
        self.kwargs = kwargs
        self.full, _ = get_blocked_list(get_source(), {}, **self.kwargs)
        self.last = self.full
        self.new_update_observable = Observable(
            f"{source_name}.blocked_list_new_update", callbacks=new_update_callbacks
        )

    @staticmethod
    def from_tracker(tracker, **kwargs):
        get_source = lambda: tracker.points
        blocked_list = BlockedList(tracker.name, get_source, **kwargs)

        async def tracker_change(tracker, *args):
            return await blocked_list.on_new_items()

        tracker.new_points_observable.subscribe(tracker_change)
        tracker.reset_points_observable.subscribe(tracker_change)
        return blocked_list

    async def on_new_items(self):
        self.full, update = get_blocked_list(self.get_source(), self.full, **self.kwargs)
        await self.new_update_observable(self, update)

    def get_update_from_last(self):
        self.last, update = get_blocked_list(self.get_source(), self.last, **self.kwargs)
        return update


# TODO create async version that uses io executor
@contextlib.contextmanager
def stream_store(path: Path, logger: logging.Logger):
    logging.debug("Reading data")
    if path.exists():
        with path.open("rb") as f:
            unpacker = msgpack.Unpacker(f, raw=False, timestamp=3)
            data = list(chain.from_iterable(unpacker))
        logging.info("Data loaded: {} items".format(len(data)))
    else:
        path.parent.mkdir(parents=True, exist_ok=True)
        data = []
        logging.info("Data file did not exist. Starting blank.")

    with path.open("ab", 0) as f:
        packer = msgpack.Packer(datetime=True)

        def write_items(items):
            packed = packer.pack(items)
            f.write(packed)
            f.flush()

        yield data, write_items


# From https://stackoverflow.com/a/46255794/72911


class RateLimitingSemaphore:
    def __init__(self, qps_limit, loop=None):
        self.loop = loop or asyncio.get_event_loop()
        self.qps_limit = qps_limit

        # The number of calls that are queued up, waiting for their turn.
        self.queued_calls = 0

        # The times of the last N executions, where N=qps_limit - this should allow us to calculate the QPS within the
        # last ~ second. Note that this also allows us to schedule the first N executions immediately.
        self.call_times = deque()

    async def __aenter__(self):
        self.queued_calls += 1
        while True:
            cur_rate = 0
            if len(self.call_times) == self.qps_limit:
                cur_rate = len(self.call_times) / (self.loop.time() - self.call_times[0])
            if cur_rate < self.qps_limit:
                break
            interval = 1.0 / self.qps_limit
            elapsed_time = self.loop.time() - self.call_times[-1]
            await asyncio.sleep(self.queued_calls * interval - elapsed_time)
        self.queued_calls -= 1

        if len(self.call_times) == self.qps_limit:
            self.call_times.popleft()
        self.call_times.append(self.loop.time())

    async def __aexit__(self, exc_type, exc, tb):
        pass
