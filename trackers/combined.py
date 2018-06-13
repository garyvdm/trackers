from itertools import chain

from trackers.base import Tracker


def time_key(item):
    return item.get('time') or item.get('server_time')


class Combined(Tracker):

    @classmethod
    async def start_tracker(cls, name, trackers, new_points_callbacks=(), reset_points_callbacks=()):
        tracker = cls(name, new_points_callbacks=new_points_callbacks, reset_points_callbacks=reset_points_callbacks)
        tracker.trackers = trackers
        tracker.sub_to_be_completed = len(trackers)
        for sub_tracker in trackers:
            sub_tracker.new_points_observable.subscribe(tracker.on_sub_new_points)
            sub_tracker.completed.add_done_callback(tracker.on_sub_completed)
        return tracker

    async def on_sub_new_points(self, sub_tracker, points):
        if points:
            points = list(sorted(points, key=time_key))
            is_sorted = not self.points or time_key(self.points[-1]) <= time_key(points[0])
            if is_sorted:
                await self.new_points(points)
            else:
                all_points_sorted = list(sorted(chain(self.points, points), key=time_key))
                await self.reset_points()
                await self.new_points(all_points_sorted)

    def stop(self):
        for sub_tracker in self.trackers:
            sub_tracker.stop()

    def on_sub_completed(self, fut):
        self.sub_to_be_completed -= 1
        if self.sub_to_be_completed <= 0:
            self.completed.set_result(None)