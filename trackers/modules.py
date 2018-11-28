from functools import partial

from trackers.async_exit_stack import AsyncExitStack


async def config_modules(app, settings):
    import trackers.general
    import trackers.sources.map_my_tracks
    import trackers.sources.traccar
    import trackers.sources.spot
    import trackers.sources.tkstorage
    import trackers.sources.trackleaders

    exit_stack = AsyncExitStack()

    modules = (
        trackers.sources.map_my_tracks.config,
        trackers.sources.traccar.config,
        trackers.sources.spot.config,
        trackers.sources.tkstorage.config,
        trackers.sources.trackleaders.config,
    )

    for module in modules:
        await exit_stack.enter_context(module(app, settings))

    app['start_event_trackers'] = {
        'mapmytracks': trackers.sources.map_my_tracks.start_event_tracker,
        'traccar': trackers.sources.traccar.start_event_tracker,
        'spot': trackers.sources.spot.start_event_tracker,
        'tkstorage': trackers.sources.tkstorage.start_event_tracker,
        'static': trackers.general.static_start_event_tracker,
        'cropped': partial(trackers.general.wrapped_tracker_start_event, trackers.general.cropped_tracker_start),
        'filter_inaccurate': partial(trackers.general.wrapped_tracker_start_event, trackers.general.filter_inaccurate_tracker_start),
        'trackleaders': trackers.sources.trackleaders.start_event_tracker
    }
    return exit_stack
