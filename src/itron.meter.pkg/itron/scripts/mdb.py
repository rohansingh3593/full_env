#!/usr/bin/env python3

from kaizenbot.kbotdbclient_psql import _KBotDBClient_psql
from rohan.meter.MeterDB import MeterDB
from concurrent.futures import ThreadPoolExecutor, as_completed
import argparse
import abc
import sys
import os
import json
import yaml
import subprocess
import datetime
import time
import psycopg2

def run_query(query):
    resources =_KBotDBClient_psql('kaizenbot.rohan.com:5432', "kgodwin", 'AppServe', 'appservschema')

def getDuration(then, now = datetime.datetime.now(tz=datetime.timezone.utc), interval = "default"):

    # Returns a duration as specified by variable interval
    # Functions, except totalDuration, returns [quotient, remainder]

    duration =  now - then # For build-in functions
    duration_in_s = duration.total_seconds()

    def years():
      return divmod(duration_in_s, 31536000) # Seconds in a year=31536000.

    def days(seconds = None):
      return divmod(seconds if seconds != None else duration_in_s, 86400) # Seconds in a day = 86400

    def hours(seconds = None):
      return divmod(seconds if seconds != None else duration_in_s, 3600) # Seconds in an hour = 3600

    def minutes(seconds = None):
      return divmod(seconds if seconds != None else duration_in_s, 60) # Seconds in a minute = 60

    def seconds(seconds = None):
      if seconds != None:
        return divmod(seconds, 1)
      return duration_in_s

    def totalDuration():
        y = years()
        d = days(y[1]) # Use remainder to calculate next variable
        h = hours(d[1])
        m = minutes(h[1])
        s = seconds(m[1])

        dif=''
        if y[0]:
            dif += str(y[0]) + " years "
        if d[0]:
            dif += str(d[0]) + " days "
        if h[0]:
            dif += str(h[0]) + " hours "
        if m[0]:
            dif += str(m[0]) + " minutes "
        if s[0]:
            dif += str(s[0]) + "s"

        return dif
        #"Time between dates: {} years, {} days, {} hours, {} minutes and {} seconds".format(int(y[0]), int(d[0]), int(h[0]), int(m[0]), int(s[0]))

    return {
        'years': int(years()[0]),
        'days': int(days()[0]),
        'hours': int(hours()[0]),
        'minutes': int(minutes()[0]),
        'seconds': int(seconds()),
        'default': totalDuration()
    }[interval]

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

class cmd_listmeters(CommandEntry):
    def __init__(self):
        name = type(self).__name__.split('_')[1]
        super().__init__(name, "show a list of meters")

    def run_command(self,mgr,args, unknown):
        mw = [MeterWrapper(db) for db in mgr.get_meters(node_status='%')]
        for x in mw:
            print(x)

    def add_parameters(self, parser):
        #generic_parameters(parser)
        pass

class MeterWrapper():
    def __init__(self, db):
        #{'NODE_IP': '10.176.100.139', 'PLATFORM_ID': 'fwdevops_kgodwin',
        # 'NODE_STATUS': 'active', 'NODE_BUSY': 'no', 'BUSY_CHANGE_COUNT': 659,
        # 'LAST_BUSY_CHANGE': datetime.datetime(2022, 12, 1, 23, 57, 41, 459249,
        # tzinfo=psycopg2.tz.FixedOffsetTimezone(offset=0, name=None)), 'DNS_NAME': '6600283D97.rohan.com', 'PEER_GROUP': 'groupb', 'LOCK_HOST': 'kg-di-dev.kghome'}
        self.db = db
        self.ip = db.info['NODE_IP']
        self.last_time = db.info['LAST_BUSY_CHANGE']
        self.active = db.info['NODE_STATUS']
        self.peer_group = db.info['PEER_GROUP'] if db.info['PEER_GROUP'] else ''
        self.status = "idle" if db.info['NODE_BUSY'] == 'no' else f"locked by {db.info['LOCK_HOST']}"
        self.locked = db.info['NODE_BUSY'] == 'yes'
        self.hostname = db.info['LOCK_HOST']

    @property
    def duration(self):
        start = self.last_time
        start.replace(tzinfo=None)
        duration = getDuration(start)
        return duration

    def __str__(self):
        group = '' if not self.peer_group else self.peer_group+":"
        str = '{group}{addr:20s} {active:8s}  {status}  {duration}'.format(addr=self.ip, active = self.active, status = self.status, duration=self.duration, group=group )
        return str



