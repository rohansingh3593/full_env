#!/usr/bin/env python3

import os
import argparse
from unicodedata import name
import re
import logging
import sys
import logging.handlers
import datetime
import abc
import glob
from os.path import dirname, realpath, sep, pardir
from curses import wrapper

# import the pytest directory in GITTOP/pytest so we can get DI tools
from git import Repo

def get_gittop():
    repo = Repo('.', search_parent_directories=True)
    GITTOP=repo.working_tree_dir
    return GITTOP

import rohan.meter.AsMan as AsMan
import rohan.meter.MeterMan as mm
import rohan.meter.Gen5Meter as Gen5Meter

IMPROV_INSTALL="ENABLE_IMPROV_SCRIPT_LOGS=1 ImProvHelper.sh"

logger = logging.getLogger()

INFO=logger.info
WARN=logger.warn
DEBUG=logger.debug

def generic_parameters(parser):
    parser.add_argument('-v','--version', default='latest', type=str, help='fw version to find')
    parser.add_argument('-l', '--downgrade', action='store_true',
        help="select downgrade image from -v version (requires both args)."
             " This is required if installing an older version of firmware on the meter")
    parser.add_argument('-p','--prompt', action='store_true', help='ask for version from list')
    parser.add_argument('-u','--diff-upgrade', action='store_true', help='perform a diff upgrade')
    parser.add_argument('-f','--file', type=str, help='file to install')
    parser.add_argument('-r','--remote-file', type=str, help='remote file to install')
    parser.add_argument('-c','--no-coldstart', action='store_true', help='do not perform a coldstart')
    parser.add_argument('-n','--no-gmr', action='store_false', help='Do not GMR the meter before install')
    parser.add_argument('-m','--message', type=str, help='informational message to self')

class CommandEntry(abc.ABC):
    """ base class of command
        paramters should be filled in by
        the derived class
    """
    def __init__(self, name,  help):
        self.name = name
        self.help = help

    def add_parser(self, subparsers, parent_parser):
        parser_create = subparsers.add_parser(self.name, parents=[parent_parser],
                                            add_help=False,
                                            help=self.help)
        parser_create.set_defaults(func=self)
        self.add_parameters(parser_create)

    @abc.abstractmethod
    def add_parameters(self, parser):
        pass

    @abc.abstractmethod
    def run_command(self, mgr, args, unknown):
        pass

class cmd_coldstart(CommandEntry):
    def __init__(self):
        super().__init__('coldstart', "Coldstart the meter with version specified")

    def run_command(self,mgr,args, unknown):
        if len(unknown) > 0:
            print("Unrecognized arguments: ", unknown)
            exit(1)
        return mgr.cmd_coldstart(args, unknown)

    def add_parameters(self, parser):
        generic_parameters(parser)

class cmd_install(CommandEntry):
    def __init__(self):
        super().__init__('install', "Use ImProvHelper.sh to install item (diff upgrade, agent, di, etc)")


    def add_parameters(self, parser):
        generic_parameters(parser)

    def run_command(self,mgr,args, unknown):
        return mgr.cmd_install(args)

class cmd_reboot(CommandEntry):
    def __init__(self):
        super().__init__('reboot', "reboot the meter with the /sbin/reboot command")

    def add_parameters(self, parser):
        generic_parameters(parser)

    def run_command(self,mgr,args, unknown):
        return mgr.reboot_meter()

class cmd_gmr(CommandEntry):
    def __init__(self):
        super().__init__('gmr', "Send global meter reset and wait for meter to come back up")

    def add_parameters(self, parser):
        generic_parameters(parser)

    def run_command(self,mgr,args, unknown):
        return mgr.gmr()
class cmd_status(CommandEntry):

    def __init__(self):
        super().__init__('status', "Display the ImProv database task/state table")

    def run_command(self, mgr, args, unknown):
        expect = mgr.login(timeout_ok=True)

        print("Query task status")
        entries = mgr.get_task_status(expect)[0]
        print("Query process status")
        tasks = mgr.get_table(expect,"ImageProcessTask")

        for item in entries:
            print(f"{item['Id']}: {item['ImagePath']}: code: {item['ResultCode']}")
            for task in tasks:
                if task['ImageProcessStatusId'] == item['Id']:
                    print("  ", task['TaskUId'], task['FinalResult'])

    def add_parameters(self, parser):
        pass


