""" utility functions used by multiple tests """
import sqlite3
import logging
import os
import json
import re
from itron.meter.AbstractMeter import AbstractMeter


LOGGER = logging.getLogger(__name__)

FW_1_7_PLUS_DIR = "/usr/share/itron/DI-AppServices-Package"
FW_PRE_1_7_DIR = "/mnt/common/DI-AppServices-Package"
deleted_list = [ '/usr/share/itron/improv/Diff.Install/AppServ',
                '/usr/share/itron/DI-AppServicesPackage.New',
                '/mnt/common/DI-AppServicesPackage.New',
                #'/mnt/common/D55-populateFWInformationForAppServices.sql' 
                ]

default_new_tables = ["AgentData", "AgentEvents", "AgentFeatureDataCounter", "AgentInformation",
    "AgentMailbox", "AgentPolicy", "AgentRegistration", "DIDevice", "DIP2PGroupDbTable",
    "DIP2PKeyManagementDbTable", "DIP2PKeyValidationCounterDbTable", "DIP2PPublishedDataDbTable",
    "DIP2PReceivedNetworkMessagesDbTable", "DIP2PSentNetworkMessagesDbTable", "DIP2PStatSummaryTotalDbTable",
    "DIP2PStatsDatainCBORPerDay", "DIP2PSubscribedDataDbTable", "DIP2PSubscriptionDbTable", "DIPolicyFile",
    "DeviceArchive", "DeviceArchiveEntry", "FeatureConfiguration", "PolicyViolationStatistics"]

def numbered_file(workdir, file_name):
    name, ext = os.path.splitext(file_name)
    path = os.path.join(workdir, name + ext)
    i = 1
    while os.path.exists(path):
        path = os.path.join(workdir, f"name-{i}{ext}")
        i += 1
    return path


def verify_appserve(logger, meter: AbstractMeter, workdir,  expected_version,no_hash=False,sql_tables_no_as=None, expected_new_tables=None):
    """! Verify that the meter is working and that AppServe is running and active 
    
    This function will perform checks to validate that the installation was successful.  It will check
    version information, hash information, sql tables, etc that the installer normally creates.

    @param   meter                meter object passed in from framwork (ssh connection)
    @param   expected_version     the AS package that should be installed on the meter
    @param   expected_new_tables  a python list of table names that were added (not currently
                                  on the meter, but will be created when install is complete)
    @param   sql_tables_no_as     a python list of sql table names to ignore (part of FW)

    @return  Returns a dictionary with information collected during validation

    """
    failed = False
    info={}
    if expected_new_tables is None:
        expected_new_tables = default_new_tables

    meter.capture_logs(workdir)
    info['sql_tables_with_as'] = meter.sql_query( '.tables', json_file=numbered_file(workdir, "sql_tables_with_as.json"))

    if sql_tables_no_as:
        new_entries, deleted_entries = diff_and_save(numbered_file(workdir, "new_tables.json"),
            sql_tables_no_as,
            info['sql_tables_with_as'])
        info['new_entries'] = new_entries
        info['deleted_entries'] = deleted_entries

        if set(new_entries) != set(expected_new_tables):
            logging.warning("The expected table list does not match the new tables")
            missing = list(set(expected_new_tables) - set(new_entries))
            extra = list(set(new_entries) - set(expected_new_tables))
            if missing:
                logging.error("Missing tables: %s", missing)
            if extra:
                logging.warning("Extra tables: %s", extra)
            failed = True

    info['database'] = database = meter.download_db(workdir)
    # TODO: Verify database contents contain expected values

    con = sqlite3.connect(database)
    cursor = con.cursor()
    res = cursor.execute("select id,path,version,unsignedhash,enabled  from fwinformation")

    for x in res:
        if x[1] == '/usr/share/itron/sha/appservicesLR.manifest':
            version = x[2]
            _id = x[0]
            _hash = x[3]
            enabled = x[4]

    assert version == expected_version
    assert int(enabled) == 1

    logger.info("AppServe Id: %s Hash:%s Version:%s", _id, _hash, version)
    if not no_hash:
        failed = failed | verify_hashs(logger,meter, x[1])

    # Validate cleanup during install

    for entry in deleted_list:
        lst = meter.ls(entry)
        logging.info("ls:%s\n%s",entry, lst)

        if lst:
            logging.error("Item should be deleted from meter: %s", entry)
            failed = True

    failed = failed | verify_uninstaller(logger, meter, workdir, _hash, check_hash=not no_hash)

    failed = failed | verify_as_running(logger,meter,workdir)

    with open(numbered_file(workdir, "info.json"),"w") as f:
        json.dump(info, f)

    assert failed is False

    return info

def ASSERT(truth, logger, failed, string):
    if not truth:
        logger.error(string)
        return True
    return failed

def verify_cosem(logger, meter, workdir):
    failed=False
    meter.command("di-tool ")
    return failed

