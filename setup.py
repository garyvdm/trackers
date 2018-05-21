import setuptools


setuptools.setup(
    name='trackers',
    packages=setuptools.find_packages(),
    include_package_data=True,
    install_requires=[
        'aioauth-client',
        'aiohttp',
        'aiohttp_session[secure]',
        'aiomsgpack',
        'aionotify',
        'aniso8601',
        'arsenic',
        'asyncio-contextmanager',
        'asynctest',
        'attrs',
        'beautifulsoup4',
        'dulwich',
        'filemagic',
        'fixtures',
        'htmlwrite',
        'jsonpointer',
        'libsass',
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

            # Tools to edit events
            'convert_to_static=trackers.bin_utils:convert_to_static',
            'assign_rider_colors=trackers.bin_utils:assign_rider_colors',
            'add_gpx_to_event_routes=trackers.bin_utils:add_gpx_to_event_routes',
            'reformat_event=trackers.bin_utils:reformat_event',
            'process_event_routes=trackers.bin_utils:process_event_routes',
            'update_bounds=trackers.bin_utils:update_bounds',
            'load_riders_from_csv=trackers.bin_utils:load_riders_from_csv',

            'run_qunit_tests=trackers.client_test_tools:qunit_runner',
            'gen_key=trackers.auth:gen_key',

        ],
    },
    test_suite='trackers.tests.suite',
)
