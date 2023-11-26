from datetime import timedelta
import os
from xdist.workermanage import parse_spec_config
from itron.meter.MeterInstance import MeterInstanceUser
from  itron.meter.MeterDB import MeterDB

def parse_config(config, name):
    return getattr(config.option, name, config.getini(name))

def parse_meter_options(config, logger):
    meters = parse_config(config, 'meters')
    meter_pairs = parse_config(config, 'meter_pairs')
    dut_db = parse_config(config, 'dut_db')
    multi_db = parse_config(config, 'multi_db')
    lock_timeout = None

    if parse_config(config, "lock_timeout"):
        val = str(parse_config(config, "lock_timeout"))
        if val.endswith('h'):
            lock_timeout = timedelta(hours=float(val[:-1])).total_seconds()
        elif val.endswith('m'):
            lock_timeout = timedelta(minutes=float(val[:-1])).total_seconds()
        elif val.endswith('s'):
            lock_timeout = timedelta(seconds=float(val[:-1])).total_seconds()
        else:
            lock_timeout = timedelta(seconds=float(val)).total_seconds()

    max_meters = int(parse_config(config, 'max_meters'))

    if not dut_db and not meters and os.getenv("PYTEST_DUT_DB"):
        dut_db = os.getenv("PYTEST_DUT_DB")

    if not dut_db and not meters and os.getenv("PYTEST_METERS"):
        meters = os.getenv("PYTEST_METERS")
        setattr(config.option, 'meters', meters)

    if not multi_db and not meter_pairs and os.getenv("PYTEST_MULTI_DB"):
        multi_db = os.getenv("PYTEST_MULTI_DB")

    tests_per_worker = parse_config(config, 'cpus')

    multi_meters = None
    multi_dbclient = None
    if multi_db:
        multi_dbclient = MeterDB(multi_db, logger)

        assert meter_pairs is None, "Can't specify a DUT database and manual meter pair list (--meter-pairs)"
        multi_meters = multi_dbclient.get_meters()

        # now, sort the list by group, then create two array of groups
        sorted_by_peer = sorted(multi_meters, key=lambda x: x.info['PEER_GROUP'] if x.info['PEER_GROUP'] else '')
        last_peer = None
        multi_meters=[]
        current_list=[]
        for x in sorted_by_peer:
            if x.info['PEER_GROUP'] == last_peer:
                current_list.append(x)
            else:
                if current_list:
                    if len(current_list) > 1:
                        multi_meters.append(current_list)
                    else:
                        logger.warning("rejecting single meter in PEER_GROUP.  there needs to be at least two meters in a group")
                current_list = [x]
            last_peer = x.info['PEER_GROUP']

        if len(current_list) > 1:
            multi_meters.append(current_list)

        if not multi_meters:
            logger.error("No meters are marked available in database to run tests. use option 'release=true' to force-release meters")
            raise ValueError("parallel.py: No meters could be locked for MULTI-DB=%s",multi_db)


    else:
        if meter_pairs:
            multi_meters = [[ MeterInstanceUser(x) for x in meter_pairs.split(',') ]]

    dbclient = None
    if dut_db:
        assert meters is None, "Can't specify a DUT database and manual meter list (--meters)"
        dbclient = MeterDB(dut_db, logger)
        setattr(config, 'db_client', dbclient)

        meters = dbclient.get_meters()
        if not meters:
            logger.error("No meters are marked available in database to run tests. use option 'release=true' to force-release meters")
            raise ValueError("parallel.py: No meters could be locked for DUT-DB=%s",dut_db)

    else:
        if meters:
            meters = [MeterInstanceUser(x) for x in meters.split(',')]

    options =  {
        'meters': meters,
        'multi_meters': multi_meters,
        'max_meters': max_meters
    }
    if lock_timeout:
        options['lock_timeout'] = lock_timeout

    return options


def add_meter_options(parser):
    group = parser.getgroup('xdist_meter')
    tests_per_worker_help = ('Set the max num of concurrent tests for each '
                             'worker (int or "auto" - split evenly)')
    meters_help = ('physical meters to be used for testing.  tests are split up to a specific meter if they have a meter parameter')
    dutdb_help = ('database to provide meters to use.  usage: --meter-db={server address|sqlite file},{args}.  args is a list of qualifications for the meters to choose')
    multi_help = ( 'database of meters for co-located communication testing.  Same format as dut-db option.')
    meter_pairs_help = ('list if physical meter IP addresses of meters to be used for peer-to-peer testing')
    lock_help = ('timeout (in seconds) for locking meter database.  currently lock is 12 hours')
    max_help = ('maximum number of meters that will be used by the session.  this allows two runs to share a larger group of meters.')

    group.addoption(
        '--cpus',
        dest='cpus',
        help=tests_per_worker_help
    )
    group.addoption(
        '--max-meters',
        dest='max_meters',
        default=999,
        help=max_help
    )
    group.addoption(
        '--meters',
        dest='meters',
        help=meters_help
    )
    group.addoption(
        '--meter-pairs',
        dest='meter_pairs',
        help=meters_help
    )
    group.addoption(
        '--dut-db',
        dest='dut_db',
        #envvar='PYTEST_DUT_DB',
        help=dutdb_help
    )
    group.addoption(
        '--multi-db',
        dest='multi_db',
        #env_var='PYTEST_MULTI_DB',
        help=multi_help
    )
    group.addoption(
        '--lock-timeout',
        type=str,
        dest='lock_timeout',
        help=lock_help
    )
    parser.addini('meters', meters_help)
    parser.addini('meter_pairs', meter_pairs_help)
    parser.addini('max_meters', max_help)
    parser.addini('cpus', tests_per_worker_help)
    parser.addini('dut_db', dutdb_help)
    parser.addini('multi_db', multi_help)
    parser.addini('lock_timeout', lock_help)
