import setuptools


setuptools.setup(
    name='trackers',
    packages=setuptools.find_packages(),
    include_package_data=True,
    install_requires=[
        'aiohttp>=2.0',
        'uvloop',
        'beautifulsoup4',
        'pyyaml',
    ],
    entry_points={
        'console_scripts': [
            'serve=trackers.serve:main',
        ],
    },
    # test_suite='trackers.tests.suite',
)