class cmd_capture(CommandEntry):
    def __init__(self):
        super().__init__('capture', "capture .txt files generated in /mnt/common for improv installs")

    def add_parameters(self, parser):
        parser.add_argument('-D','--directory', default='.', type=str, help='directory to store capture data')

    def run_command(self, mgr, args, unknown):
        expect = mgr.login(no_scp=True)
        date=datetime.datetime.now().strftime("%d-%m-%Y_%H_%M_%S")
        directory=os.path.join(args.directory, 'ses_'+args.target+'_'+date)
        print("capturing snapshot to", directory)
        os.mkdir(directory)
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
                files = expect.ls_list(item)

                for file in files:
                    name = os.path.basename(file)
                    print(f"scp {file}, {os.path.join(_to, name)}")
                    expect.get_file(file, os.path.join(_to, name))
            except Exception as ex:
                print('Exception', ex)
                pass

        for item in clean_list:
            expect.command(f"rm -rf {item}")

        latest = os.path.join(os.path.realpath(args.directory), "latest")
        try:
            os.remove(latest)
        except FileNotFoundError:
            pass

        os.symlink(directory, latest)


class cmd_asdir(CommandEntry):
    def __init__(self):
        super().__init__('asdir', "find version of AS on build server")

    def add_parameters(self, parser):
        parser.add_argument('-v','--version', default='latest', type=str, help='di version to find')

    def run_command(self, mgr, args, unknown):
        assert args.version, "you must specify a version for this command"
        bld = AsMan.get_build(args.version)
        if bld:
            print(bld)
        else:
            os.exit(1)

class cmd_fwdir(CommandEntry):
    def __init__(self):
        super().__init__('fwdir', "find a version of firmware on build output server")

    def add_parameters(self, parser):
        generic_parameters(parser)

    def run_command(self, mgr, args, unknown):
        coldpath, coldfile, diff_path, diff_file = mgr.get_build(args)

        if diff_path:
            print(os.path.join(diff_path,diff_file))
        else:
            print(coldpath)

class cmd_sh(CommandEntry):
    def __init__(self):
        super().__init__('sh', "execute a shell command")

    def add_parameters(self, parser):
        pass

    def run_command(self, mgr, args, unknown):
        expect = mgr.login()
        INFO("ssh %s %s", args.target, unknown)
        if unknown:
            output = expect.command(' '.join(unknown))
            for line in output:
                print(line)
        else:
            expect.invoke_shell()


class cmd_preinstall(CommandEntry):
    def __init__(self):
        super().__init__('pre', "install all packages in the preinstall directory on meter")

    def add_parameters(self, parser):
        pass

    def run_command(self, mgr, args, unknown):
        with Gen5Meter.SSHGen5Meter(args.target, logger) as g5m:
            files = g5m.ls('/usr/share/rohan/PreInstall')
            for file in files:
                g5m.install(file=os.path.join('/usr/share/rohan/PreInstall',file), remote_file=True)


class cmd_verinfo(CommandEntry):
    def __init__(self):
        super().__init__('ver', "display version information from meter")

    def add_parameters(self, parser):
        pass

    def run_command(self, mgr, args, unknown):
        expect = mgr.login()

        fw_ver = mgr.getfwver(expect)
        as_ver = mgr.get_lid(expect, 'ILID_DATASERVER_APPSERV_FW_VERSION')
        as_installed = mgr.get_lid_from_tp(expect,'ILID_APP_SERVICE_PKG_INSTALLED')
        print(f"FW Ver: {fw_ver}")
        print(f"AS Ver: {as_ver}")
        print(f"AS Installed: {as_installed}")
        tbl = mgr.get_table(expect, "FWINFORMATION")
        found=False
        for item in tbl:
            if re.search("appservicesLR",item['PATH']):
                print(f"AS Hash: {item['ENABLED']} {item['VERSION']} {item['PATH']} {item['UNSIGNEDHASH']}")
                found=True
        if not found:
            print('AS Hash: not found')