def verify_hashs(logger,meter,remote_lrfile):
    fail = False
    list = meter.command(f"cat {remote_lrfile}")
    for item in list:
        items = item.split()
        hash = items[0]
        file = items[1]
        rhash = meter.command(f"sha256sum {file}", timeout=120)
        rhash = rhash[0].split()[0]
        if hash != rhash:
            logger.error("Hash mismatch for %s %s != %s", file, hash, rhash)
            fail = True

    return fail

def verify_uninstaller(logger,meter,workdir,_hash, check_hash=True):
    """ verify that the uninstaller directory matches
        the current installed version """
    un_dir = "/usr/share/itron/DI-AppServices-Package"
    if not meter.ls(un_dir):
        un_dir = "/mnt/common/DI-AppServices-Package"

    rhash_file = os.path.join(un_dir, 'appservicesLR.manifest')
    un_hash = meter.command(f"sha256sum {rhash_file}")
    logger.info(un_hash)
    failed = ASSERT(len(un_hash) == 1, logger, False,
        "hash not generated, File not found??")
    failed = ASSERT(un_hash[0].split()[0] == _hash, logger, failed,
        "hashes for leagally relevant didn't match hash from uninstall dir")
    if check_hash:
        failed = failed or verify_hashs(logger, meter, rhash_file)
    return failed

def verify_no_di(logger,meter,workdir):
    """ check to make sure app services is not installed """

    assert meter.ls(FW_1_7_PLUS_DIR) is None

def clean_as_and_gmr(logger, meter: AbstractMeter, workdir):
    """ remove as from the system on falure.  Will cause GMR """
    logger.info("gmr and clean AppServe")

    meter.gmr()

    # cleanup uninstaller entries
    meter.command(f"rm -rf {FW_1_7_PLUS_DIR}")
    meter.command(f"rm -rf {FW_1_7_PLUS_DIR}.New")
    meter.command(f"rm -rf {FW_PRE_1_7_DIR}")
    meter.command(f"rm -rf {FW_PRE_1_7_DIR}.New")

    # cleanup db entries
    meter.command("rm -rf /usr/share/itron/DI*")

    for entry in deleted_list:
        meter.command(f"rm -fr {entry}")


def verify_as_running(logger,meter,workdir):
    """ make sure app services is running and responding to DBUS messages
    """
    stat = meter.command("ps")
    r = re.compile(".*DataServer.*")
    di = list(filter(r.match, stat)) # Read Note below
    if not di:
        logging.error("DataServer is not running")
        return True
    else:
        logging.info("Dataserver running: %s", di)
    return False

def install_all_from_preinstall(logger,meter):
    files = meter.ls("/usr/share/itron/PreInstall")
    for file in files:
        logger.info("found preinstall file, installing: %s", file)
        meter.install(file=file, remote_file=True)

def install_han_from_preinstall(logger,meter: AbstractMeter):
    """ install the HAN agent """
    files = meter.ls("/usr/share/itron/PreInstall")
    if files and len(files) == 1:
        logger.info("HAN agent found,  installing")
        meter.install(file=files[0], remote_file=True)
    else:
        logger.info("HAN agent not in preinstall")

def diff_tables(sql_tables_no_as, sql_tables_with_as):
    unique = list(set(sql_tables_with_as) ^ set(sql_tables_no_as ))
    deleted = list(set(sql_tables_no_as ) & set(unique))
    return unique, deleted

def diff_and_save(file, a, b):
    new_tables, deleted = diff_tables(a,b)
    new_tables.sort()
    with open(file,"w") as f:
        json.dump(new_tables, f)
    return new_tables,deleted

def install_build(logger, workdir, m, di_package, di_version, expected_new_tables=None, sql_tables_no_as=None):
    """! install or upgrade DI package and verify correct installation 
    
    This routine will install a new version of AS on the meter, and verify that
    the install correctly updated the expected files

    @param   di_package           the AS package that we are going to install
    @param   di_version           the version number of di_package, (ex. "1.7.341.2")
    @param   expected_new_tables  a python list of table names that were added (not currently
                                  on the meter, but will be created when install is complete)
    @param   sql_tables_no_as     a python list of sql table names to ignore (part of FW)

    @return  Returns a list of table entries that were created by the install action
    """

    if not sql_tables_no_as:
        logger.info("DI Package: %s", di_package)
        fwver, asver = m.version_info()
        assert asver is None, "AS is installed so we can't get a list of non AS tables, this must be passed in"
        sql_tables_no_as = m.sql_query( '.tables', json_file=numbered_file(workdir, "sql_tables_no_as.json"))

    # now install AppServe
    code = m.install(file=di_package)
    #m.capture_logs()
    assert code == 0
    fwver, asver = m.version_info()
    assert asver == di_version
    sql_tables_with_as = m.sql_query( '.tables', json_file=numbered_file(workdir,f"sql_tables_with_{di_version}.json"))
    verify_appserve(logger, m, workdir, asver, sql_tables_no_as=sql_tables_no_as, expected_new_tables=expected_new_tables)

    new_tables,deleted = diff_and_save(numbered_file(workdir, "new_tables.json"),
        sql_tables_no_as,
        sql_tables_with_as)

    assert not deleted, "there should be no deleted tables"

    return new_tables

