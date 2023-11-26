"""
Module to support SSH connection to Gen5 Itron Meter

This is a connection oriented interface.

It is important not to execute a command that causes the meter
to drop the connection.  Therefore, installation, gmr and
reboot operations are directly supported by this class.  When
operations that cause the meter to become inoperative, this module
will poll for the meter to come back and be accessible again.

"""
import os
import stat
import time
import subprocess  # For executing a shell command
from itron.meter.AbstractMeter import AbstractMeter
from itron.meter.MeterInstance import MeterInstanceBase
from . import MeterMan
from . import AsMan
import json
import logging
import tempfile
import tarfile
import shutil
import itron.meter.FwMan as FwMan
import re
from .utils import (ping, MyZipFile)
from .MeterMan import FilterMatch
from typing import List,Tuple
from requests.structures import CaseInsensitiveDict

class SSHGen5Meter(AbstractMeter):
    """ Real meter connected to SSH
        this class implements common functions for installing programs on the meter
        and executing remote commands through SSH to query/test meter
        functionality.

        Currently implements a thin wrapper around MeterMan.  MeterMan provides
        implementations to send commands to the meter to manage firmware on the
        meter.

        Usage:
            from itron.meter.Gen5Meter import SSHGen5Meter

            with SSHGen5Meter('my_meter', logger) as meter:
                # execute command on meter and return list lines of output
                output = meter.command("ls -l /mnt/common")
                for x in output:
                    print(x)
    """

    def __init__(self,meter_ip_addr,logger,*args,timeout=10*60):

        """
        @param meter_ip_addr   IP address or hostname of meter to connect to with SSH
        @param logger          logger to report stats to
        @param timeout         timeout for connecting to the meter
        """
        super().__init__(args)
        self.meter = meter_ip_addr
        self.logger = logger
        self.mm = MeterMan.MeterMan(meter_ip_addr,logger)
        self.connection = None
        self.timeout = timeout # default to 5 minutes for a timeout

    def __enter__(self):
        self.connect()
        #ttysetattr etc goes here before opening and returning the file object
        return self

    def __exit__(self, type, value, traceback):
        #Exception handling here
        self.disconnect()
        pass

    @property
    def meter_name(self):
        return self.meter

    def __repr__(self):
        return f'SSHGen5Meter({self.meter_name})'

    def disconnect(self):
        """ force disconnection - should only be used at end of object life to clean up socket """
        if self.connection:
            self.connection.disconnect()
            self.connection = None

    def expect_shell(self, timeout=2*60, display=True, **kwargs):
        """ return an expect object that can be used to send commands

        example:
        with g5m.expect_shell as exp:
            exp.send('echo "hi there"')
            stat = exp.expect('hi \\w+') # look for hi and another word


        """
        return self.connection.expect_shell(timeout=timeout, **kwargs)

    def connect(self):
        """ this forces the SSH connection to terminate (if active) and then re-login """
        self.disconnect()
        start_time = time.time()
        end_time = start_time+self.timeout
        while time.time() < end_time:
            try:
                if ping(self.logger,self.meter):
                    self.connection = self.mm.login()
                    break
                else:
                    self.logger.info("%s host not pingable - down", self.meter)
            except Exception as e: # pylint: disable=broad-except
                self.logger.info("Exception ignored: %s", e)
                logging.exception(e,exc_info=True)
                self.logger.info("")
                pass
            time.sleep(5)

        if not self.connection:
            self.logger.error("meter is not responding.  down?")
        assert self.connection is not None, "Error: meter is not responding"


    def capture_logs(self, dir):
        self.mm.capture(self.connection, dir)

    def command(self, cmd: str, codec='utf-8', splitlines=True, **kwargs)-> List[str]:
        """ Execute a command on the meter and return the results of `stdout`
            and ignore error code.

            This opens a ssh command session and executes the command
            on the meter directly.  If this is a shell command, a new bash shell
            is created for each command.  Therefore, commands like `export VAR=1`
            will execute, but will *not* create an environment variable.
            If this functionality is required, use the `SSHGen5Meter.expect_shell`
            method.

            Args:
                cmd -- Command string to send to remote system
                splitlines -- if set, stdout and stderr are returned as an array of lines
                returns
            --------
            returns stdout as list of lines like `stdout.readlines()`

            default codec='utf-8'
            """
        return self.connection.command(cmd, codec=codec, splitlines=splitlines, **kwargs)

    def command_all(self, cmd: str, codec='utf-8', splitlines=True, **kwargs) -> Tuple[int, bytearray or str, bytearray or str]:
        """ execute command on meter and return binary output or decoded string

            Args:
                cmd -- Command string to send to remote system
                codec -- If None, then return bytearray, else `stdout` and `stderr` is string.
                         this allows caller to process output unmodified
                splitlines -- if set, stdout and stderr are returned as an array of lines

            Returns:
                ( result_code, stdout, stderr )

         """
        return self.connection.command_with_all(cmd, codec=codec, splitlines=splitlines, **kwargs)

    def command_with_code(self, cmd: str, codec='utf-8', splitlines=True, **kwargs) -> Tuple[ int, List[str]]:
        """ Same as `SSHGen5Meter.command` except returns a Tuple of exit_code
            and `stdout` decoded as utf-8 (by default)
            --------
            Args:
                cmd -- Command string to send to remote system
                codec -- If None, then return bytearray, else `stdout` and `stderr` is string.
                         this allows caller to process output unmodified
                splitlines -- if set, stdout and stderr are returned as an array of lines

            returns exit_code, stdout as list of lines like `stdout.readlines()`
            """
        return self.connection.command_with_code(cmd, codec=codec, splitlines=splitlines, **kwargs)

    def gmr(self):
        """ execute GMR, then wait for meter to reconnect """
        self.connection = self.mm.gmr_from_connected(self.connection)

    def reboot_meter(self):
        """ execute /sbin/reboot, then wait for meter to reconnect """
        self.connection = self.mm.reboot_meter_connected(self.connection)

    def coldstart(self, version=None, file=None, gmr=True, downgrade=False):
        """ gmr the meter, then install the coldstart image specified """
        self.logger.info("Coldstart: %s",version)
        assert(version or file)
        assert file is None # currently dont support file, just need else below
        code = None
        if version:
            self.connection, code = self.mm.coldstart_auto(self.connection, version=version, do_gmr=gmr, debug=True)

        self.logger.debug("Coldstart returned: %s", code)
        return code

    def install(self, version=None, file=None, remote_file=False):
        """ install a package on the meter with ImProv """
        self.logger.info("Install: %s",version)
        assert(version or file)
        if version:
            args,_ = self.mm.parse_args(["mm", "install","-v", version])
            self.logger.info("Args: %s",args)
            code = self.mm.cmd_install(args)
        elif file:
            args,_ = self.mm.parse_args(["mm", "install","--remote-file" if remote_file else "--file", file])
            self.logger.info("Args: %s",args)
            code = self.mm.cmd_install(args)

        # re-login in case above command logged us out
        self.connect()
        self.logger.debug("Install returned: %s", code)
        return code

    def version_info(self):
        """ return the firmware version currently installed on the meter """
        return self.mm.getfwver(self.connection), self.mm.get_lid(self.connection, 'ILID_DATASERVER_APPSERV_FW_VERSION')

    def download_db(self, location):
        """ download the muse01.db file from the meter for local sqlite access """
        target = os.path.join(location, "muse01.db")
        self.get_file("/mnt/common/database/muse01.db",target)
        return target

    def get_file(self, src, target):
        """! Read a file from the meter to the target (local) directory

        @param src    directory on the meter (path from root dir)
        @param target local file to recieve the data from the file

        """
        return self.connection.get_file(src,target)

    def put_file(self, src, target):
        """ send file to the meter """
        return self.connection.put_file(src,target)

    def ls(self, path):
        """ get a directory listing of path """
        return self.connection.ls_list(path)

    def sql_query(self, query, json_file=None, headers=False):
        """ perform a sql query and return results as a list """
        table = self.mm.db_operation(query, self.connection, header = headers)
        table = [x.strip(' ') for x in table]
        if json_file:
            with open(json_file,"w") as f:
                json.dump(table, f)
        return table

    def sql_query_as_dict(self, query):
        """ perform a sql query and return results as a list """
        table = self.mm.get_table(self.connection, None, query)
        insensitive = []
        if table:
            for entry in table:
                insensitive.append(CaseInsensitiveDict(entry))
        return insensitive

    def get_table(self, table, json_file=None, csvmode=False):
        """ download an entire table from meter, returns a dictionary """

        if csvmode:
            table = self.mm.get_table_csv(self.connection, table)
        else:
            table = self.mm.get_table(self.connection, table)
        if json_file:
            with open(json_file,"w") as f:
                json.dump(table, f)
        insensitive = []
        if table:
            for entry in table:
                insensitive.append(CaseInsensitiveDict(entry))
        return insensitive

    def decrypt_package(self,src, destname):
        """ Decrypt a package and extract the signed .gz file """
        ENCRYPTED=os.path.join("/media/mmcblk0p1", f"encrypt{time.time()}")
        REMOTE_DECRYPTED=os.path.join("/media/mmcblk0p1", f"decrypt{time.time()}")

        try:
            logging.info("copy %s to meter for decrypt", src)
            self.put_file(src, ENCRYPTED)
            code, data = self.command_with_code(f"ImageDecrypt /proc/device-tree/exdata {ENCRYPTED} {REMOTE_DECRYPTED}")
            logging.info("ImageDecrypt: %s", data)
            if code == 0:
                self.get_file(REMOTE_DECRYPTED, destname)

        finally:
            self.command(f"rm -rf ${ENCRYPTED}, ${REMOTE_DECRYPTED}")
        return destname

    def _find_file(self, path, str):
        for root, dirs, files in os.walk(path):
            for f in files:
                if f.find(str) != -1:
                    return f
        return None

    def repack_diff_package(self, logger,  fw_package, di_package, di_scripts, workdir):
        """ Repack is responsible for re-inserting a DI-AppServe package

        This is achieved by decrypting the package,
        untaring the sub-tarballs and replacing the DI package
        re-tar, and re-sign
        """

        m = re.search("Package-([0123456789.]+)_", di_package)
        di_version = m.group(1)

        otapack = "otapack"
        signer = 'signerclient'
        assert os.path.exists(fw_package), "Fw package does not exist"
        assert os.path.exists(di_package), "DI package does not exist"

        rohan = os.path.join(workdir, "rohan")
        os.mkdir(rohan)

        tmp = os.path.join(workdir, "tmp_file")
        os.mkdir(tmp)



        if os.path.basename(fw_package).startswith("decrypted-"):
            FILENAME = os.path.basename(fw_package)[10:]
            if FILENAME.startswith("signed-"):
                FILENAME = FILENAME[7:]
            SRC = fw_package
            pos = FILENAME.find('.tar.gz')
            if pos != -1:
                FILENAME = FILENAME[:pos+7]
            pkg_name = FILENAME
        else:
            asdir = os.path.join(rohan, "AppServ")
            os.mkdir(asdir)
            with MyZipFile(fw_package) as z:
                z.extractall(asdir)

            pkg_name = self._find_file(asdir, "tar.gz")
            INTERNAL = os.path.join(asdir, pkg_name)
            decrypted = os.path.join(os.path.dirname(fw_package), "decrypted-" + pkg_name)
            SRC = decrypted
            if not os.path.exists(decrypted):
                SRC = self.decrypt_package(INTERNAL, decrypted)

            FILENAME = os.path.basename(fw_package)[7:]

        rfs = os.path.join(rohan, "rootfs")
        l1 = os.path.join(rohan, "layer1")
        l2 = os.path.join(rohan, "layer2")
        os.mkdir(rfs)
        os.mkdir(l1)
        os.mkdir(l2)

        with tarfile.open(name=SRC, mode="r:gz") as t:
            t.extractall(l1)


        logging.info(os.listdir(l1))


        INNER = os.path.join(l1, self._find_file(l1, "tar.gz"))

        assert os.path.exists(INNER), f"{INNER} does not exist"


        # tempdata = tar.extract(member, path=f'{workdir}/tmpfile',set_attrs=False)


        # extract_tar(INNER)

        tar_file = INNER

        logging.info(INNER)

        # Find .xz file and extract it to layer2
        # INNER = os.path.join(l1, self._find_file(l1, "tar.gz"))

        logging.info('l1')
        logging.info(os.listdir(l1))
        logging.info('l2')
        logging.info(os.listdir(l2))
        logging.info('rfs')
        logging.info(os.listdir(rfs))
        # logging.info(os.path.join(l2, INNER))
        # assert os.path.exists(os.path.join(l2, INNER))
        



        with tarfile.open(name=tar_file, mode='r:gz') as t:
            t.extractall(l1)

        logging.info('l1')
        logging.info(os.listdir(l1))
        logging.info('l2')
        logging.info(os.listdir(l2))
        logging.info('rfs')
        logging.info(os.listdir(rfs))


        with tarfile.open(name=os.path.join(l1, self._find_file(l1, "tar.gz")), mode='r:gz') as t:
            t.extractall(l2)

        logging.info('l1')
        logging.info(os.listdir(l1))
        logging.info('l2')
        logging.info(os.listdir(l2))
        logging.info('rfs')
        logging.info(os.listdir(rfs))


        # now we should have the rootfs.tar.gz in layer2, so extract that
        ROOTFS = os.path.join(l2, "rootfs.tar.gz")

        logging.info(ROOTFS)
        extract_tar(ROOTFS)


        # Set permissions for extracted files/directories (if needed)
        # for root, dirs, files in os.walk(l2):
        #     for dir_name in dirs:
        #         dir_path = os.path.join(root, dir_name)
        #         os.chmod(dir_path, 0o755)  # Change to appropriate permissions
                
        #     for file_name in files:
        #         file_path = os.path.join(root, file_name)
        #         os.chmod(file_path, 0o644)  # Change to appropriate permissions

        assert os.path.exists(ROOTFS), "ROOTFS does not exist"

        
        # with tarfile.open(name=ROOTFS) as t:

        with tarfile.open(name=ROOTFS, mode='r:gz') as t:
            # t.extractall(rfs,numeric_owner=True)
            t.extractall(tmp)

        logging.info('sussessfully - 12311212')

        
        # didir = os.path.join(rfs, 'usr/share/itron/improv/Diff.Install/AppServ')
        didir = os.path.join(rfs, 'usr/share/itron/PreInstall')

        cur_pack = self._find_file(didir, "DI-AppServices-Package")
        logger.info("wrapped DI package being replaced: %s", cur_pack)

        shutil.rmtree(didir)
        os.mkdir(didir)

        # extract scripts
        with MyZipFile(di_scripts, "r") as z:
            z.extractall(didir)

        # extract DI package
        ok = False
        with MyZipFile(di_package, "r") as z:
            for zf in z.filelist:
                if zf.filename.find(".gz") != -1:
                    z.extract(zf, didir)
                    ok = True
                    break
        assert ok, "Couldn't find .gz file in di appserv package"

        # re-tar rootfs
        logging.info("tar create %s with %s", ROOTFS, rfs)
        with tarfile.open(ROOTFS, 'w:gz') as t:
            t.add(rfs, './')

        # re-tar layer 2
        logging.info("tar create %s with %s", INNER, l2)
        with tarfile.open(INNER, "w:xz") as t:
            t.add(l2, "./")

        TARGET_NAME = f'repacked-{di_version}-{FILENAME}'
        TARGET = os.path.join(workdir, TARGET_NAME)
        TARGET_TMP = os.path.join(rohan, "decrypted.tar.gz")
        # PRE_SIGNED = os.path.join(TMP, FILENAME)
        SIGNED_NAME = pkg_name

        # re-tar layer 1
        logging.info("tar create %s with %s", TARGET_TMP, l1)
        subprocess.check_call(f"tar czf {TARGET_TMP} *", shell=True, cwd=l1)
        # with tarfile.open(TARGET_TMP, 'w:gz') as t:
        #    t.add(l1, './')

        cmd = ' '.join([otapack, os.path.basename(TARGET_TMP), SIGNED_NAME])
        subprocess.check_call(cmd, shell=True, cwd=rohan)
        logger.info("Calling signtool for %s", SIGNED_NAME)
        output = subprocess.check_output(' '.join([signer, '-k', 'itron-test', 'x', SIGNED_NAME]), shell=True, cwd=rohan)
        lines = output.decode('utf-8').splitlines()
        for line in lines:
            logging.info(line)
            if line.startswith('Got name:'):
                SIGNED = line[10:]
        logger.info("Finished repack %s", TARGET_NAME)
        shutil.copyfile(os.path.join(rohan, SIGNED), TARGET)
        return TARGET

        # unreachable: return ret


    def repack_image(self,image_file,workdir):

        """Repack Image is responsible for make a DI-AppServe package form the Image (tar.gz file)

        This is achieved by decrypting the package,
        replacing the DI package
        re-tar, and re-sign
        """


        otapack = 'otapack'
        signer = 'signerclient'
        

        assert os.path.exists(image_file), f"{image_file} does not exist"

        self.decrypt_package(image_file,workdir)
        dir = os.listdir(workdir)

        for file in dir:
            if('decrypt' in file):
                encrypted_file =file
        logging.info(encrypted_file)

        di_file = os.path.join(workdir, 'decrypted_' + os.path.basename(image_file))
        image_file = os.path.join(workdir, encrypted_file)
        FwMan.decrypt(image_file,di_file)

        tar_gz_fie = os.path.basename(di_file)

        cmd = ' '.join([otapack, tar_gz_fie, f'new_{tar_gz_fie}'])

        subprocess.check_call(cmd, shell=True, cwd = workdir)
        logging.info(f"Calling signtool for new_{tar_gz_fie}")

        output = subprocess.check_output(' '.join([signer, '-k', 'itron-test', 'x', f'new_{tar_gz_fie}']), shell=True, cwd=workdir)
        lines = output.decode('utf-8').splitlines()
        for line in lines:
            logging.info(line)
            if line.startswith('Got name:'):
                SIGNED = line[10:]
        logging.info("Finished repack %s", image_file)
        new_image = os.path.join(workdir, SIGNED)
        logging.info(new_image)

        return new_image


    def get_process(self, filters: FilterMatch or list(FilterMatch),  include_zombies=False):
        return self.mm.get_process(self.connection, filters, include_zombies)