class cmd_repack(CommandEntry):
    def __init__(self):
        super().__init__('repack', "repackage diff upgrade with new AppServe")

    def add_parameters(self, parser):
        parser.add_argument('-f','--file', type=str, help='file to repackage')
        parser.add_argument('-v','--asver', type=str, help='App serve version (build output if not specified)')
        parser.add_argument('-D','--directory', default=os.getcwd(), type=str, help='directory to store capture data')

    def run_command(self, mgr, args, unknown):
        with Gen5Meter.SSHGen5Meter(args.target, logger) as g5m:
            if args.asver:
                di_package = AsMan.get_build(args.version)
            else:
                gittop = AsMan.get_gittop()
                dir = os.path.join(gittop, 'artifacts/bionic-x86_64/TargetDebug/DI-AppServices-Package-*.zip')
                files = glob.glob(dir)
                print(files)
                assert len(files) == 1
                di_package=files[0]


            di_scripts = os.path.join( os.path.dirname(di_package), 'Diff_Scripts_' + os.path.basename(di_package))
            logger.info("DI Package: %s", di_package)
            logger.info("DI scripts: %s", di_scripts)
            assert os.path.exists(di_package), f"Error - {di_package} file does not exist"
            assert os.path.exists(di_scripts), f"Error - {di_scripts} does not exist, can't repack"

            file = g5m.repack_diff_package(logger, args.file, di_package, di_scripts, args.directory)
            print(file)

def curses_run(screen, args, unknown):
    import curses
    from rohan.meter.MeterDB import MeterDB
    from concurrent.futures import ThreadPoolExecutor, as_completed

    class CursesHandler(logging.Handler):
        def __init__(self, screen):
            logging.Handler.__init__(self)
            self.screen = screen
        def emit(self, record):
            try:
                msg = self.format(record)
                screen = self.screen
                fs = "\n%s"
                screen.addstr(fs % msg)
                screen.box()
                screen.refresh()
            except (KeyboardInterrupt, SystemExit):
                raise
            except:
                raise

    db = MeterDB(args.dut_db)
    meters = db.get_meters()
    _unicode = True
    MAX_ROW, MAX_COL = screen.getmaxyx()
    mid = int(MAX_ROW/2)
    win1 = curses.newwin(mid, MAX_COL,0,0)
    win2 = curses.newwin( MAX_ROW-mid, MAX_COL,mid+1,0)
    win1.box()
    win2.box()
    win1.addstr(2,2,"Testing my curses app")
    screen.refresh()
    win1.refresh()
    win1.scrollok(True)
    win1.idlok(True)
    win1.leaveok(True)
    win1.setscrreg(4, mid - 3)
    win1.addstr(4, 4, "")
    mh = CursesHandler(win1)
    formatter = logging.Formatter(' %(asctime) -25s - %(name) -15s - %(levelname) -10s - %(message)s')
    formatterDisplay = logging.Formatter('  %(asctime)-8s|%(name)-12s|%(levelname)-6s|%(message)-s', '%H:%M:%S')
    mh.setFormatter(formatterDisplay)
    #rootlogger = logging.getLogger()
    logger.addHandler(mh)

    for x in range(len(meters)):
        win2.addstr(x*2, 0, "Meter: {0}".format(str(meters[x])))
        win2.addstr(x*2+1, 0, "Status: Waiting for lock")
        win2.refresh()

    def ProcessMeter(meter, args, logger, stdscr, x):
        mgr = mm.MeterMan(meter.ip_address, logger)
        while True:
            locked = db.lock_node(meter.ip_address)
            if locked:
                stdscr.addstr(x*2+1, 0, "Status: locked")
                stdscr.refresh()
                break
        try:
            args.func.run_command(mgr, args, unknown)
        except Exception as e:
            import traceback
            stdscr.addstr(x*2+1, 0, "Status: Exception - "+e)
        finally:
            db.unlock_node(meter.ip_address)

        return x

    with ThreadPoolExecutor(len(meters)) as executor:
        futures = {executor.submit(ProcessMeter, meters[i], args, logger, win2, i) for i in range(len(meters))}

        for fut in as_completed(futures):
            res = fut.result()
            print(f"The gen outcome is {res}")