class cmd_locks(CommandEntry):
    def __init__(self):
        name = type(self).__name__.split('_')[1]
        super().__init__(name, "show lock state for meters")

    def run_command(self,mgr,args, unknown):
        # decode the db entries
        mw = [MeterWrapper(db) for db in mgr.get_meters()]
        mw = sorted(mw, key=lambda x: x.peer_group)

        for entry in mw:
            print(str(entry))

    def add_parameters(self, parser):
        #generic_parameters(parser)
        pass


class cmd_dump(CommandEntry):
    def __init__(self):
        name = type(self).__name__.split('_')[1]
        super().__init__(name, "dump database")

    def run_command(self,mgr,args, unknown):
        data = mgr.dump()
        print(data)

    def add_parameters(self, parser):
        #generic_parameters(parser)
        pass

class cmd_listdb(CommandEntry):
    def __init__(self):
        name = type(self).__name__.split('_')[1]
        super().__init__(name, "show a list of known databases")

    def run_command(self,mgr,args, unknown):
        info = mgr.get_dbinfo()
        print(yaml.dump(info, indent=4))

    def add_parameters(self, parser):
        #generic_parameters(parser)
        pass

class cmd_lock(CommandEntry):
    def __init__(self):
        name = type(self).__name__.split('_')[1]
        super().__init__(name, "show a list of known databases")

    def run_command(self,mgr,args, unknown):
        print("nodes: ", unknown)
        for node in unknown:
            try:
                # tell manager to lock, but don't track lock and unlock on exit
                if mgr.lock_node(node, track=False):
                    print("%s locked" %( node))
            except Exception as e:
                print("error locking ", node, e)

    def add_parameters(self, parser):
        #generic_parameters(parser)
        pass


class cmd_sql(CommandEntry):
    def __init__(self):
        name = type(self).__name__.split('_')[1]
        super().__init__(name, "execute sql query on selected db")

    def run_command(self,mgr,args, unknown):
        query = ' '.join(unknown)
        print("Query: ", query)
        data = mgr.execute_sql(query)
        print(yaml.dump(data, indent=4))

    def add_parameters(self, parser):
        #generic_parameters(parser)
        pass

class cmd_unlock(CommandEntry):
    def __init__(self):
        name = type(self).__name__.split('_')[1]
        super().__init__(name, "unlock nodes")

    def run_command(self,mgr,args, unknown):
        print("nodes: ", unknown)
        for node in unknown:
            try:
                data = mgr.force_unlock_node(node)
            except Exception as e:
                print("error unlocking ", node, e)

    def add_parameters(self, parser):
        #generic_parameters(parser)
        pass


class cmd_resetlocks(CommandEntry):
    def __init__(self):
        name = type(self).__name__.split('_')[1]
        super().__init__(name, "reset locks on all nodes locked by this host")

    def run_command(self,mgr,args, unknown):
        mw = [MeterWrapper(db) for db in mgr.get_meters()]
        hostname = os.uname()[1]
        print(args)
        locks = 0; skip = 0
        for node in mw:
            try:
                if node.locked:
                    if args.all_hosts or node.hostname == hostname:
                        locks += 1
                        mgr.force_unlock_node(node.ip)
                        print("unlocked %s, locked by %s for %s" % (node, node.hostname, node.duration ))
                    else:
                        print("Skipping %s (owned by %s)" %(node, node.hostname))
                        skip += 1
            except Exception as e:
                print("error unlocking ", node, e)

        if not locks:
            print('everything is unlocked already')
        if skip:
            print("%s skipped, use --all_hosts to unlock these" % (skip))

    def add_parameters(self, parser):
        parser.add_argument('--all_hosts', action='store_true', default=None)
        pass

