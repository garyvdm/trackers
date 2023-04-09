from itertools import chain

from trackers.base import Tracker


def time_key(item):
    return item.get("time") or item.get("server_time")


class Combined(Tracker):
    @classmethod
    async def start(cls, name, trackers, new_points_callbacks=(), reset_points_callbacks=()):
        tracker = cls(
            name,
            new_points_callbacks=new_points_callbacks,
            reset_points_callbacks=reset_points_callbacks,
        )
        tracker.trackers = list(trackers)
        tracker.sub_to_be_completed = len(tracker.trackers)

        points = tracker.get_sorted_points()
        for sub_tracker in trackers:
            sub_tracker.new_points_observable.subscribe(tracker.on_sub_new_points)
            sub_tracker.reset_points_observable.subscribe(tracker.on_sub_reset_points)
            sub_tracker.completed.add_done_callback(tracker.on_sub_completed)
        await tracker.new_points(points)

        if not trackers:
            tracker.completed.set_result(None)
        return tracker

    async def append_sub_tracker(self, tracker):
        assert not self.completed.done()
        self.trackers.append(tracker)
        if not tracker.completed.done():
            self.sub_to_be_completed += 1
        await self.on_sub_new_points(tracker, tracker.points)

        tracker.new_points_observable.subscribe(self.on_sub_new_points)
        tracker.reset_points_observable.subscribe(self.on_sub_reset_points)
        tracker.completed.add_done_callback(self.on_sub_completed)

    def get_sorted_points(self):
        return list(
            sorted(
                chain.from_iterable((sub_tracker.points for sub_tracker in self.trackers)),
                key=time_key,
            )
        )

    async def on_sub_new_points(self, sub_tracker, points):
        if points:
            points = list(sorted(points, key=time_key))
            is_sorted = not self.points or time_key(self.points[-1]) <= time_key(points[0])
            if is_sorted:
                await self.new_points(points)
            else:
                self.logger.debug("Points not sorted. Resting.")
                all_points_sorted = list(sorted(chain(self.points, points), key=time_key))
                await self.reset_points()
                await self.new_points(all_points_sorted)

    async def on_sub_reset_points(self, sub_tracker):
        new_points = self.get_sorted_points()
        if new_points != self.points:
            await self.reset_points()
            await self.new_points(new_points)

    def stop(self):
        for sub_tracker in self.trackers:
            sub_tracker.stop()

    def on_sub_completed(self, fut):
        self.sub_to_be_completed -= 1
        if self.sub_to_be_completed <= 0:
            self.completed.set_result(None)

    def set_finished(self):
        super().set_finished()
        for sub_tracker in self.trackers:
            sub_tracker.set_finished()
