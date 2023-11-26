import os
import re
import tempfile
import requests
import zipfile
import tarfile
import time
import logging
from . import Walker
import json

logger = logging.getLogger(__name__)

_build_internal = None
def get_build_path():
    global _build_internal
    if _build_internal is None:
        _build_internal = Walker.glob("/mnt/ral-rdgbuild-03/GEN5_RIVA_SR_10*/Latest")
    return _build_internal

def get_version_data():
    """ this file is created by running:
        pytest pytest-regress-venv/src/rohan.meter.pkg/rohan/meter/test_fwman.py::test_make_fw_dict

        That testcase will scan the OWI_BUILDS web site for new builds

        This increases the speed of firmware lookups when installing, as we can translate
        the version to the location instantly
    """
    if os.path.exists("version_data.json"):
        logger.info(f"Using version_data.json")
        with open("version_data.json", 'r') as f:
            index = json.load(f)
            return index
    else:
        return []

version_tree = get_version_data()



def decrypt(encrypted_file, target, logger=logging):
    # curl -F "fwdl=@DI-AppServices-Package-1.5.270.0.tar.gz" http://ral-rdpwsgi-01.rohan.com/fwdl-decrypter --output decrypted.tar.gz
    headers = {'User-Agent': 'Mozilla/5.0'}
    link    = 'http://ral-rdpwsgi-01.rohan.com/fwdl-decrypter'
    with requests.Session() as session:
        with open(encrypted_file, 'rb') as xmit_file:
            files = {'fwdl': xmit_file}
            retry = True
            end_time = time.time() + 10*60
            while time.time() < end_time:
                try:
                    logger.debug("Request")
                    with session.post(link,headers=headers, files=files, stream=True, timeout=(400,400)) as resp:
                        with open(target, "wb") as f:
                            logger.debug("writing chunk")
                            for chunk in resp.iter_content(chunk_size=1024):
                                f.write(chunk)
                    retry = False
                    break
                except requests.exceptions.ConnectionError:
                    logger.exception("Connection error with %s", link)
                    time.sleep(60)
                    continue
            if retry:
                logger.error("timeout decrypting package")
                raise TimeoutError("timeout decrypting package")
    return target

def _find_file(path, str):
    for root, dirs, files in Walker.walk(path):
        for f in files:
            if f.find(str) != -1:
                return f
    return None

def get_preinstall(fw_package, as_dir=None):
    """! get the  PreInstall packages from the fw_package

    @param fw_package   file name of signed package
    @param as_dir       name of directory to extract the packages to

    @return name of AS package
     """

    with tempfile.TemporaryDirectory() as TMP:
        if not fw_package.startswith("http://"):
            assert os.path.exists(fw_package), "Fw package does not exist"
        else:
            r = requests.get(fw_package, stream=True)
            r.raise_for_status()
            zipfile_name = os.path.join(TMP, os.path.basename(fw_package))
            with open(zipfile_name, 'wb') as file:
                for chunk in r.iter_content(chunk_size=8192):
                    file.write(chunk)
            fw_package = zipfile_name

        asdir = os.path.join(TMP, "AppServ")
        os.mkdir(asdir)
        with zipfile.ZipFile(fw_package) as z:
            z.extractall(asdir)

        pkg_name = _find_file(asdir, "tar.gz")
        INTERNAL=os.path.join(asdir, pkg_name)
        decrypted = os.path.join(asdir, "decrypted.tar.gz")
        SRC=decrypted
        if not os.path.exists(decrypted):
            decrypt(INTERNAL, decrypted)

        FILENAME=os.path.basename(fw_package)[7:]

        rfs = os.path.join(TMP, "rootfs")
        l1 = os.path.join(TMP, "layer1")
        l2 = os.path.join(TMP, "layer2")
        os.mkdir(rfs)
        os.mkdir(l1)
        os.mkdir(l2)
        with tarfile.open(name=SRC,mode="r:gz") as t:
            t.extractall(l1)

        # Find .xz file and extract it to layer2

        iner = _find_file(l1, ".xz")
        mode='r:xz'
        if not iner:
            iner = _find_file(l1, ".gz")
            mode='r:gz'

        INNER=os.path.join(l1, iner)

        with tarfile.open(name=INNER,mode=mode) as t:
            t.extractall(l2)

        # now we should have the rootfs.tar.gz in layer2, so extract that
        ROOTFS=os.path.join(l2, "rootfs.tar.gz")
        with tarfile.open(name=ROOTFS,mode='r:gz') as t:
            items = t.getmembers()

        didir = 'usr/share/rohan/PreInstall'
        cur_pack = []
        for i in items:
            # if this is a preinstall file, fetch it
            if re.search(didir, i.path):
                cur_pack.append(i.path)
                if as_dir:
                    t.extract(i, as_dir)
        return cur_pack

def _get_diff_ver(version,dirs,path,downgrade=False):

    selected = getSelected(version,dirs)

    diffpath = os.path.join(path,selected,'Distribution-Files/Gen5RivaMeter-Dev/UpgradeFromPriorBuild')
    diffs = Walker.listdir(diffpath)
    r = re.compile(".*zip")
    newlist = list(filter(r.match, diffs)) # Read Note below
    if len(newlist) > 1:
        logger.info('Found more than one diff upgrade file.  Not sure which one to use, in %s', coldpath)
        exit(1)
    elif len(newlist) < 1:
        logger.info('Did not find diff upgrade zip file in %s', coldpath)
        exit(1)
    else:
        diffile = newlist[0]

    # format: 'signed-Test_FWDL_d_SecureBoot_FW10.5.429.2-10.5.429.3.AsicTestSecure_Gen5RivaAsic.GEN5RIVA_REL_3_1_ALPHA3_Dev.tar.gz_2022_04_14T20_20_32_04_00_ps.zip'
    vers = re.sub('signed-.*SecureBoot_FW','', diffile)
    vers = re.sub('.AsicTestSecure.*zip', '', vers)
    vers = vers.split('-')
    logger.info(f"From version {vers[0]} to {vers[1]}")

    # now get the "from version" coldstart file

    coldpath, coldfile = _get_version(path, vers[0], dirs, downgrade=downgrade)

    logger.info(f'Diff upgrade set to: {diffile}')
    return coldpath, coldfile, diffpath, diffile