class SpecialFormatter(logging.Formatter):
    FORMATS = {logging.DEBUG :"%(relativeCreated)7.3fs DBG:   %(module)s: %(lineno)d: %(message)s",
               logging.ERROR : "%(relativeCreated)7.3fs ERROR: %(message)s",
               logging.INFO : "%(relativeCreated)7.3fs %(message)s",
               'DEFAULT' : "%(relativeCreated)7.3fs %(levelname)s: %(message)s"}

    def format(self, record):
        record.relativeCreated = record.relativeCreated / 1000
        self._style._fmt = self.FORMATS.get(record.levelno, self.FORMATS['DEFAULT'])
        return super().format(record)


commands = [
    cmd_coldstart(),
    cmd_install(),
    cmd_status(),
    cmd_capture(),
    cmd_fwdir(),
    cmd_asdir(),
    cmd_verinfo(),
    cmd_sh(),
    cmd_repack(),
    cmd_preinstall(),
    cmd_reboot(),
    cmd_gmr(),
]

class SmartFormatter(argparse.HelpFormatter):

    def _split_lines(self, text, width):
        if text.startswith('R|'):
            return text[2:].splitlines()
        # this is the RawTextHelpFormatter._split_lines
        return argparse.HelpFormatter._split_lines(self, text, width)



def main():

    all_args = sys.argv
    parent_parser = argparse.ArgumentParser(description='Meter Manager - manage meter attached to SSH')
    parent_parser.add_argument('-d', '--debug', action='store_true', help="set log level to debug")
    parent_parser.add_argument('--target', type=str, default=os.getenv('TARGET'), help="hostname/IP of meter to use (defaults to TARGET env var)")
    parent_parser.add_argument('--mm_version', action='version', version='%(prog)s 2.0')
    parent_parser.add_argument('--dut-db', default=None, type=str, help='run coldstart on all meters in group')

    parser = argparse.ArgumentParser(description='Meter Manager - manage meter attached to SSH')
    # create sub-parser for all commands
    subparsers = parser.add_subparsers(dest='command', title="operation", description="Select the appropriate command below.  Use command followed by --help for options related to that command",help="Operation")
    for item in commands:
        item.add_parser(subparsers, parent_parser)

    # standard options for all commands

    args,unknown = parser.parse_known_args()

    TARGET = args.target
    assert args.target, "You must either have a TARGET env var or specify the --target option"

    HOME = os.getenv('HOME')
    Log_Format = "%(relativeCreated)s %(levelname)s %(asctime)s - %(message)s"


    level = logging.INFO

    if args.debug:
        level = logging.INFO

    formatter = SpecialFormatter()
    #logging.Formatter(
    #'%(asctime)s | %(name)s |  %(levelname)s: %(message)s')

    if not args.dut_db:
        stream_handler = logging.StreamHandler()
        stream_handler.setLevel(level)
        stream_handler.setFormatter(formatter)
        handlers = [ stream_handler ]
    else:
        handlers = []

    remove_handler = None

    if os.path.exists(f"{HOME}/rohanGit/{TARGET}"):

        LOGFILE = f"{HOME}/rohanGit/{TARGET}/mm.log"

        handler = logging.handlers.RotatingFileHandler(
            LOGFILE, maxBytes=(1048576*5), backupCount=7
        )
        formatter = logging.Formatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s")
        handler.setFormatter(formatter)
        handler.setLevel(logging.INFO)
        handlers.append(handler)

        LOGFILE = f"{HOME}/rohanGit/{TARGET}/mmcmd.log"

        handler2 = logging.handlers.RotatingFileHandler(
            LOGFILE, maxBytes=(1048576*5), backupCount=7
        )
        formatter = logging.Formatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s")
        handler2.setFormatter(formatter)
        handler2.setLevel(logging.INFO)
        handlers.append(handler2)
        remove_handler = handler2


    logger.setLevel(level)
    for hand in handlers:
        logger.addHandler(hand)

    #meter = Gen5Meter.SSHGen5Meter(hostname, logger)
    print(f"MeterMan using meter: {args.target}")

    if args.dut_db:
        wrapper(curses_run, args, unknown)
    else:
        mgr = mm.MeterMan(args.target, logger)

        DEBUG("command: %s", ' '.join(all_args))
        if remove_handler:
            logger.removeHandler(remove_handler)
        try:
            args.func.run_command(mgr, args, unknown)
        except Exception as e:
            import traceback
            print(e)
            traceback.print_exc()



if __name__=='__main__':
    main()
