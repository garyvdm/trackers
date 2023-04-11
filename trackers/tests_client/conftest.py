import os.path

import arsenic
import structlog
from pytest import fixture, param


# To make arsenic quite
def dropper(logger, method_name, event_dict):
    raise structlog.DropEvent


structlog.configure(processors=[dropper])


@fixture(
    params=[
        param(
            (
                arsenic.services.Chromedriver(log_file=os.devnull),
                arsenic.browsers.Chrome(),
            ),
            id="chrome",
        ),
        # param(
        #     arsenic.services.Geckodriver(log_file=os.devnull),
        #     arsenic.browsers.Firefox(),
        #     id="firefox",
        # ),
        # param(
        #     arsenic.services.Chromedriver(log_file=os.devnull),
        #     arsenic.browsers.Chrome(chromeOptions={"args": ["--headless", "--disable-gpu"]}),
        #     id="chrome-headless",
        # ),
    ],
)
async def browser(request):
    async with arsenic.get_session(*request.param) as session:
        yield session
