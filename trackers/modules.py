import trackers.general
import trackers.sources.garmin_livetrack
import trackers.sources.map_my_tracks
import trackers.sources.traccar
from trackers.async_exit_stack import AsyncExitStack


async def config_modules(app, settings):
    exit_stack = AsyncExitStack()

    modules = (
        trackers.sources.map_my_tracks.config,
        trackers.sources.traccar.config,
        # trackers.garmin_livetrack.config,
    )

    for module in modules:
        await exit_stack.enter_context(module(app, settings))
    return exit_stack


start_event_trackers = {
    'mapmytracks': trackers.sources.map_my_tracks.start_event_tracker,
    # 'garmin_livetrack': trackers.garmin_livetrack.start_event_tracker,
    'traccar': trackers.sources.traccar.start_event_tracker,
    'static': trackers.general.static_start_event_tracker,
    'cropped': trackers.general.cropped_tracker_start_event,
}
