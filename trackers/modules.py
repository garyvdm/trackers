from contextlib import AsyncExitStack


async def config_modules(app, settings):
    import trackers.sources.map_my_tracks
    import trackers.sources.traccar
    import trackers.sources.spot
    import trackers.sources.tkstorage
    import trackers.sources.trackleaders
    import trackers.sources.matrix

    modules = (
        trackers.sources.map_my_tracks.config,
        trackers.sources.traccar.config,
        trackers.sources.spot.config,
        trackers.sources.tkstorage.config,
        trackers.sources.trackleaders.config,
        trackers.sources.matrix.config,
    )

    source_trackers = {
        'mapmytracks': trackers.sources.map_my_tracks.start_event_tracker,
        'traccar': trackers.sources.traccar.start_event_tracker,
        'spot': trackers.sources.spot.start_event_tracker,
        'tkstorage': trackers.sources.tkstorage.start_event_tracker,
        'trackleaders': trackers.sources.trackleaders.start_event_tracker,
        'matrix': trackers.sources.matrix.start_event_tracker,
    }

    exit_stack = AsyncExitStack()

    for module in modules:
        await exit_stack.enter_async_context(module(app, settings))

    app['start_event_trackers'].update(source_trackers)
    return exit_stack
