#!/usr/bin/env python3
"""
MeterMan provides a connectionless set of utilities for manipulating
a meter.  The connection is either managed by the method, or passed to
the method

"""
import os
import argparse
import socket
import re
import subprocess
import time
import sys
import datetime
from .RemoteSSH_paramiko import RemoteSSH as RemoteSSH,SSHAuthenticationError, SSHConnectError, SSHTimeout
from enum import Enum
import zipfile
from .utils import ping
from . import Walker
from tempfile import TemporaryDirectory
import requests
from click import progressbar
import itron.meter.FwMan as FwMan
import csv
import codecs

#IMPROV_INSTALL="ImProvHelper.sh"
IMPROV_INSTALL_DEBUG="ENABLE_IMPROV_SCRIPT_LOGS=1 ImProvHelper.sh"
IMPROV_INSTALL="ENABLE_IMPROV_SCRIPT_LOGS=1 ImProvHelper.sh"

class MeterState(Enum):
    """ current state of meter durring install """
    NOT_STARTED = 1
    STARTED = 2
    REBOOTING = 3
    REBOOT_COMPLETE = 4
    DONE = 5

class DatabaseError(Exception):
    def __init__(self, message, code):
        # Call the base class constructor with the parameters it needs
        super().__init__(message)

        # Now for your custom code...
        self.code = code


class FilterMatch:

    PID_COLUMN=0
    USER_COLUMN=1
    VSC_COLUMN=2
    STATUS_COLUMN=3
    PROCESS_COLUMN=4
    ARGS_COLUMN=5

    def __init__(self, match, column, is_re=False):
        self.plain = match
        if not is_re:
            self.match = re.compile(re.escape(match))
        else:
            self.match = re.compile(match)
        self.column = column
        self.is_re = is_re

