import asyncio
from contextlib import asynccontextmanager
from datetime import datetime, timedelta

import aiohttp
from aniso8601 import parse_datetime

from trackers.base import Tracker, print_tracker


@asynccontextmanager
async def config(app, settings):
    async with aiohttp.ClientSession(connector=aiohttp.TCPConnector(limit=4)) as app[
        "matrix.session"
    ]:
        yield


def date_format(dt: datetime):
    return dt.strftime("%Y-%m-%dT%H_%M_%S")


async def start_event_tracker(app, event, rider_name, tracker_data, start, end):
    tracker = MatrixTracker(
        app,
        rider_name,
        tracker_data["username"],
        tracker_data["password"],
        tracker_data["client_id"],
        tracker_data["asset_id"],
        start,
        end,
    )
    await tracker.start()
    return tracker


# async with session.get(f'https://api-mit.mixtelematics.com/api/tracking/positions/list/client/{client_id}',
#                        headers=headers) as response:
#     response.raise_for_status()
#     data = await response.json()
#     pprint.pprint(data)


class MatrixTracker(Tracker):
    def __init__(self, app, tracker_name, username, password, client_id, asset_id, start, end):
        super().__init__("matrix.{}-{}".format(asset_id, tracker_name))
        self.session: aiohttp.ClientSession = app["matrix.session"]
        self.username = username
        self.password = password
        self.client_id = client_id
        self.asset_id = asset_id
        self.seen_position_ids = set()
        self.start_time = start
        self.end_time = end
        self.headers = ()
        self.logged_in = None
        self.last_get_points = start

    async def start(self):
        await self.login()
        await self.get_points()

        self.monitor_task = asyncio.ensure_future(self.monitor())
        self.stop = self.monitor_task.cancel

    async def login(self):
        self.logger.debug("Attempting Login.")

        login_data = {
            "rememberMe": True,
            "preferredLanguage": None,
            "userName": self.username,
            "password": self.password,
        }
        async with self.session.post(
            "https://api-mit.mixtelematics.com/api/login", json=login_data
        ) as response:
            data = await response.json()
            if isinstance(data, str) and data.startswith("ErrorNo:"):
                self.logger.error("Login Failed")
                self.logged_in = False
                # TODO Set status point, and maybe complete.
            else:
                self.logged_in = True
                self.headers = (("x-auth", data["authenticationToken"]),)

    async def get(self, *args, **kwargs):
        self.start_time, self.end_time or (datetime.now() + timedelta(days=1))

        response: aiohttp.ClientResponse = await self.session.get(
            *args, headers=self.headers, **kwargs
        )
        if response.status == 401 and response.reason == "X-auth Not valid":
            response.release()
            await self.login()
            # retry
            response = await self.session.get(*args, headers=self.headers or (), **kwargs)

        async with response:
            response.raise_for_status()
            return await response.json()

    async def get_points(self):
        if not self.logged_in:
            return

        try:
            start = self.last_get_points
            end = self.end_time or (datetime.now() + timedelta(minutes=1))
            self.logger.debug(f"Getting points from {start} to {end}  ({end - start}).")

            url = (
                f"https://api-mit.mixtelematics.com/api/tracking/trip/positions/client/{self.client_id}"
                f"/asset/{self.asset_id}/fromDate/{date_format(start)}/toDate/{date_format(end)}"
            )

            positions = (await self.get(url))["items"]
            new_points = []
            new_position_ids = []
            for position in positions:
                positionId = position["positionId"]
                if positionId not in self.seen_position_ids:
                    time = (
                        parse_datetime(position["positionDateTime"]["dateTime"])
                        .astimezone()
                        .replace(tzinfo=None)
                    )
                    self.last_get_points = max(self.last_get_points, time)
                    new_points.append(
                        {
                            "position": [
                                position["position"]["latitude"],
                                position["position"]["longitude"],
                            ],
                            "time": time,
                            "server_time": datetime.now(),
                            "status": position["status"],
                        }
                    )
                    new_position_ids.append(positionId)

            self.seen_position_ids.update(new_position_ids)
            if new_points:
                await self.new_points(new_points)
        except asyncio.CancelledError:
            raise
        except Exception:
            self.logger.exception("Error in get_points: ")

    async def monitor(self):
        try:
            while self.end_time and datetime.now() < self.end_time or not self.end_time:
                await asyncio.sleep(60)
                await self.get_points()
        finally:
            self.completed.set_result(None)


async def main():
    client_id = 93401
    asset_id = 270834
    username = "LEUENBERGER"
    password = ""

    import signal

    app = {}
    settings = {}
    async with config(app, settings):
        tracker = MatrixTracker(
            app,
            "foobar",
            username,
            password,
            client_id,
            asset_id,
            datetime(2019, 3, 18),
            None,
        )
        await tracker.start()
        print_tracker(tracker)

        run_fut = asyncio.Future()
        for signame in ("SIGINT", "SIGTERM"):
            loop.add_signal_handler(getattr(signal, signame), run_fut.set_result, None)
        try:
            await run_fut
        finally:
            for signame in ("SIGINT", "SIGTERM"):
                loop.remove_signal_handler(getattr(signal, signame))
        tracker.stop()
        await tracker.complete()


if __name__ == "__main__":
    import logging

    logging.basicConfig(level=logging.DEBUG)
    loop = asyncio.get_event_loop()
    loop.run_until_complete(main())
