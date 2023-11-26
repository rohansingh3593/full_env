#!/usr/bin/env python3
# content of conftest.py

import pytest
import os
import git
import logging
import glob
import re
import sys
from datetime import date, datetime

LOGGER = logging.getLogger(__name__)

#pytest_plugins = "rohan.plugins.parallel"
# start time of session
session_start = datetime.now()

LOG_TRACE=30
logging.addLevelName(LOG_TRACE, "TRACE")
def log_trace(self, message, *args, **kws):
    if self.isEnabledFor(LOG_TRACE):
        self._log(LOG_TRACE, message, args, **kws)
logging.Logger.trace = log_trace

@pytest.fixture(autouse=True,scope='function')
def logger(logname, request):
    log = logging.getLogger(request.node.nodeid)
    fileh = logging.FileHandler(logname)
    fileh.setFormatter(logging.Formatter('%(asctime)s %(levelname)-8s (%(name)s:%(filename)s:%(lineno)s) %(message)s'))
    log.addHandler(fileh)
    return log

@pytest.fixture(scope='function')
def testname(request):
    return request.node.nodeid.replace('/','.')

@pytest.fixture(autouse=True,scope='function')
def logname(workdir):
    return os.path.join(workdir,f"results.log")

@pytest.fixture(scope='session')
def session_date():
    strdate = session_start.strftime("%d-%m-%Y_%H_%M_%S")
    return strdate

@pytest.fixture(scope='function')
def workdir(request, testname,session_date):
    pwd = os.getcwd()
    print("cwd fixture: ", pwd)
    dir = os.path.join(request.fspath.dirname, f"session-{session_date}/workdir-{testname}")
    LOGGER.info("Workdir: %s", dir)
    os.makedirs(dir,exist_ok = True)
    return dir

def get_gittop():
    repo = git.Repo('.', search_parent_directories=True)
    GITTOP=repo.working_tree_dir
    LOGGER.info("GitTOP: %s",GITTOP)
    return GITTOP

@pytest.fixture(scope='session')
def gittop():
    return get_gittop()

@pytest.fixture(scope='session')
def di_version(di_package):
    """os.mktemp()
    with tempfile.TemporaryDirectory() as tmpdirname:
        shutil.unpack_archive(di_package, tmpdirname)
        subprocess.check_call("tar xf ")
    """
    # DI-AppServices-Package-1.7.137.11_TS.zip
    m=re.search("Package-([0123456789.]+)_", di_package)
    return m.group(1)


@pytest.fixture(scope='session')
def di_package(gittop):
    dir = os.path.join(gittop, 'artifacts/bionic-x86_64/TargetDebug/DI-AppServices-Package-*.zip')
    files = glob.glob(dir)
    print(files)
    assert len(files) == 1
    package=files[0]
    LOGGER.info("DI Package: %s", package)
    return package

# just before the call, setup the logger path to "logname" fixture above
"""
@pytest.hookimpl
def pytest_runtest_call(item):
    logging_plugin = item.config.pluginmanager.get_plugin("logging-plugin")
    logging_plugin.set_log_path(item.funcargs['logname'])
"""
