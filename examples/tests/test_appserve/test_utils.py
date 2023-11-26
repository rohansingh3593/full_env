from itron.meter.Gen5Meter import ParallelMeter
import itron.meter.AsMan as AsMan
import pytest
from .utils import verify_appserve
import os

@pytest.mark.full_meter
@pytest.mark.need_di_package
@pytest.mark.parametrize("check_hash", [False, True])
def test_verify_appserve(workdir,meter, logger, check_hash):
    """ if appserve is installed, verify that it is.
        if it is not installed, check for cleanup """
    logger.info("%s using meter %s", __name__, meter)

    with ParallelMeter(meter,logger) as m:

        logger.trace("Getting version info")
        fwver, asver = m.version_info()
        logger.info("FW version: %s", fwver)
        logger.info("DI Version: %s", asver)

        # note, you could hard-code the table entries like this:
        sql_tables_no_as = ["BINARYSTORE", "Blurt", "BlurtBackup", "CATEGORY_TO_EVENTS", "CATEGORY_TO_LIDS", "CONFIGURATIONXML",
         "CONTAINEROVERLAY", "CONTAINERSETUP", "CONTAINERSTATUS", "CORRECTOWNERSHIP", "ComponentGroups", "Configuration",
          "ConfigurationPackage", "CpcRecord", "DATABASEMAINTENANCE", "DBusToEvent", "DEVICE_TO_LIDS", "DISPLAYLINES",
          "DISPLAYSCREENS", "DLMSACCESSRIGHTS", "DLMSCLIENTS", "DLMSOBJECTSTRUCTURE", "DLMSSERVERS", "DSTPERIODS",
          "DemandCapture", "DemandCoincident", "DemandCoincidentConfig", "DemandConfiguration", "DemandCumulative",
          "DemandPeaks", "DemandPrevious", "DemandReset", "DemandSetConfiguration", "DemandSetEventTime", "DisconnectTable",
          "DlmsConnections", "DlmsCosemGen5Roles", "DlmsFrameCounter", "DlmsSecuritySetup", "DlmsServerAssociations",
          "DynamicConfiguration", "ENERGYCONFIGURATION", "ENERGYDATA", "ENERGYHISTORY", "ERRORS", "EVENT_ACTION_STATS",
          "EVENT_CATEGORIES", "EVENT_STATS", "EventAction", "EventDescription", "EventLogID", "EventSpecification",
          "FWINFORMATION", "GENERICLOOKUP", "GPRFILES", "HANACL", "HDLCCONNECTIONS", "HwControlTable", "IMAGETRANSFERBLOCK",
          "IMAGETRANSFERSTATUS", "ImageActivateInfo", "ImageProcessStatus", "ImageProcessTask", "LIDS", "LID_BEHAVIOR_TYPES",
          "LID_CATEGORIES", "LOG_EVENTRECORDS", "LOG_EVENTRULES", "LOG_EVENTRULES_TO_LISTENERSET_ASSOCIATIONS", "LOG_LISTENERSETS",
          "LOG_LISTENERS_TO_LISTENERSETS_ASSOCATIONS", "LidBehavior", "MESSAGESTORE", "OBIS", "OBISCLASSATTRIBUTES", "OBJECTSTORE",
          "OVERLAYCONFIGURATION", "OVERLAYSETUP", "PLUGINS", "PLUGINS_CATEGORY_TO_NAME", "PLUGINS_INTERFACE_TO_CATEGORY",
          "PREINSTALLPACKAGELIST", "PREINSTALLPACKAGEPREREQ", "PROFILEHISTORY", "ProfileFlag", "ProfileInterval",
          "ProfileIntervalMain", "ProfileSetSpec", "PulseWeightTable", "RAMTABLENAMES", "RESETREASONS", "REVERTREASONS",
          "ReactorPriorityTable", "ReactorSetTable", "SELFREADBILLINGDATA", "SELFREADQUANTITYCONFIGURATION", "SELFREADRECORDS",
          "STATISTICS", "STATISTICS2", "SelfReadHistory", "SelfReadSchedule", "TABLESTOUCHES", "TESTHISTORY", "TamperTable",
          "TimeProfile", "TouDayProfileTable", "TouEnergyTable", "TouRateLookupTable", "TouSeasonProfileTable", "TouSingleValuesTable",
          "TouSpecialDaysTable", "TouWeekProfileTable", "VersionHistory"]

        sql_tables_with_as = ["AgentData", "AgentEvents", "AgentFeatureDataCounter", "AgentInformation", "AgentMailbox",
        "AgentPolicy", "AgentRegistration", "BINARYSTORE", "Blurt", "BlurtBackup", "CATEGORY_TO_EVENTS", "CATEGORY_TO_LIDS",
        "CONFIGURATIONXML", "CONTAINEROVERLAY", "CONTAINERSETUP", "CONTAINERSTATUS", "CORRECTOWNERSHIP", "ComponentGroups",
        "Configuration", "ConfigurationPackage", "CpcRecord", "DATABASEMAINTENANCE", "DBusToEvent", "DEVICE_TO_LIDS",
        "DIDevice", "DIP2PGroupDbTable", "DIP2PKeyManagementDbTable", "DIP2PKeyValidationCounterDbTable",
        "DIP2PPublishedDataDbTable", "DIP2PReceivedNetworkMessagesDbTable", "DIP2PSentNetworkMessagesDbTable",
        "DIP2PStatSummaryTotalDbTable", "DIP2PStatsDatainCBORPerDay", "DIP2PSubscribedDataDbTable", "DIP2PSubscriptionDbTable",
        "DIPolicyFile", "DISPLAYLINES", "DISPLAYSCREENS", "DLMSACCESSRIGHTS", "DLMSCLIENTS", "DLMSOBJECTSTRUCTURE",
        "DLMSSERVERS", "DSTPERIODS", "DemandCapture", "DemandCoincident", "DemandCoincidentConfig", "DemandConfiguration",
        "DemandCumulative", "DemandPeaks", "DemandPrevious", "DemandReset", "DemandSetConfiguration", "DemandSetEventTime",
        "DeviceArchive", "DeviceArchiveEntry", "DisconnectTable", "DlmsConnections", "DlmsCosemGen5Roles", "DlmsFrameCounter",
        "DlmsSecuritySetup", "DlmsServerAssociations", "DynamicConfiguration", "ENERGYCONFIGURATION", "ENERGYDATA",
        "ENERGYHISTORY", "ERRORS", "EVENT_ACTION_STATS", "EVENT_CATEGORIES", "EVENT_STATS", "EventAction", "EventDescription",
        "EventLogID", "EventSpecification", "FWINFORMATION", "FeatureConfiguration", "GENERICLOOKUP", "GPRFILES", "HANACL",
        "HDLCCONNECTIONS", "HwControlTable", "IMAGETRANSFERBLOCK", "IMAGETRANSFERSTATUS", "ImageActivateInfo", "ImageProcessStatus",
        "ImageProcessTask", "LIDS", "LID_BEHAVIOR_TYPES", "LID_CATEGORIES", "LOG_EVENTRECORDS", "LOG_EVENTRULES",
        "LOG_EVENTRULES_TO_LISTENERSET_ASSOCIATIONS", "LOG_LISTENERSETS", "LOG_LISTENERS_TO_LISTENERSETS_ASSOCATIONS",
        "LidBehavior", "MESSAGESTORE", "OBIS", "OBISCLASSATTRIBUTES", "OBJECTSTORE", "OVERLAYCONFIGURATION", "OVERLAYSETUP",
        "PLUGINS", "PLUGINS_CATEGORY_TO_NAME", "PLUGINS_INTERFACE_TO_CATEGORY", "PREINSTALLPACKAGELIST", "PREINSTALLPACKAGEPREREQ",
        "PROFILEHISTORY", "PolicyViolationStatistics", "ProfileFlag", "ProfileInterval", "ProfileIntervalMain", "ProfileSetSpec",
        "PulseWeightTable", "RAMTABLENAMES", "RESETREASONS", "REVERTREASONS", "ReactorPriorityTable", "ReactorSetTable",
        "SELFREADBILLINGDATA", "SELFREADQUANTITYCONFIGURATION", "SELFREADRECORDS", "STATISTICS", "STATISTICS2", "SelfReadHistory",
        "SelfReadSchedule", "TABLESTOUCHES", "TESTHISTORY", "TamperTable", "TimeProfile", "TouDayProfileTable", "TouEnergyTable",
        "TouRateLookupTable", "TouSeasonProfileTable", "TouSingleValuesTable", "TouSpecialDaysTable", "TouWeekProfileTable",
        "VersionHistory"]

        # run the test once to get the expected tables, returns a database table list and other information
        # about the current appserve install
        logger.trace("Verify appserve is %s", asver)
        info = verify_appserve(logger, m, workdir, asver, expected_new_tables=[], no_hash = not check_hash)

        # now pass all the table entries from previous step and artificially make a new entry
        # by removing 3 items from the list (basically this tests the diff functionality)
        no_as = info['sql_tables_with_as']
        new_entrys = no_as[:3]
        no_as = no_as[3:]

        logger.info("Info from verify: %s", info)

        logger.trace("Verify appserve again with diffs")
        info = verify_appserve(logger, m, workdir, asver, no_hash=True,sql_tables_no_as=no_as, expected_new_tables=new_entrys)

        logger.trace("Test successful")
