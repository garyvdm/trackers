import testresources
import testscenarios
from selenium import webdriver


def load_tests(loader, tests, pattern):
    scenarios = testscenarios.generate_scenarios(tests)
    return testresources.OptimisingTestSuite(scenarios)


class WebDriverResource(testresources.TestResourceManager):

    def __init__(self, driver_cls, *args, **kwargs):
        super().__init__()
        self.driver_cls = driver_cls
        self.args = args
        self.kwargs = kwargs

    def make(self, dependency_resources):
        return self.driver_cls(*self.args, **self.kwargs)

    def clean(self, driver):
        driver.quit()

    def _reset(self, driver, dependency_resources):
        driver.get('about:blank')
        driver.delete_all_cookies()
        return driver

    def isDirty(self):
        return True


class TestPythonOrgSearch(testresources.ResourcedTestCase):

    scenarios = [
        ('phantomjs', dict(driver_resource_manager=WebDriverResource(webdriver.PhantomJS, service_log_path='/dev/null'))),
        # ('firefox', dict(driver_resource_manager=WebDriverResource(webdriver.Firefox, log_path='/dev/null'))),
        # ('chrome', dict(driver_resource_manager=WebDriverResource(webdriver.Chrome))),
    ]

    @property
    def resources(self):
        return [("driver", self.driver_resource_manager)]

    def test(self):
        pass
