import os
from argparse import Namespace
from tempfile import NamedTemporaryFile
from textwrap import dedent

import asynctest

from trackers.bin_utils import (
    add_gpx_to_event_routes,
    assign_rider_colors,
    convert_to_static,
)
from trackers.events import Event
from trackers.tests.test_events import TestEventWithMockTracker


class TestConvertToStatic(asynctest.TestCase, TestEventWithMockTracker):
    async def test_mock(self):
        app, settings, writer = self.do_setup(
            """
            tracker_end: 2019-01-01 00:00:00
            riders:
              - name: foo
                tracker: {type: mock}
        """
        )
        await convert_to_static.__wrapped__(app, settings, Namespace(event_name="test_event"))

        writer.reset()
        self.assertEqual(
            writer.get("events/test_event/data.yaml").data.decode(),
            dedent(
                """
            tracker_end: 2019-01-01 00:00:00
            live: false
            static_analyse: true
            riders:
            - name: foo
            """
            ).lstrip("\n"),
        )
        self.assertEqual(writer.get("events/test_event/static/foo/source").data, b"\x90")


class TestAssignRiderColors(asynctest.TestCase, TestEventWithMockTracker):
    maxDiff = None

    async def test(self):
        app, settings, writer = self.do_setup(
            """
            riders:
              - {}
              - {}
              - {}
        """
        )
        await assign_rider_colors.__wrapped__(app, settings, Namespace(event_name="test_event"))

        writer.reset()
        self.assertEqual(
            writer.get("events/test_event/data.yaml").data.decode(),
            dedent(
                """
            riders:
            - color: hsl(0, 100%, 50%)
              color_marker: hsl(0, 100%, 60%)
              color_pre_post: hsl(0, 100%, 70%)
            - color: hsl(120, 100%, 50%)
              color_marker: hsl(120, 100%, 60%)
              color_pre_post: hsl(120, 100%, 70%)
            - color: hsl(240, 100%, 50%)
              color_marker: hsl(240, 100%, 60%)
              color_pre_post: hsl(240, 100%, 70%)
            """
            ).lstrip("\n"),
        )


class TestAddGpxToEventRoutes(asynctest.TestCase, TestEventWithMockTracker):
    async def test(self):
        app, settings, writer = self.do_setup("{}")

        with NamedTemporaryFile(delete=False) as f:
            f.write(
                dedent(
                    """
                <?xml version="1.0"?>
                <gpx version="1.1" xmlns="http://www.topografix.com/GPX/1/1" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" xsi:schemaLocation="http://www.topografix.com/GPX/1/1 http://www.topografix.com/GPX/1/1/gpx.xsd">
                <wpt lon="-26.09321" lat="-27.9813">
                  <name>Start</name>
                </wpt>
                <trk>
                  <name>Test GPX route</name>
                  <trkseg>
                    <trkpt lat="-26.09321" lon="27.9813"></trkpt>
                    <trkpt lat="-26.0933" lon="27.98154"></trkpt>
                    <trkpt lat="-26.09341" lon="27.98186"></trkpt>
                  </trkseg>
                </trk>
                </gpx>
            """
                )
                .lstrip("\n")
                .encode()
            )

        self.addCleanup(os.remove, f.name)

        await add_gpx_to_event_routes.__wrapped__(
            app,
            settings,
            Namespace(
                event_name="test_event",
                gpx_file=f.name,
                no_elevation=True,
                split_at_dist=[],
                split_point_range=1000,
                rdp_epsilon=2,
                circular_range=None,
                print=False,
                replace_main=False,
            ),
        )

        writer.reset()
        event = await Event.load(app, "test_event", writer)
        self.assertEqual(
            event.routes,
            [
                {
                    "original_points": [
                        [-26.09321, 27.9813],
                        [-26.0933, 27.98154],
                        [-26.09341, 27.98186],
                    ],
                    "points": [[-26.09321, 27.9813], [-26.09341, 27.98186]],
                    "main": True,
                    "split_at_dist": [],
                    "split_point_range": 1000,
                    "rdp_epsilon": 2,
                    "no_elevation": True,
                    "simplified_points_indexes": [0, 1],
                    "circular_range": None,
                    "gpx_file": f.name,
                }
            ],
        )

        self.assertEqual(
            event.config,
            {
                "markers": [
                    {
                        "title": "Start",
                        "svg_marker": {"direction": "sw", "text": "Start"},
                        "position": {"lat": -27.9813, "lng": -26.09321},
                    }
                ]
            },
        )
