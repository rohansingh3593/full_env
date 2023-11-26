import pytest
import logging
from datetime import timedelta
import os
from itron.plugins.parse_args import parse_meter_options,add_meter_options
from itron.plugins.metersched import MeterScheduler

"""
TODO: implement logger output to master
import rpyc
from rpyc.utils.server import ThreadedServer
class LoggerRPC(rpyc.Service):

    lock_server = rpyc.connect(host='localhost', port=server_port)
"""


LOGGER = logging.getLogger(__name__)
__version__ = '0.9.9'



def pytest_addoption(parser):
    add_meter_options(parser)

def add_markers(config):
    config.addinivalue_line(
        "markers", "xdist_affinity: affinity to run the test on, typically a meter or meter group"
    )

@pytest.hookimpl(tryfirst=True)
def pytest_cmdline_main(config):
    if getattr(config, "workerinput", None):
        config.pluginmanager.register(XDistWorkerPlugin(config.workerinput), 'xdistworkermeters')
        add_markers(config)
    else:
        options = parse_meter_options(config, LOGGER)
        # only activate xdist meter stuff if in auto and have db spec
        if options['meters'] or options['multi_meters']:
            add_markers(config)
            config.pluginmanager.numprocesses = "auto"
            config.pluginmanager.register(XDistMasterMetersPlugin(options), 'xdistmeters')

class XDistMasterMetersPlugin:
    def __init__(self, options):
        self.options = options
        self.meter_scheduler = None
        self.lock_timeout=timedelta(hours=5).total_seconds()
        if 'lock_timeout' in options:
            self.lock_timeout = options['lock_timeout']


    def get_meters(self, config):
        return self.options['meters']


    @pytest.hookimpl(trylast=True)
    def pytest_sessionfinish(self, exitstatus):
        if self.meter_scheduler:
            self.meter_scheduler.close()
            self.meter_scheduler=None

    @pytest.hookimpl
    def pytest_xdist_idle_poll(config, session, scheduler, nodemanager):
        if getattr(scheduler, 'poll'):
            scheduler.poll(config, session, nodemanager)


    def get_multi_meters(self, config):
        return self.options['multi_meters']

    @pytest.hookimpl
    def pytest_xdist_auto_num_workers(self, config):
        # we need one worker, as it needs to enumerate the tests.
        # It's affinity will be undefined or none
        return 1

    @pytest.hookimpl(tryfirst=True)
    def pytest_xdist_make_scheduler(self, config, log):
        m = self.get_meters(config)
        mul = self.get_multi_meters(config)
        max_meters = self.options['max_meters']
        # TODO: add meter scheduler
        #return LoadGroupScheduling(config, log)
        self.meter_scheduler = MeterScheduler(config, LOGGER, self.lock_timeout,max_meters, log, m, mul)
        return self.meter_scheduler

    #@pytest.hookimpl(tryfirst=True)
    #def pytest_xdist_make_nodemanager(self, config):
    #    return MeterNodeManager(config, self.get_meters(config))

    #@pytest.hookimpl(tryfirst=True)
    #def pytest_xdist_make_sessionmanager(self, config):
    #    return MeterSessionManager(config, self.get_meters(config))


class XDistWorkerPlugin:
    def __init__(self, workerinput):
        self.workerinput = workerinput

    @pytest.hookimpl(tryfirst=True)
    def pytest_configure(self, config):

        n = getattr(config.option, 'numprocesses')
        worker = getattr(config, 'workerinput', None)
        if worker is None:
            LOGGER.warning("Configure: Master Controller")
        else:
            LOGGER.warning("Configure: Slave Node %s", worker['workerid'])
            meter = os.getenv("PYTEST_XDIST_METER_TARGET", None)
            LOGGER.warning("Meter: %s", meter)

            worker_id = os.environ.get("PYTEST_XDIST_WORKER")
            if worker_id is not None:

                if meter: worker_log_file = f"tests_{worker_id}-{meter}.log"
                else: worker_log_file = f"tests_{worker_id}.log"

                #log_file = config.getini("log_file")
                #if log_file:
                logging.getLogger().addHandler(logging.FileHandler(worker_log_file))



    @pytest.hookimpl
    def pytest_collection_modifyitems(session, config, items):
        """ classify each test by affinity

            current affinities are: single_meter and multi_meter

            in the future, there could also be affinity for types of meters (test only runs on a specific meter, like PLC)
        """

        from _pytest.mark.structures import MARK_GEN

        # automatically mark tests based on their fixture usage
        auto_marks = {
            'single_meter': ['meter', 'preinstalled_meter','meter_db'],
            'multi_meter' : ['multi_meter','preinstalled_multi_meter']
        }
        for item in items:
            for fixture in item.fixturenames:
                for key,value in auto_marks.items():
                    if fixture in value:
                        mark = MARK_GEN.xdist_affinity(name=key)
                        item.add_marker(mark)

        for item in items:
            mark = item.get_closest_marker("xdist_group")
            if mark:
                gname = (
                    mark.args[0]
                    if len(mark.args) > 0
                    else mark.kwargs.get("name", "default")
                )
                item._nodeid = f"{item.nodeid}&group={gname}"
            mark = item.get_closest_marker("xdist_affinity")
            if mark:
                aname = (
                    mark.args[0]
                    if len(mark.args) > 0
                    else mark.kwargs.get("name", "default")
                )
                item._nodeid = f"{item.nodeid}&affinity={aname}"
