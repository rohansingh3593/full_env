import pytest
import os

from rohan.meter import FwMan
from rohan.meter.FwMan import get_build_path
import re
import json
import glob
import csv
import logging
from . import Walker


logger = logging.getLogger(__name__)
def test_fwdir():
    logger.info(f"getting build")
    coldpath,coldfile,_,_ = FwMan.get_build(version='10.5.633')
    fw_image = os.path.join(coldpath, coldfile)
    logger.info(f"getting preinstall")
    files = FwMan.get_preinstall(fw_image)
    data = {}
    logger.info(f"finding files")
    for file in files:
        if re.search('DI-AppServices', file):
            as_ver = re.search("Package-([0123456789.]+)",file)[1]
            if as_ver.endswith('.'):
                as_ver = as_ver[:-1]
            data['as_ver'] = as_ver

        if re.search('HANAgent', file):
            data['han_ver'] = re.search("HANAgent_([0123456789.]+)",file)[1]
    assert data['as_ver'] == "1.7.327.0"

#@pytest.mark.not_normal
def test_make_fw_dict():
    index = {}
    if os.path.exists("version_data.json"):
        logger.info(f"Using version_data.json")
        with open("version_data.json", 'r') as f:
            index = json.load(f)
    rfs = 'Distribution-Files/Gen5RivaMeter-Dev/images-signed/rfs/rootfs.tar.gz'
    coldstart_dir = 'Distribution-Files/Gen5RivaMeter-Dev/ColdStartPackage'
    my_build_path = get_build_path().copy()
    #my_build_path.sort(reverse=True)
    #my_build_path = []
    #my_build_path.extend(glob.glob("/mnt/ral-rdgbuild-03/GEN5_RIVA_SR_10-5_REL_3_1/Latest"))
    #my_build_path.extend(glob.glob("/mnt/ral-rdgbuild-03/GEN5_RIVA_SR_10-5_REL_3_1_EARLY/Latest"))
    #my_build_path.extend(glob.glob("/mnt/ral-rdgbuild-03/GEN5_RIVA_SR_10-8_REL_3_1_1/Latest"))
    #my_build_path.extend(glob.glob("/mnt/ral-rdgbuild-03/GEN5_RIVA_SR_10-8_REL_3_1_1_ALPHA1/Latest"))

    for path in my_build_path:
        try:
            logger.info(f"Trying directory {path}")
            dirs = Walker.listdir(path)
            dirs.sort(reverse=True)

            for build in dirs:
                if not build.startswith(path):
                    cwd = os.path.join(path, build)
                else:
                    cwd = build
                coldpath = os.path.join(cwd, coldstart_dir)
                cold_dir = Walker.listdir(coldpath)
                if cold_dir:
                    r = re.compile(".*zip")
                    newlist = list(filter(r.match, cold_dir)) # Read Note below
                    if len(newlist) > 1:
                        continue
                    elif len(newlist) < 1:
                        continue
                    else:
                        coldstart_file = os.path.join(coldpath, newlist[0])

                    fw_ver = re.search("FW_(10[0123456789_]+)",build)
                    if fw_ver:
                        fw_ver = fw_ver[1]
                    else:
                        continue
                    fw_ver = fw_ver.replace('_','.')

                    if fw_ver not in index:
                        logger.info("Adding %s to index", fw_ver)
                        files = FwMan.get_preinstall(coldstart_file)
                        data = {
                            'fw_version': fw_ver,
                            'fw_path': cwd,
                            'coldstart': coldstart_file
                        }
                        for file in files:
                            if re.search('DI-AppServices', file):
                                as_ver = re.search("Package-([0123456789.]+)",file)[1]
                                if as_ver.endswith('.'):
                                    as_ver = as_ver[:-1]
                                logger.info("%s has AS version %s", fw_ver,as_ver)
                                data['as_ver'] = as_ver

                            if re.search('HANAgent', file):
                                data['han_ver'] = re.search("HANAgent_([0123456789.]+)",file)[1]

                        index[fw_ver] = data
                        with open('version_data.json',"w") as f:
                            json.dump(index, f, indent=4)
                    else:
                        logger.info("%s is already in index", fw_ver)


        except FileNotFoundError:
            logger.info(f"empty {path}")
            pass

def test_make_fw_csv():
    index = {}
    if os.path.exists("version_data.json"):
        with open("version_data.json", 'r') as f:
            index = json.load(f)

    csv_columns = ['fw_version','as_ver','han_ver','fw_path','coldstart']
    dict_data = []
    for item in index.items():
        dict_data.append(item[1])
    dict_data = sorted(dict_data, key=lambda d: d['as_ver'] if 'as_ver' in d else '', reverse=True)
    csv_file = "version_data.csv"
    try:
        with open(csv_file, 'w') as csvfile:
            writer = csv.DictWriter(csvfile, fieldnames=csv_columns)
            writer.writeheader()
            for data in dict_data:
                writer.writerow(data)
    except IOError:
        print("I/O error")

def test_connection():
    from kaizenbot.kbotdbclient_psql import _KBotDBClient_psql

    resources =_KBotDBClient_psql('kaizenbot.rohan.com:5432', "kgodwin", 'AppServe', 'appservschema')
    allot = resources.allot_all_nodes()
    logger.info("Allot: %s", allot)
    nodes = resources.read_all_nodes()
    logger.info("Nodes: %s", nodes)
    resources.acquire_platform(platform='ALL',nowait=False)

if __name__ == '__main__':
    unittest.main()