def getSelected(version, dirs):
    if version == 'latest':
        selected = dirs.pop()
    else:
        r = re.compile(".*" + version +  ".*")
        selected = list(filter(r.match, dirs)) # Read Note below
        if len(selected) > 1:
            selexted = selected[0]
            #selected = inquirer.list_input("Coldboot build: ", choices=selected);
        elif len(selected) < 1:
            raise FileNotFoundError
        else:
            selected = selected[0]
    return selected

def _find_zip(path):
    dirs = Walker.listdir(path)
    r = re.compile(".*zip")
    newlist = list(filter(r.match, dirs)) # Read Note below
    coldfile = None
    if len(newlist) > 1:
        logger.info(newlist)
        logger.error('Found more than one coldboot file.  Not sure which one to use, in', path)
        raise ValueError('Too many matches.')
    elif len(newlist) < 1:
        logger.error('Did not find coldboot zip file in', path)
        raise FileNotFoundError
    else:
        coldfile = newlist[0]
    return coldfile

def _get_version(path, version, dirs, downgrade=False):

    selected = getSelected(version, dirs)
    logger.info("Coldboot set to : %s", selected)

    if downgrade:
        coldpath = os.path.join(path,selected,'Distribution-Files/Gen5RivaMeter-Dev/DowngradePackage/')
    else:
        coldpath = os.path.join(path,selected,'Distribution-Files/Gen5RivaMeter-Dev/ColdStartPackage/')
    coldfile = _find_zip(coldpath)
    logger.info(f"Coldboot file: %s", coldfile)

    return coldpath, coldfile

def _find_build(single_path, file=None, version=None,diff_upgrade=False, downgrade=False):
    dirs = Walker.listdir(single_path)
    dirs.sort()

    diff_file = None
    diff_path = None
    if diff_upgrade:
        coldpath, coldfile, diff_path,diff_file = _get_diff_ver(version, dirs, single_path, downgrade=downgrade)
    else:
        coldpath, coldfile = _get_version(single_path, version, dirs, downgrade=downgrade)

    return coldpath, coldfile, diff_path, diff_file

def compare_versions(oldv, newv):
    """ returns 0 if same
        -1 if older
        1 if newer """
    ov = oldv.split('.')[:2]
    nv = newv.split('.')[:2]
    if ov[0] == nv[0]:
        if ov[1] == nv[1]:
            return 0
        if ov[1] > nv[1]:
            return -1
        return 1
    else:
        if ov[0] > nv[0]:
            return -1
        return 1

def pkg_to_ver(file):
    version = re.search("FW(10[0123456789.]+)",os.path.basename(file))
    ver = version[1] if not version[0].endswith('.') else version[1][:-1]
    return ver

def get_build_ex(version):
    coldpath, coldfile, _, _ = get_build(version=version)

    distro = os.path.dirname(coldpath) if not coldpath.endswith('/') else os.path.dirname(coldpath[:-1])
    items = Walker.listdir(distro)
    avail_packages = ["ColdStartPackage/","DowngradePackage/","UpgradeFromSR_10-2/",
        "UpgradeFromSR_10-3/",
        "UpgradeFromSR_10-4/",
        "UpgradeWithinSR_10-5/",
        "FutureDiff1/",                       
        "FutureDiff2/",                       
        "FutureDiff3/",                       
    ]
    info = {
        "base_path": distro,
        "version": pkg_to_ver(coldfile)
    }

    for item in avail_packages:
        if item in items or item[:-1] in items:
            name = _find_zip(os.path.join(distro, item)+'/')
            info[item[:-1]] = os.path.join(distro, item, name)

    return info

def get_build(file=None, version=None,diff_upgrade=False, downgrade=False):

    if file:
        coldpath = os.path.dirname(file)
        coldfile = os.path.basename(file)
        found = True
    else:
        if version in version_tree:
            logger.info("Build in version_data.json")
            coldpath = os.path.dirname(version_tree[version]['coldstart'])
            coldfile = os.path.basename(version_tree[version]['coldstart'])
            return coldpath, coldfile, None, None

        found = False
        twover = version.split('.')
        twover = twover[0] + '-' + twover[1]
        build_path = get_build_path()
        if type(build_path) is list:
            for path in build_path:
                try:
                    if twover in path:
                        logger.info(f"Trying directory {path}")
                        coldpath, coldfile, diff_path, diff_file = _find_build(path,file=file,version=version,diff_upgrade=diff_upgrade, downgrade=downgrade)
                        found = True
                        break
                except FileNotFoundError:
                    pass
        else:
            coldpath, coldfile, diff_path, diff_file = _find_build(build_path, file=file, version=version, diff_upgrade=diff_upgrade, downgrade=downgrade)

    if not found :
        logger.info("Build not found")
        assert(False)
    return coldpath, coldfile, diff_path, diff_file