def extract_tar(tar_file):
            
    logging.info(tar_file)
    assert os.path.exists(tar_file), f"{tar_file} does not exist"

    tar = tarfile.open(tar_file, mode="r:gz")
    members = tar.getmembers()
    file_permission = stat.S_IRUSR | stat.S_IWUSR
    folder_permission = stat.S_IRUSR | stat.S_IWUSR | stat.S_IXUSR
    # Let's pretend we want to edit, and write to new tar
    if len(members) > 0:
        fd, tmp_tar = tempfile.mkstemp(prefix=("%s.fixed." % tar_file))
        os.close(fd)
        fixed_tar = tarfile.open(tmp_tar, "w:gz")
        # Then process members
        for member in members:
            # add o+rwx for directories
            if member.isdir() and not member.issym():
                member.mode = folder_permission | member.mode
                extracted = tar.extractfile(member)
                fixed_tar.addfile(member, extracted)
            # add o+rw for plain files
            elif member.isfile() and not member.issym():
                member.mode = file_permission | member.mode
                extracted = tar.extractfile(member)
                fixed_tar.addfile(member, extracted)
            else:
                fixed_tar.addfile(member)

  
                
        fixed_tar.close()

        os.remove(tar_file)
        logging.info('remove sussessful')

        # Rename the fixed tar to be the old name
        os.rename(tmp_tar,tar_file)

    tar.close()
    assert os.path.exists(tar_file), f"{tar_file} does not exist"

    logging.info('sussessful')
    logging.info(tar_file)

class AdvancedMeter(SSHGen5Meter):
    """
    An advanced meter contains more than just the IP address. It includes the database information
    related to the meter from the --dut-db option.
    """
    def __init__(self,meter,logger,*args,timeout=10*60):
        # initialize the SSH object with the meter ip address from the db
        super().__init__(meter.ip_address,logger,*args,timeout=timeout)
        self._meter_db = meter

    @property
    def meter_db(self):
        return self._meter_db

class ParallelMeter(SSHGen5Meter):
    pass

    """ This is just an implementation of SSHMeter
    def __new__(self, meter, logger, *args, timeout=10*60):
        if isinstance(meter, MeterInstanceBase):
            return AdvancedMeter(meter, logger, *args, timeout=timeout)
        else:
            return SSHGen5Meter(meter, logger, *args, timeout=timeout)
    """
