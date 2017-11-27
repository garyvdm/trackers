import setuptools


setuptools.setup(
    name='trackers',
    packages=setuptools.find_packages(),
    include_package_data=True,
    install_requires=[
        'aiohttp>=2.0',
        'aniso8601',
        'arsenic',
        'asyncio-contextmanager',
        'asynctest',
        'attrs',
        'beautifulsoup4',
        'dulwich',
        'filemagic',
        'fixtures',
        'more-itertools',
        'msgpack-python',
        'nvector',
        'polyline',
        'python-slugify',
        'pyyaml',
        'tap.py',
        'testresources',
        'testscenarios',
        'uvloop',
    ],
    entry_points={
        'console_scripts': [
            'serve=trackers.serve:main',
            'convert_to_static=trackers.bin_utils:convert_to_static',
            'assign_rider_colors=trackers.bin_utils:assign_rider_colors',
            'add_gpx_to_event_routes=trackers.bin_utils:add_gpx_to_event_routes',
            'reformat_event=trackers.bin_utils:reformat_event',
            'process_event_routes=trackers.bin_utils:process_event_routes',
        ],
    },
    test_suite='trackers.tests.suite',
)
