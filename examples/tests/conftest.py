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


@pytest.fixture(autouse=True,scope='function')
def logger(logname, request):
    log = logging.getLogger(request.node.nodeid)
    fileh = logging.FileHandler(logname)
    fileh.setFormatter(logging.Formatter('%(asctime)s %(levelname)-8s (%(name)s:%(filename)s:%(lineno)s) %(message)s'))
    log.addHandler(fileh)
    log.info("==== Start ======== %s ==========", request.node.nodeid)
    yield log
    log.info("==== End ======== %s ==========", request.node.nodeid)
    log.removeHandler(fileh)

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