class cmd_deactivate(CommandEntry):
    def __init__(self):
        name = type(self).__name__.split('_')[1]
        super().__init__(name, "take meter out of service")

    def run_command(self,mgr,args, unknown):
        print("nodes: ", unknown)
        for node in unknown:
            try:
                data = mgr.deactivate(node)
            except Exception as e:
                print("error deactivating ", node, e)

    def add_parameters(self, parser):
        #generic_parameters(parser)
        pass

class cmd_activate(CommandEntry):
    def __init__(self):
        name = type(self).__name__.split('_')[1]
        super().__init__(name, "put meter in service")

    def run_command(self,mgr,args, unknown):
        print("nodes: ", unknown)
        for node in unknown:
            try:
                data = mgr.activate(node)
            except Exception as e:
                print("error activating ", node, e)

    def add_parameters(self, parser):
        #generic_parameters(parser)
        pass

class cmd_validate(CommandEntry):
    def __init__(self):
        name = type(self).__name__.split('_')[1]
        super().__init__(name, "check dns and ip addresses of meters")

    def ping_meter(self, meter):
        node = meter.info
        try:

            cmd = f"dig +short {node['DNS_NAME']}"
            ip = subprocess.check_output(cmd.split(' '))
            ip = ip.decode('utf-8').strip()
            if ip:
                if ip != node['NODE_IP']:
                    print(cmd, ip)
                    print("Error, node %s should be %s" %(node['DNS_NAME'],ip))
                    #mgr.update_ip(node['NODE_IP'])
                else:
                    dns = 'dns valid'
            else:
                dns = 'missing'
        except:
            dns = 'failed'

        cmd = f"ping -n -c2 {meter}"

        try:
            ip = subprocess.check_output(cmd.split(' ')).decode('utf-8')
        except:
            return [meter, dns, "failed ping"]
        results = ip.split('\n')
        return [meter, dns, results[-3]]

    def run_command(self,mgr,args, unknown):
        meters = mgr.get_meters()
        results = {}
        if args.ping:
            with ThreadPoolExecutor(len(meters)) as executor:
                futures = {executor.submit(self.ping_meter, meters[i]) for i in range(len(meters))}

                for fut in as_completed(futures):
                    meter, dns, ping = fut.result()
                    node = meter.info
                    #print(f"The gen outcome is {res}")
                    results[meter.ip_address] = ping
                    print("%-15s %-25s %-10s %s \n" %( node['NODE_IP'], node['DNS_NAME'],dns,ping))

        for n in meters:
            try:
                node = n.info


            except Exception as e:
                print("error validating ", node, e)

    def add_parameters(self, parser):
        parser.add_argument('--ping', action='store_true', default=None)
        pass


def main():
    all_args = sys.argv
    parent_parser = argparse.ArgumentParser(description='Meter Manager - manage meter attached to SSH')
    parent_parser.add_argument('-d', '--debug', action='store_true', help="set log level to debug")
    parent_parser.add_argument('--mdb_version', action='version', version='%(prog)s 2.0')

    def_dut_db = os.getenv("PYTEST_DUT_DB")
    parent_parser.add_argument('--dut-db', type=str, default=def_dut_db if def_dut_db else "kaizenbot.rohan.com:5432")

    parser = argparse.ArgumentParser(description='Meter Database Manager - manage meter database')
    # create sub-parser for all commands
    subparsers = parser.add_subparsers(dest='command', title="operation", description="Select the appropriate command below.  Use command followed by --help for options related to that command",help="Operation")
    commands = [
        cmd_listdb(),
        cmd_listmeters(),
        cmd_dump(),
        cmd_sql(),
        cmd_lock(),
        cmd_locks(),
        cmd_unlock(),
        cmd_resetlocks(),
        cmd_deactivate(),
        cmd_activate(),
        cmd_validate(),
    ]

    for item in commands:
        item.add_parser(subparsers, parent_parser)

    if len(all_args) == 1:
        print(parser.format_help())
        exit(0)

    # standard options for all commands
    args,unknown = parser.parse_known_args()
    try:
        mgr=MeterDB(args.dut_db)
        args.func.run_command(mgr, args, unknown)
    except Exception as e:
        print(e)


if __name__=='__main__':
    main()