class MeterMan:
    """ Meter Manager - Controls a meter through an abstracted interface """
    def __init__(self, hostname, logger):
        self.hostname = hostname
        self.logger = logger

    def login( self, timeout_ok=False, no_scp=False) -> 'RemoteSSH':
        connection = RemoteSSH(self.hostname, self.logger, timeout_ok=timeout_ok, no_scp=no_scp)
        return connection

    def upload_keys( self ):
        self.logger.info("Uploading SSH keys...")
        home = os.getenv("HOME")
        if os.path.exists(f"{home}/.ssh/id_rsa.pub"):
            subprocess.run(["sshpass", "-p", "itron", "ssh-copy-id",  "-i",  f"{home}/.ssh/id_rsa.pub", f"root@{self.hostname}"], check=False)


    def reboot_meter( self, timeout=3*60):
        self.logger.info("Reboot Meter")
        return self.reboot_meter_connected(self.login(), timeout)

    def reboot_meter_connected(self, connection, timeout=3*60):
        return self._reboot_and_wait("/sbin/reboot", connection, timeout)

    def _reboot_and_wait(self, command, connection: 'RemoteSSH', timeout) -> 'RemoteSSH':
        try:
            self.logger.info("Executing: %s", command)
            connection.command(command, timeout=10, expect_error=True)
        except (SSHConnectError, SSHAuthenticationError, SSHTimeout,socket.timeout):
            pass
        except BaseException as e:
            self.logger.exception("Unexpected exception")
            raise
        #except (pssh.exceptions.Timeout, pssh.exceptions.ConnectionError, gevent.timeout.Timeout):
        #    pass
        return self.wait_reconnect(connection, command, timeout)

    def gmr_from_connected( self, connection, timeout=3*60 ):
        return self._reboot_and_wait("/usr/share/itron/scripts/GlobalMeterReset.sh", connection, timeout)

    def gmr(self, timeout=3*60):
        self.logger.info("GMR")
        return self.gmr_from_connected(self.login(),timeout)

    def wait_reconnect(self, connection, name, timeout):
        connection.disconnect()
        timeout_down = time.time() + 30

        # wait for meter to go away
        while ping(self.logger, self.hostname) and time.time() < timeout_down:
            pass

        connection = self.wait_up(name)

        timeout_up = time.time() + timeout

        # now we need monit to be running before we can start another install
        done = False
        while time.time() < timeout_up:
            running = self.get_process(connection,
                FilterMatch(".*ResourceCaddy", column=FilterMatch.PROCESS_COLUMN, is_re=True)
                )

            if running:
                done = True
                break
            else:
                time.sleep(10)
                self.logger.debug("wait for monit to init")

        assert done, "Timeout waiting for resource caddy"
        return connection


    def get_process(self, connection: 'RemoteSSH', matchlist: FilterMatch or list(FilterMatch), is_re=False, include_zombies=False):
        """ filer process list.
            defaults to filtering by process name

            Example:

            PID USER       VSZ STAT COMMAND
                1 root      5600 S    init -- root=/dev/mapper/eda_rootfs
                2 root         0 SW   [kthreadd]

                    0 = process id
                    1 = user name
                    2 = vsc
                    3 = status
                    4 = command
                    >4 should not be used with this function
                """
        ret = []
        if type(matchlist) is not list:
            matchlist = [matchlist]

        # cut down on interface traffic if we don't have any regex patterns
        if all([not filter.is_re for filter in matchlist]):
            cmd = "ps -w | grep -e " + ' -e '.join([filter.plain for filter in matchlist])
        else:
            cmd = "ps -w"
        psout = connection.command(cmd)

        if psout:

            if psout[0].startswith("  PID USER  "):
                #remove heading line
                psout = psout[1:]

            for line in psout:
                s = line.split()
                s = s[:5] + [line[26:]]
                if s[3] != 'Z' or include_zombies:
                    found = [filter.match.search(s[filter.column]) if len(s) >= filter.column else False for filter in matchlist]
                    if all(found):
                        # make s[4] all args, instead of split args
                        result = s[:5]
                        result.append(line[26:])
                        ret.append(result)

        return ret

    def wait_up( self, cmd, timeout=20*60) -> 'RemoteSSH':
        start = time.time()
        timeout = start + timeout
        self.logger.info(f"waiting for %s to finish", cmd)
        connection = None
        while time.time() < timeout:
            try:
                if (ping(self.logger, self.hostname) and
                    ping(self.logger, self.hostname, port=22)
                ):
                    connection = self.login(timeout_ok=True)
                    break

            except ConnectionRefusedError as ex:
                self.logger.info("waiting: connection error %s", ex)
            except (SSHConnectError, SSHAuthenticationError, SSHTimeout,socket.timeout):
                pass

            except BaseException as ex:
                self.logger.execption("Warning: unexpected exception")
                pass
            #except (socket.timeout, pssh.exceptions.Timeout, gevent.timeout.Timeout):
            #    self.logger.info("waiting: login timeout")
            #except pssh.exceptions.ConnectionError:
            #    self.logger.info("waiting: login error")
            #except pssh.exceptions.AuthenticationError as ex:
            #    self.logger.info("waiting: auth failure, updating keys %s", ex)
            #    self.upload_keys()
            #except pssh.exceptions.SessionError as ex:
            #    self.logger.info("waiting: login error")

            time.sleep(5)

        assert connection, "timeout waiting for meter"
        secs = int(time.time() - start)
        self.logger.info(f"\nMeter up and running after {secs}s")
        return connection

    def rcp_internal_package( self, connection, path, file, use_cache=False, use_partial=False):
        with TemporaryDirectory() as tmpdirpath:
            full = os.path.join(path,file)
            if full.startswith('http://'):
                with TemporaryDirectory() as zipdir:
                    url = full
                    r = requests.get(url, stream=True)
                    r.raise_for_status()
                    total_size_in_bytes= int(r.headers.get('content-length', 0))
                    zipfile_name = os.path.join(zipdir, os.path.basename(url))
                    with progressbar(length=total_size_in_bytes, label=zipfile_name) as p:
                        with open(zipfile_name, 'wb') as file:
                            for chunk in r.iter_content(chunk_size=8192):
                                file.write(chunk)
                                p.update(len(chunk))
                    with zipfile.ZipFile(zipfile_name, 'r') as zip_ref:
                        zip_ref.extractall(tmpdirpath)
            else:
                with zipfile.ZipFile(full, 'r') as zip_ref:
                    zip_ref.extractall(tmpdirpath)

            dirs = Walker.listdir(tmpdirpath)
            zips = list(filter(re.compile(".*\\.tar\\.gz").match, dirs))
            if len(zips) != 1:
                self.logger.info("Error extracting tar.gz file from zipfile")
                raise ValueError("Error extracting tar.gz file from zipfile")
            gz_file = zips[0]
            self.logger.info("Sync %s with meter", gz_file)

            if use_cache:
                target = f"/media/mmcblk0p1/imagecache"
                connection.command(f'mkdir -p {target}')
            else:
                target = f"/media/mmcblk0p1"

            _from = os.path.join(tmpdirpath, gz_file)
            _to =  os.path.join(target, os.path.basename(gz_file))
            if use_partial:
                _to = _to + '.part'
            self.logger.info("scp %s %s", _from, _to)
            connection.put_file(_from, _to)

        ret = os.path.join(target, gz_file)
        self.logger.info("rcp image=%s", ret)
        return ret


    def update_image_cache( self,  remote, path):
        ''' Copy the .gz file inside of the package (signed zip)
            onto the meter
            '''

        package_file = os.path.basename(path)
        path = os.path.dirname(path)

        self.logger.info("RSync %s/%s to meter cache",path,package_file)
        remote.command("rm -f /media/mmcblk0p1/imagecache/*.part")

        result = remote.ls_list('/media/mmcblk0p1/imagecache')

        # guess at the internal file's name.  Should be a subset of the package
        # name
        internal_gz_file = re.sub('signed-', '', package_file)
        internal_gz_file = re.sub('.tar.gz.*', '.tar.gz', internal_gz_file)

        if len(result) > 10:
            self.logger.warning("there are too many imagecache entries: %s %s", len(result), result)
            while len(result) > 9:
                file = result.pop(0)
            if not re.compile(internal_gz_file+"$").search(file):
                cmd = f"rm -f /media/mmcblk0p1/imagecache/{file}"
                self.logger.warning(cmd)
                remote.command(cmd)
            result = remote.ls_list('/media/mmcblk0p1/imagecache')


        cached = False

        self.logger.info('Files on the meter: %s', result)
        if result is None:
            self.logger.info("mkdir /media/mmcblk0p1/imagecache")
            remote.mkdir('/media/mmcblk0p1/imagecache')
        elif re.compile(internal_gz_file+"$").search('\n'.join(result)):
            cached = True
            remote.command(f"touch /media/mmcblk0p1/imagecache/{internal_gz_file}" )

        if not cached:
            gz_file2 = self.rcp_internal_package(remote, path,f"{package_file}",use_cache=True, use_partial=True)
            remote.command(f"mv /media/mmcblk0p1/imagecache/{internal_gz_file}.part /media/mmcblk0p1/imagecache/{internal_gz_file}")
            if os.path.basename(gz_file2) != internal_gz_file:
                self.logger.warn("inner/outer gz files don't match, cache consistency error, inner: %s, outer: %s",gz_file2, internal_gz_file)

        return internal_gz_file

    def db_operation(self, query, connection, header=False, options='', splitlines=True):
        timeout = time.time() + 60
        header = '-header' if header else ''
        while time.time() < timeout:
            cmd = f"sqlite3 /usr/share/itron/database/muse01.db {header} {options} '{query}'"
            code, table, error = connection.command_with_all(cmd, splitlines=False)
            if error or code:
                self.logger.error("sqlerror: (%s) %s", code, error)
            # code 5 is db locked
            if code == 5:
                time.sleep(1)
            else:
                break

        if code:
            raise DatabaseError(error, code)
        if splitlines:
            table = table.splitlines()
        return table


    def get_table(self, connection, table, query=None):
        query = f'select * from {table}' if not query else query
        entries = []
        lines=self.db_operation(query, connection, header=True)

        # command is echoed, so
        while len(lines) > 0:
            headers = lines.pop(0).split('|')
            if len(headers) > 2:
                break


        found = None
        while len(lines):
            values = lines.pop(0).split('|')
            data = {}

            # convert to dictionary
            for x in range(0,len(headers)):
                data[headers[x]] = values[x]

            entries.append(data)

        return entries

    @staticmethod
    def splitWithQuotes(txt):
        s = txt.split('\n')
        res = []
        cnt = 0
        for item in s:
            if res and cnt % 2 == 1:
                res[-1] = res[-1] + '\n' + item
            else:
                res.append(item)
                cnt = 0
            cnt += item.count('"')
        return res

    def get_table_csv( self, connection, table, query=None):
        """ better version of get table function that parses binary data and accepts
            quoted strings """

        query = f'select * from {table}' if not query else query
        entries = []

        lines  = self.db_operation(query, connection, options='-quote -header', splitlines=False)
        if lines:
            lines = self.splitWithQuotes(lines)

            # read data and convert to python
            for x in csv.DictReader(lines, quotechar="'", delimiter=',',skipinitialspace=True):
                for k,v in x.items():
                    if v.startswith("X'"):
                        x[k] = codecs.decode(v[2:-1], 'hex_codec')
                    if v == "NULL":
                        x[k] = None
                entries.append(x)
        return entries

    def get_task_status( self, connection ):
        pending = 0
        failed = 0
        complete = 0
        data = self.get_table(connection, "ImageProcessStatus")
        for item in data:
            code = int(item['ResultCode'])
            if code <= -1:
                pending = pending + 1
            elif code == 0:
                complete = complete + 1
            else:
                failed = failed + 1

        return data, pending, failed, complete

    # convert epoch to human readable
    def epoch_to_str(self, epoch_str):
        try:
            return datetime.datetime.fromtimestamp(int(epoch_str)).strftime('%c')
        except:
            return "None"

    def monitor_task_complete(self, start_time, gz_file, timeout=15*60, retries = 4):
        self.logger.info("monitor_task_complete: %s", gz_file)
        code = 255
        boot_started=False
        task_id = None
        found = None
        if not start_time:
            start_time = time.time()

        timeout_time = start_time + timeout # five minutes
        connection = None
        retry = 0

        state = MeterState.NOT_STARTED

        while True:
            self.logger.debug("Meter state: %s", state)
            try:
                # Meter may go down durring install, so
                # close the connection, and reconnect
                if connection:
                    connection.close_channel()
                    connection = None

                if ping(self.logger, self.hostname):
                    connection = self.login(timeout_ok=True)
                else:
                    boot_started = True
                    self.logger.info("Ping failed - meter booting")
                    self.wait_up( "operation")
                    continue

                #connection = self.login()
                entries = self.get_task_status(connection)[0]
                meter_time = int(connection.command("date '+%s'")[0])

                diff_time = meter_time - time.time()

                connection.disconnect()
                connection = None

                if not found:
                    for data in entries:
                        if gz_file:
                            # meter time can be off, so correct
                            begin_time = int(data['BeginTime']) - diff_time
                            self.logger.info("Image time: %s  Start time: %s  diff: %s:", begin_time, start_time, begin_time - start_time)
                            if  begin_time >= start_time:
                                if not re.search(gz_file, data["Parameters"]):
                                    self.logger.warning("Image name does not match but time does (%s != %s", gz_file, data["Parameters"])
                                found = data
                                task_id = int(data['Id'])
                                state = MeterState.STARTED
                        else:
                            if (data['Id'] == entries[-1]['Id']):
                                found = data
                                task_id = int(data['Id'])
                                state = MeterState.STARTED

                        tcode = int(data['ResultCode'])
                        if(tcode <= -1):
                            self.logger.info("%s code: %s", data['Parameters'], data['ResultCode'])
                        else:
                            self.logger.debug("%s code: %s", data['Parameters'], data['ResultCode'])

                if found and len(entries) >= task_id:
                    # update found with new data
                    found = entries[task_id-1]
                    self.logger.info("Status (%s: code:%s begin:%s end:%s)", task_id, found['ResultCode'],self.epoch_to_str(found['BeginTime']),self.epoch_to_str(found['EndTime']))

                    # get the result code from the last entry
                    code = int(found['ResultCode'])
                    if code <= -1:
                        self.logger.info("In progress")
                    else:
                        if code:
                            self.logger.info(f"Failed.  ResultCode: {code}")
                            return code

                        self.logger.info(f"Operation Successful: {found['Parameters']}")
                        return 0
                else:
                    if boot_started:
                        if len(entries) >= 1 and task_id != None:
                            if len(entries) > task_id:
                                code = int(entries[task_id]['ResultCode'])
                                state = MeterState.REBOOT_COMPLETE
                                if code <= -1:
                                    self.logger.info('Reboot successful: still pending complete')
                                else:
                                    self.logger.info(f"Reboot successful: entry: {entries[task_id]['ImagePath']}: code: {code}")
                                    return code
                            else:
                                self.logger.info(f'Reboot successful, but could not find task (was {task_id})')
                                task_id = None

                        else:
                            self.logger.info("Reboot successful, ImageProcessStatus table empty")
                            return 0
                    else:
                        self.logger.info("operation not started yet... Waiting")

                if timeout_time < time.time():
                    raise ValueError("Operation did not complete within expected time")
                time.sleep(20)

            # except (socket.timeout, pssh.exceptions.Timeout, pssh.exceptions.ConnectionError, gevent.timeout.Timeout):
            except socket.timeout:
                status = "meter down"
                self.logger.info(status)
                self.wait_up( "operation")
                boot_started = True
                state = MeterState.REBOOTING
                continue

            #except pssh.exceptions.AuthenticationError:
            #    self.logger.info("Meter key exchange failure, updating ~/.ssh/id_rsa.pub key in authorized_keys")
            #    self.upload_keys()
            #    continue
            except (SSHConnectError, SSHAuthenticationError,SSHTimeout,socket.timeout) as e:
                retry += 1
                if retry > retries:
                    raise
                self.logger.warning('meter connection retry %s %s', retry, e)
                time.sleep(10)
                continue

            except Exception as e:
                self.logger.exception("Unhandled exception error %s", e)
                raise

        return code

    def getfwver( self, remote):
        return self.get_lid(remote, 'ILID_SYSTEM_FW_VERSION', dynamic=False)

    def get_lid_from_tp( self, remote, lid):
        (return_code, data) = remote.execute_command(f"ImProvHelper.sh --ReadLid {lid}")
        lines = data
        if return_code: return return_code
        self.logger.debug("lines: %s",lines)
        if lines:
            ver = lines.pop()
        else:
            ver = None
        return ver

    def get_lid( self, remote, lid, dynamic=True):
        if dynamic:
            config='dynamicconfiguration'
        else:
            config='configuration'

        (return_code, data) = remote.execute_command(f"sqlite3 /mnt/common/database/muse01.db \"select valuetext from {config} WHERE Lid = (SELECT LID FROM LIDS WHERE DefineName='{lid}');\"")
        lines = data
        if return_code: return return_code
        self.logger.debug("lines: %s", lines)
        if lines:
            ver = lines.pop()
        else:
            ver = None
        return ver

    def get_lid_static( self, remote, lid):
        (return_code, data) = remote.execute_command(f"sqlite3 /mnt/common/database/muse01.db \"select valuetext from configuration WHERE Lid = (SELECT LID FROM LIDS WHERE DefineName='{lid}');\"")
        lines = data
        if return_code: return return_code
        self.logger.debug("lines: %s", lines)
        if lines:
            ver = lines.pop()
        else:
            ver = None
        return ver

    def diff_upgrade( self, diff_path,diff_file):
        self.improv_cached(os.path.join(diff_path, diff_file))

    def get_build( self, args):
        return FwMan.get_build(file=args.file, version=args.version, diff_upgrade=args.diff_upgrade, downgrade=args.downgrade)

    def _clean_improv(self, connection):
        directory = '/mnt/idleRFS'
        rfslist = connection.ls_list(directory)
        if len(rfslist) > 0 and 'ImProv_image.tar.gz' in rfslist:
            self.logger.warning("Warning: idleRFS has files on it. Removing improv image (%s)", rfslist)
            connection.command(f'rm {directory}/ImProv_image.tar.gz')

    def improv_cached(self, file, connection=None):
        if connection is None:
            connection = self.login()

        self.logger.info("Starting improv for %s", file)

        cur_ver = self.getfwver(connection)
        self.logger.info(f"Current version: {cur_ver}")

        gz_file = self.update_image_cache(connection, file)

        # perform diff upgrade
        connection.command(f"cp /media/mmcblk0p1/imagecache/{gz_file} /mnt/common/{gz_file}")
        self.logger.info(connection.command(f"{IMPROV_INSTALL} --image /mnt/common/{gz_file}"))
        connection=None

    def coldstart_auto(self, connection, version, do_gmr, debug=False):

        # There are three modes:
        # - versions same, just coldstart
        # - lower version, diff upgrade from version
        # - higher version, use downgrade package

        cur_ver = self.getfwver(connection)

        info = FwMan.get_build_ex(version=version)
        new_ver = info['version']

        if do_gmr:
            connection = self.gmr_from_connected(connection)

        cmp = FwMan.compare_versions(cur_ver, new_ver)
        if cmp == 0:
            self.logger.info("--------------------------- Coldstart started")
            connection,code = self.install_with_reboot(connection, info['ColdStartPackage'])
        else:
            if cmp < 0:
                downfile = info['DowngradePackage']
                # downgrade required
                self.logger.info("--------------------------- Downgrade started")
                connection, code = self.install_with_reboot(connection, downfile, debug=debug)
            else:
                self.logger.info("--------------------------- Upgrade started")
                connection, code = self.upgrade(cur_ver, connection, info)

        return connection, code

    def upgrade(self, cur_ver, connection, info, debug=False):
        # we need to upgrade the meter from a known version
        twover = cur_ver.split('.')
        twover = twover[0] + '-' + twover[1]
        fromver = f"UpgradeFromSR_{twover}"
        if fromver in info:
            code = None
            if fromver in info:
                to_ver = FwMan.pkg_to_ver(info[fromver])
                if to_ver != cur_ver:
                    self.logger.info("Need to upgrade from %s to %s before diff upgrade", cur_ver, to_ver)
                    to_path, to_pkg, _, _ = FwMan.get_build(version=to_ver)
                    self.logger.info("--------------------------- Pre-diff upgrade started")
                    connection,code = self.install_with_reboot(connection, os.path.join(to_path, to_pkg))
                    assert code == 0, f"Error code {code} returned from installer"

                self.logger.info("--------------------------- Diff upgrade started")
                connection, code = self.install_with_reboot(connection, info[fromver])

                if code == 0:
                    self.logger.info("--------------------------- install complete")
                else:
                    self.logger.error("Error installing diff upgrade")
        else:
            self.logger.trace(f"version selected has no Upgrade option {fromver}, performing coldstart")
            connection, code = self.install_with_reboot(connection, info['ColdStartPackage'])

        return connection, code

    def cmd_coldstart( self, args, unknown):
        """ special handling for coldstart image (allows gmr) """
        if len(unknown) > 0:
            self.logger.info("Unrecognized arguments: %s", unknown)
            raise ValueError("Invalid parameter")

        if args.version and not args.file and not args.prompt and not args.downgrade:
            return self.coldstart_auto(self.login(), args.version, args.no_gmr)

        improv_mode = IMPROV_INSTALL if not args.debug else IMPROV_INSTALL_DEBUG

        coldpath, coldfile, diff_path, diff_file = self.get_build(args)
        start_time = time.time()

        if args.no_gmr:
            connection = self.gmr()
        else:
            connection = self.login()

        self._clean_improv(connection)

        cur_ver = self.getfwver(connection)
        self.logger.info("Current verwsion: %s", cur_ver)

        gz_file = self.update_image_cache( connection, os.path.join(coldpath, coldfile))

        if not args.no_coldstart:
            new_ver=re.search("FW(10[0123456789.]+)",coldfile)[1]

            # perform coldstart
            connection.command(f"cp /media/mmcblk0p1/imagecache/{gz_file} /mnt/common/{gz_file}")
            self.logger.debug(connection.command(f"{improv_mode} --image /mnt/common/{gz_file}"))
            ver = self.getfwver(connection)
            self.logger.debug("FWVer: %s", ver)
            connection=None

            code = self.monitor_task_complete(start_time, gz_file)
            if code == 0:
                self.logger.debug("coldstart successful - uploading .ssh/id_rsa.pub for auto-login")
                # now stuff our id_rsa.pub into the meter so we are passwordless
                self.upload_keys()
            else:
                self.logger.error("coldstart failed, aborting")
                return code
        connection = self.login()
        new_ver = self.getfwver(connection)
        self.logger.info(f"Current version: {new_ver}")

        if args.diff_upgrade:
            diff_start_time = time.time()
            self.diff_upgrade(diff_path,diff_file)

            if self.monitor_task_complete(diff_start_time, gz_file):
                connection = self.login()
                ver = self.getfwver(connection)
            else:
                self.logger.error("something bad happened...")
                return 1

        # improv needs some time after install before it is really done.  Give it a minute
        # time.sleep(60)

        return 0

    def install_with_reboot(self, connection, path, debug=False):
        self._clean_improv(connection)

        cur_ver = self.getfwver(connection)
        self.logger.info("Current verwsion: %s", cur_ver)

        gz_file = self.update_image_cache( connection, path)

        new_ver=FwMan.pkg_to_ver(path)
        improv_mode = IMPROV_INSTALL if not debug else IMPROV_INSTALL_DEBUG

        # perform coldstart
        connection.command(f"cp /media/mmcblk0p1/imagecache/{gz_file} /mnt/common/{gz_file}")

        start_time = time.time()

        self.logger.debug(connection.command(f"{improv_mode} --image /mnt/common/{gz_file}"))
        ver = self.getfwver(connection)
        self.logger.debug("FWVer: %s", ver)
        connection=None

        code = self.monitor_task_complete(start_time, gz_file)
        if code == 0:
            self.logger.debug("coldstart successful - uploading .ssh/id_rsa.pub for auto-login")
            # now stuff our id_rsa.pub into the meter so we are passwordless
            self.upload_keys()
        else:
            self.logger.error("coldstart failed, aborting")
            return None, code

        connection = self.login()
        new_ver = self.getfwver(connection)
        self.logger.info(f"Current version: {new_ver}")

        return connection, code

    def cmd_install( self, args, env_variable=None):
        start_time = time.time()
        connection = self.login()

        self._clean_improv(connection)

        if args.remote_file:
            rfile = args.remote_file
            install_cmd = f"{IMPROV_INSTALL} --image {args.remote_file}"

            if env_variable:
                env_variable = list(env_variable)
                env_variable.append(install_cmd)
                install_cmd = ';'.join(env_variable)

            self.logger.debug(connection.command(install_cmd))
        elif args.file:
            args.file = os.path.abspath(args.file)
            path = os.path.dirname(args.file)
            file = os.path.basename(args.file)
            self.logger.debug("path: %s, file:%s", path, file)
            if (os.path.exists(args.file)):
                rfile = self.rcp_internal_package(connection, path, file, use_cache=False)
                install_cmd = f"{IMPROV_INSTALL} --image {rfile}"
                if env_variable:
                    env_variable = list(env_variable)
                    env_variable.append(install_cmd)
                    install_cmd = ';'.join(env_variable)

                self.logger.debug(connection.command(install_cmd))

            else:
                self.logger.error('File does not exist, aborting')
                return 1
        else:
            self.logger.error("You must specify a local (--file) or remote file to install")
            return 1

        code = self.monitor_task_complete(start_time, os.path.basename(rfile))

        # improv needs some time after install before it is really done.  Give it a minute
        # time.sleep(60) - this should not be needed anymore.... we use port

        self.logger.info("Install complete. Code: %s", code)

        return code

    def cmd_status( self, args):
        connection = self.login()
        entries = self.get_task_status(connection)[0]
        tasks = self.get_table(connection, "ImageProcessTask")

        for item in entries:
            self.logger.info(f"{item['Id']}: {item['ImagePath']}: code: {item['ResultCode']}")
            for task in tasks:
                if task['ImageProcessStatusId'] == item['Id']:
                    self.logger.info("  %s %s", task['TaskUId'], task['FinalResult'])

    def capture(self, connection, directory):
        self.logger.info("capturing snapshot to %s", directory)
        os.makedirs(directory, exist_ok=True)
        capture_list = [
            "/mnt/common/database/muse01.db",
            "/mnt/pouch/LifeBoatLogs/ImProv.txt",
        ]
        clean_list = [
            "/mnt/common/*.txt"
        ]
        capture_list.extend(clean_list)

        for item in capture_list:
            try:
                _to = os.path.realpath(directory)
                files = connection.ls_list(item)

                for file in files:
                    name = os.path.basename(file)
                    self.logger.info(f"scp {file}, {os.path.join(_to, name)}")
                    connection.get_file(file, os.path.join(_to, name))
            except Exception as ex:
                self.logger.info('Exception %s', ex)
                self.logger.exception(ex)
                pass

        for item in clean_list:
            connection.command(f"rm -rf {item}")

    def cmd_capture( self, args):
        connection = self.login(no_scp=True)
        date=datetime.datetime.now().strftime("%d-%m-%Y_%H_%M_%S")
        directory='ses_'+self.hostname+'_'+date
        self.capture(connection, directory)

        latest = os.path.join(os.path.realpath(args.directory), "latest")
        try:
            os.remove(latest)
        except FileNotFoundError:
            pass

        os.symlink(directory, latest)


    def cmd_verinfo( self, args, unknown):
        connection = self.login()

        fw_ver = self.getfwver(connection)
        as_ver = self.get_lid(connection, 'ILID_DATASERVER_APPSERV_FW_VERSION')
        as_installed = self.get_lid_from_tp(connection,'ILID_APP_SERVICE_PKG_INSTALLED')
        self.logger.info(f"FW Ver: {fw_ver}")
        self.logger.info(f"AS Ver: {as_ver}")
        self.logger.info(f"AS Installed: {as_installed}")

    def cmd_wait_improv( self, args, unknown):
        self.monitor_task_complete()

    def parse_args( self, cmdline=sys.argv):
        parser = argparse.ArgumentParser(description='coldboot meter to specific version')
        parser.add_argument('-v','--version', default='latest', type=str, help='fw version to coldboot')
        parser.add_argument('-l', '--downgrade', action='store_true', help="select downgrade image from -v version (requires both args)")
        parser.add_argument('-u','--diff-upgrade', action='store_true', help='ask for version from list')
        parser.add_argument('-f','--file', type=str, help='file to install')
        parser.add_argument('-r','--remote-file', type=str, help='remote file to install')
        parser.add_argument('-c','--no-coldstart', action='store_true', help='do not perform a coldstart')
        parser.add_argument('-d', '--debug', default=None, type=str, help="debugging level (info, warn, error)")
        parser.add_argument('-n','--no-gmr', action='store_false', help='Do not GMR the meter before install')
        parser.add_argument('-m','--message', type=str, help='informational message to self')
        parser.add_argument('command', type=str, help="command to execute")

        args,unknown = parser.parse_known_args(cmdline)
        return args,unknown