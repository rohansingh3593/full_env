"""
Credit from https://github.com/browsertron/pytest-parallel

KEG: refactored workers and threads_per_worker to
multi-thread worker for non-meter tests (cpus parameter to specify number of threads)
multi-threaded worker (meters paramter to specify specific meter IP addresses)

Segregates tests with the 'meter' fixture to the meter thread and all others to the cpu thread

This allows multiple meters and tests to run simultaniously

"""
import os
import py
import sys
import time
import pytest
import _pytest
import threading
import json
import multiprocessing
from queue import Empty
from tblib import pickling_support
from multiprocessing import Manager, Process, Event
from rohan.meter.MeterInstance import MeterInstanceUser,MeterInstanceDB
import logging
from contextlib import ExitStack,contextmanager
import atexit
from _pytest._io import TerminalWriter
from  rohan.meter.MeterDB import MeterDB,MeterDBBase
import random
import signal
import rpyc
from rpyc.utils.server import ThreadedServer
from logging.handlers import QueueHandler, QueueListener
import pickle

LOGGER = logging.getLogger(__name__)

# In Python 3.8 and later, the default on macOS is spawn.
# We force forking behavior at the expense of safety.
#
# "On macOS, the spawn start method is now the default. The fork start method should be
#  considered unsafe as it can lead to crashes of the subprocess. See bpo-33725."
#
# https://docs.python.org/3/library/multiprocessing.html#contexts-and-start-methods
if sys.platform.startswith('darwin'):
    multiprocessing.set_start_method('fork')

__version__ = '0.9.9'
workdir_key = pytest.StashKey[str]()

def parse_config(config, name):
    return getattr(config.option, name, config.getini(name))

log_dir = "."
lock_timeout=60

def pytest_addoption(parser):
    tests_per_worker_help = ('Set the max num of concurrent tests for each '
                             'worker (int or "auto" - split evenly)')
    meters_help = ('physical meters to be used for testing.  tests are split up to a specific meter if they have a meter parameter')
    dutdb_help = ('database to provide meters to use.  usage: --meter-db={server address|sqlite file},{args}.  args is a list of qualifications for the meters to choose')
    multi_help = ( 'database of meters for co-located communication testing.  Same format as dut-db option.')
    meter_pairs_help = ('list if physical meter IP addresses of meters to be used for peer-to-peer testing')
    lock_help = ('timeout (in seconds) for locking meter database.  currently lock is 12 hours')

    group = parser.getgroup('pytest-parallel')
    group.addoption(
        '--cpus',
        dest='cpus',
        help=tests_per_worker_help
    )
    group.addoption(
        '--meters',
        dest='meters',
        help=meters_help
    )
    group.addoption(
        '--meter-pairs',
        dest='meter-pairs',
        help=meters_help
    )
    group.addoption(
        '--dut-db',
        dest='dut-db',
        #envvar='PYTEST_DUT_DB',
        help=dutdb_help
    )
    group.addoption(
        '--multi-db',
        dest='multi-db',
        #env_var='PYTEST_MULTI_DB',
        help=multi_help
    )
    group.addoption(
        '--lock_timeout',
        dest='lock_timeout',
        help=lock_help
    )

    parser.addini('meters', meters_help)
    parser.addini('meter-pairs', meter_pairs_help)
    parser.addini('cpus', tests_per_worker_help)
    parser.addini('dut-db', dutdb_help)
    parser.addini('multi-db', multi_help)
    parser.addini('lock_timeout', lock_help)


def run_test(session, item, nextitem):
    LOGGER.debug("Running test %s",item.nodeid)
    item.ihook.pytest_runtest_protocol(item=item, nextitem=nextitem)
    if session.shouldstop:
        raise session.Interrupted(session.shouldstop)


def process_with_threads(config, queue,  log_queue, session, tests_per_worker, errors):
    # This function will be called from subprocesses, forked from the main
    # pytest process. First thing we need to do is to change config's value
    # so we know we are running as a worker.
    config.parallel_worker = True

    root = logging.getLogger()
    handler = QueueHandler(log_queue)
    root.addHandler(handler)
    LOGGER.addHandler(handler)

    threads = []
    for v in range(tests_per_worker):
        thread = ThreadWorkerBase(queue,  log_queue, session, errors, f"cpu{v}-thread")
        thread.start()
        threads.append(thread)
    [t.join() for t in threads]


def process_with_multi_meters(config, queue,  log_queue, session, multi_meters, server_port,  errors):
    # This function will be called from subprocesses, forked from the main
    # pytest process. First thing we need to do is to change config's value
    # so we know we are running as a worker.
    config.parallel_worker = True

    root = logging.getLogger()
    handler = QueueHandler(log_queue)
    root.addHandler(handler)

    # connect to the server managing the locks
    lock_server = rpyc.connect(host='localhost', port=server_port)
    multi_meters = [ [RemoteMeter(meter.info, lock_server) if isinstance(meter, MeterInstanceDB) else meter for meter in meters] for meters in multi_meters]

    v = multi_meters[0]
    name = f"multi_meter{v}-thread"
    threading.current_thread().setName(name)

    os.environ["PYTEST_XDIST_MULTI_METER_TARGET"] = ','.join([m.ip_address for m in v])

    try:
        task = ThreadWorkerMultiMeter(queue,  log_queue, session, errors, name, v)
        task.run()
    finally:
        root.removeHandler(handler)
        multi_meters = None
        lock_server.close()

def set_log_dir(dir):
    global log_dir
    log_dir = dir

def process_with_meters(config, queue,  log_queue,  session, meters, server_port, errors):
    # This function will be called from subprocesses, forked from the main
    # pytest process. First thing we need to do is to change config's value
    # so we know we are running as a worker.
    config.parallel_worker = True

    lock_server = rpyc.connect(host='localhost', port=server_port)

    root = logging.getLogger()
    handler = QueueHandler(log_queue)
    root.addHandler(handler)

    # convert db meters to remote db meters
    meters = [RemoteMeter(meter.info, lock_server) if isinstance(meter, MeterInstanceDB) else meter for meter in meters]

    meter = meters[0]

    os.environ["PYTEST_XDIST_METER_TARGET"] = meter.ip_address

    meters = None
    name = f"meter-{meter.ip_address}-thread"
    threading.current_thread().setName(name)

    try:
        task = ThreadWorkerMeter( queue,  log_queue,  session, errors, name, meter=meter)
        task.run()
    finally:
        root.removeHandler(handler)
        del meter
        lock_server.close()


class ThreadWorkerBase(threading.Thread):
    def __init__(self, queue,  log_queue, session, errors, name):
        super().__init__(name=name)
        self.queue = queue
        self.session = session
        self.errors = errors
        log = logging.getLogger()
        global log_dir

        fname = "".join(x if x.isalnum() else '_' for x in name)
        fname =os.path.join(log_dir, fname + ".log")

        fileh = logging.FileHandler(fname)
        #fileh.setFormatter(logging.Formatter('%(asctime)s %(levelname)-8s (%(name)s:%(filename)s:%(lineno)s) %(message)s'))
        fileh.setLevel(logging.DEBUG)
        log.addHandler(fileh)

    def process_item(self,item, index):
        try:
            run_test(self.session, item, None)
        except BaseException:
            import pickle
            import sys

            self.errors.put((self.name, pickle.dumps(sys.exc_info())))
        finally:
            try:
                self.queue.task_done()
            except ConnectionRefusedError:
                pass

    def prepare_args(self, item, index):
        pass

    def run(self):
        pickling_support.install()
        locked = False

        while True:
            try:
                index = self.queue.get(block=False)
            except ConnectionRefusedError:
                time.sleep(.1)
                continue
            except Empty:
                break

            item = self.session.items[index]
            self.prepare_args(item, index)
            self.process_item(item, index)


class ThreadWorkerMeter(ThreadWorkerBase):
    def __init__(self, queue,  log_queue, session, errors, name, meter):
        self.meter = meter
        self.meter_exit_handler = None
        global lock_timeout
        self.lock_timeout = lock_timeout
        super().__init__(queue,  log_queue, session, errors, name)

    def prepare_args(self, item, index):
        LOGGER.debug("Meter: %s processing index %s", self.meter, index)
        if self.meter_exit_handler:
            item.funcargs['meter_exit_handler'] = self.meter_exit_handler


    def run(self):
        pickling_support.install()
        timeout = time.time()+self.lock_timeout
        try:
            while not self.queue.empty():
                locked = self.meter.lock()
                if not locked:
                    if timeout < time.time():
                        LOGGER.error("Meter lock timeout")
                        raise ValueError("Meter lock timeout.  use --lock_timeout option to extend timeout from 1 minute")
                    LOGGER.info("Meter locked.  Waiting")
                    time.sleep(random.randint(50,70))
                else:
                    self.meter_exit_handler = MeterExitHandler(self.meter)
                    break

            super().run()

            if self.meter_exit_handler:
                self.meter_exit_handler.shutdown()
                self.meter_exit_handler = None
        except BaseException:
            self.errors.put((self.name, pickle.dumps(sys.exc_info())))
            raise

class ThreadWorkerMultiMeter(ThreadWorkerBase):
    def __init__(self, queue,  log_queue, session, errors, name, multi_meter):
        self.multi_meter = multi_meter
        self.meter_exit_handler = None
        global lock_timeout
        self.lock_timeout = lock_timeout
        super().__init__(queue,  log_queue, session, errors, name)

    def prepare_args(self, item, index):
        LOGGER.info("Multi-Meter: %s processing index %s", self.multi_meter, index)

        if self.meter_exit_handler:
            item.funcargs['meter_exit_handler'] = self.meter_exit_handler

    def run(self):
        pickling_support.install()
        try:
            self.meter_exit_handler = []
            timeout = time.time()+self.lock_timeout

            while not self.queue.empty():
                locks = [m for m in self.multi_meter if m.lock()]
                locked = len(locks) == len(self.multi_meter)
                if not locked:
                    # unlock and try again later
                    for x in locks:
                        x.unlock()
                    if timeout < time.time():
                        LOGGER.error("Meter lock timeout")
                        raise ValueError("Meter lock timeout.  use --lock_timeout option to extend timeout from 1 minute")
                    LOGGER.info("Meters locked.  Waiting")
                    time.sleep(random.randint(50,70))
                else:
                    self.meter_exit_handler = [MeterExitHandler(meter) for meter in self.multi_meter]
                    break

            try:
                super().run()
            except Exception as e:
                LOGGER.exception("test case exception")
                raise

            for handler in self.meter_exit_handler:
                handler.shutdown()
            self.meter_exit_handler = None
        except BaseException:
            self.errors.put((self.name, pickle.dumps(sys.exc_info())))
            raise


class MeterExitHandler:
    """ class to notify callers that the meter is going away

        this would probably be used to collect information that
        could be lost after the meter lock has been release, like
        code coverage data, etc.

    """
    def __init__(self,  meter):
        self.callbacks = {}
        self.meter = meter

    def get_handler_by_name(self, name):
        return self.callbacks.get(name, None)

    def register_exit_handler_by_name(self, name, fnc):
        oldone = None
        if name in self.callbacks:
            LOGGER.info("Registering multipe cleanup callbacks!!!! probably should not do this")
            oldone = self.callbacks[name]

        self.callbacks[name] = fnc
        LOGGER.info("Registered %s callbacks for meter %s", len(self.callbacks), self.meter)
        return oldone

    def shutdown(self):
        while len(self.callbacks) > 0:
            try:
                x = next(iter(self.callbacks))
                fnc = self.callbacks.pop(x)
                fnc(self.meter)
            except Exception as e:
                LOGGER.exception(e)

        self.meter.unlock()
        self.callbacks = None

class RemoteDBServer(rpyc.Service):
    def __init__(self, meterdb):
        self.db = meterdb
        self.locks = []
        path = os.getenv("BUILD_ARTIFACTSTAGINGDIRECTORY", ".")
        self.filename = os.path.join(path, "parallel.locks.json")
        rpyc.service.__init__(self)

    def update_locks(self):
        LOGGER.info("Updated parallel.locks.json with %s", self.locks)
        with open(self.filename, "w") as fh:
            json.dump(self.locks, fh, indent=4)

    @rpyc.exposed
    def lock_node(self, node):
        locked = self.db.lock_node(node)
        if locked:
            self.locks.append(str(node))
            self.update_locks()
        return locked

    @rpyc.exposed
    def unlock_node(self, node):
        self.locks.remove(str(node))
        ret = self.db.unlock_node(node)
        self.update_locks()

class RPCThreadedServer(ThreadedServer):
    ''' an RPC server that adds a wait_up method that
        waits for the server to start listening.  This is required
        to avoid a client from trying to connect to the server
        before the server has initialized and causes a connect timeout
    '''
    def __init__(self, *args, **kwargs):
        self.start_event = Event()
        ThreadedServer.__init__(self, *args, **kwargs)

    def _listen(self):
        if self.active:
            return
        super(ThreadedServer, self)._listen()
        self.start_event.set()

    def wait_up(self):
        self.start_event.wait()

class RemoteDBServerThread():
    def __init__(self, db, name):
        self.object = RemoteDBServer(db)
        config = rpyc.core.protocol.DEFAULT_CONFIG.copy()
        config['allow_all_attrs'] = True
        config['allow_exposed_attrs'] = False
        self.server = RPCThreadedServer(self.object, protocol_config=config, logger=LOGGER)
        self.thread = threading.Thread(target=self.server.start)
        self.thread.start()
        self.server.wait_up()
        self.port = self.server.port

    def __enter__(self):
        return self

    def __exit__(self, type, value, traceback):
        self.server.close()

    def close(self):
        self.server.close()


class RemoteMeter(MeterInstanceDB):
    def __init__(self, info, server):
        """ This is a class to hold all of the information about a meter
        that was selected from the database """
        self.server = server
        self.parent_db = self.server.root
        self.info = info
        self.locked = False

@pytest.mark.trylast
def pytest_configure(config):
    pid = os.getpid()
    with open('pytest.pid','w') as fh:
        fh.write(str(pid) + '\n')
    meters = parse_config(config, 'meters')
    meter_pairs = parse_config(config, 'meter-pairs')
    dut_db = parse_config(config, 'dut-db')
    multi_db = parse_config(config, 'multi-db')
    global lock_timeout
    if parse_config(config, "lock_timeout"):
        lock_timeout = int(parse_config(config, "lock_timeout"))

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
        multi_dbclient = MeterDB(multi_db, LOGGER)
        setattr(config, 'mdb_client', multi_dbclient)

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
                        LOGGER.warning("rejecting single meter in PEER_GROUP.  there needs to be at least two meters in a group")
                current_list = [x]
            last_peer = x.info['PEER_GROUP']

        if len(current_list) > 1:
            multi_meters.append(current_list)

        if not multi_meters:
            LOGGER.error("No meters are marked available in database to run tests. use option 'release=true' to force-release meters")
            raise ValueError("parallel.py: No meters could be locked for MULTI-DB=%s",multi_db)


    else:
        if meter_pairs:
            multi_meters = [[ MeterInstanceUser(x) for x in meter_pairs.split(',') ]]

    setattr(config, 'multi_meters', multi_meters)
    setattr(config, 'mdb_client', multi_dbclient) # used for cleanup

    dbclient = None
    if dut_db:
        assert meters is None, "Can't specify a DUT database and manual meter list (--meters)"
        dbclient = MeterDB(dut_db, LOGGER)
        setattr(config, 'db_client', dbclient)

        meters = dbclient.get_meters()
        if not meters:
            LOGGER.error("No meters are marked available in database to run tests. use option 'release=true' to force-release meters")
            raise ValueError("parallel.py: No meters could be locked for DUT-DB=%s",dut_db)

    else:
        if meters:
            meters = [MeterInstanceUser(x) for x in meters.split(',')]
    setattr(config, 'db_client', dbclient)


    setattr(config.option, 'meters', meters)

    count = len(meters) if meters else 0
    count += len(multi_meters) if multi_meters and len(multi_meters[0]) else 0
    count += int(tests_per_worker) if tests_per_worker else 0

    # do not install if this is an xdist worker
    worker = getattr(config, 'workerinput', None)

    if not config.option.collectonly and count and getattr(config.option,'numprocesses',None) == None and not worker:
        config.pluginmanager.register(ParallelRunner(config, dbclient, multi_dbclient), 'parallelrunner')

class ThreadLocalEnviron(os._Environ):
    def __init__(self, env):
        if sys.version_info >= (3, 9):
            super().__init__(
                env._data,
                env.encodekey,
                env.decodekey,
                env.encodevalue,
                env.decodevalue,
            )
            self.putenv = os.putenv
            self.unsetenv = os.unsetenv
        else:
            super().__init__(
                env._data,
                env.encodekey,
                env.decodekey,
                env.encodevalue,
                env.decodevalue,
                env.putenv,
                env.unsetenv
            )
        if hasattr(env, 'thread_store'):
            self.thread_store = env.thread_store
        else:
            self.thread_store = threading.local()

    def __getitem__(self, key):
        if key == 'PYTEST_CURRENT_TEST':
            if hasattr(self.thread_store, key):
                value = getattr(self.thread_store, key)
                return self.decodevalue(value)
            else:
                raise KeyError(key) from None
        return super().__getitem__(key)

    def __setitem__(self, key, value):
        if key == 'PYTEST_CURRENT_TEST':
            value = self.encodevalue(value)
            self.putenv(self.encodekey(key), value)
            setattr(self.thread_store, key, value)
        else:
            super().__setitem__(key, value)

    def __delitem__(self, key):
        if key == 'PYTEST_CURRENT_TEST':
            self.unsetenv(self.encodekey(key))
            if hasattr(self.thread_store, key):
                delattr(self.thread_store, key)
            else:
                raise KeyError(key) from None
        else:
            super().__delitem__(key)

    def __iter__(self):
        if hasattr(self.thread_store, 'PYTEST_CURRENT_TEST'):
            yield 'PYTEST_CURRENT_TEST'
        keys = list(self._data)
        for key in keys:
            yield self.decodekey(key)

    def __len__(self):
        return len(self.thread_store.__dict__) + len(self._data)

    def copy(self):
        return type(self)(self)


class ThreadLocalSetupState(threading.local, _pytest.runner.SetupState):
    def __init__(self):
        super(ThreadLocalSetupState, self).__init__()


class ThreadLocalFixtureDef(threading.local, _pytest.fixtures.FixtureDef):
    def __init__(self, *args, **kwargs):
        super(ThreadLocalFixtureDef, self).__init__(*args, **kwargs)


class ParallelRunner(object):
    def __init__(self, config, dbclient, multi_dbclient):
        self._config = config
        self.started = None
        self.dbclient = dbclient
        self.multi_dbclient = multi_dbclient
        self.exitstatus = None

        reporter = config.pluginmanager.getplugin('terminalreporter')

        # prevent mangling the output
        reporter.showfspath = False
        reporter._show_progress_info = False

        # get the number of workers
        meters = parse_config(config, 'meters')
        self.multi_meters = getattr(config, 'multi_meters', None)
        workers = 1
        if meters:
            workers = workers + 1
        if self.multi_meters:
            workers = workers + 1

        self.workers = workers
        self.meters = meters
        atexit.register(self.cleanup)

    @pytest.mark.tryfirst
    def pytest_sessionstart(self, session):
        # make the session threadsafe
        _pytest.runner.SetupState = ThreadLocalSetupState

        # ensure that the fixtures (specifically finalizers) are threadsafe
        _pytest.fixtures.FixtureDef = ThreadLocalFixtureDef

        # make the environment threadsafe
        os.environ = ThreadLocalEnviron(os.environ)

    def sighup(self, signum, frame):
        LOGGER.debug("Received signal %s", signum)
        raise KeyboardInterrupt()

    def execute(self, session, cpus, errors, manager, pes, log_queue):
        # segregate jobs by meter requirements
        idx=0
        config_errors = 0
        queue_nometer = pes.enter_context(self.queue_wrapper(manager, 'queue_nometer'))
        queue_meter = None
        queue_multimeter = None


        for i in session.items:
            parallel = [mark for mark in i.iter_markers(name="parallel")]
            multi = {'multi_meter','preinstalled_multi_meter'} & set(i.fixturenames)

            if multi:
                num_meters = parallel[0].kwargs.get('p2p_meters', None) if parallel else None
                if num_meters is None:
                    LOGGER.error("""Test case %s is using multple meters,
but does not have '@pytest.mark.parallel(p2p_meters={meters})` mark""",session.items[idx].nodeid)
                    config_errors += 1
                if queue_multimeter is None:
                    queue_multimeter = pes.enter_context(self.queue_wrapper(manager, 'queue_multimeter'))
                queue_multimeter.put(idx)
            else:
                union = {'meter','preinstalled_meter','meter_db'} & set(i.fixturenames)
                if union:
                    if queue_meter is None:
                        queue_meter = pes.enter_context(self.queue_wrapper(manager, 'queue_meter'))
                    queue_meter.put(idx)
                else:
                    queue_nometer.put(idx)
            idx=idx+1

        if config_errors:
            raise session.Interrupted(
                "%d error%s during collection"
                % (config_errors, "s" if config_errors != 1 else "")
                )

        self.responses_queue = pes.enter_context(self.queue_wrapper(manager, 'responses_queue'))

        @contextmanager
        def run_for_responses_processor(queue):
            try:
                thread = threading.Thread(
                    target=self.process_responses,
                    args=(queue,),
                    name="responses_processor"
                )
                thread.daemon = True
                thread.start()
                yield thread
            finally:
                queue.put(('quit', {}))
                thread.join()

        pes.enter_context(run_for_responses_processor(self.responses_queue))

        # Current process is not a worker.
        # This flag will be changed after the worker's fork.
        self._config.parallel_worker = False

        with ExitStack() as es:
            # start nometer worker
            args = (self._config, queue_nometer, log_queue, session, cpus, errors)
            process_nometer = Process(target=process_with_threads, args=args, name="no_meter_exec")
            process_nometer.start()

            processes = []
            multi_process = []
            mserver = None
            mmserver = None

            all_multi = {e.ip_address for l in self.multi_meters for e in l } if self.multi_meters else {}
            if self.meters:
                reg_meters = {e.ip_address for e in self.meters}
            else:
                reg_meters = set()

            # are there any common meters?
            common_meters = reg_meters.intersection(all_multi)

            if queue_meter:
                assert self.meters, "you must specify --meters or --dut-db.  There are selected tests that need meters"
                mserver = es.enter_context(RemoteDBServerThread(self.dbclient, "single-meter-db-server"))
                for meter in self.meters:
                    args = (self._config, queue_meter, log_queue, session, [meter], mserver.port, errors)
                    process = Process(target=process_with_meters, args=args,name=f"MeterProcess-{meter.ip_address}")
                    processes.append(process)

            if queue_multimeter:
                assert self.multi_meters, "you must specify --multi-db.  There are selected tests that need meters"
                mmserver = es.enter_context(RemoteDBServerThread(self.multi_dbclient, "multi-meter-db-server"))
                for meters in self.multi_meters:
                    args = (self._config, queue_multimeter, log_queue, session, [meters], mmserver.port, errors)
                    process = Process(target=process_with_multi_meters, args=args,name=f"MultiMeterProcess-{meters[0].ip_address}")
                    multi_process.append(process)

            # run in parallel if no common meters
            # flatten multimeters to set
            if not common_meters:
                processes.extend(multi_process)
                multi_process = []

            # start in parallel, then wait for them to complete
            try:
                oldsig = signal.signal(signal.SIGTERM, self.sighup)
                oldsig_hup = signal.signal(signal.SIGHUP, self.sighup)
                [p.start() for p in processes]
                self.started = processes
                for p in processes:
                    logging.info("Process pid %s started", p.pid)

                [p.join() for p in processes]
                self.started = None

                # if multi_process still set (serial run)
                if multi_process:
                    processes = multi_process
                    [p.start() for p in processes]
                    self.started = processes
                    [p.join() for p in processes]
                    self.started = None

                process_nometer.join()

            except KeyboardInterrupt:
                signal.signal(signal.SIGTERM, oldsig)
                signal.signal(signal.SIGHUP, oldsig_hup)
                self.terminate_children()

    @contextmanager
    def queue_wrapper(self, manager, name):
        try:
            queue = manager.JoinableQueue()
            yield queue
        finally:
            LOGGER.debug("Cleaning up joinable queue %s", name)
            while not queue.empty():
                queue.get_nowait()
                queue.task_done()

            queue.join()

    @contextmanager
    def queue_handler(self, log_queue):
        try:
            qhandler = QueueHandler(log_queue)
            logging.getLogger().addHandler(qhandler)
            yield qhandler
        finally:
            logging.getLogger().removeHandler(qhandler)
            qhandler.close()


    @contextmanager
    def queue_listener(self, log_queue, name):
        try:
            qlistener = QueueListener(log_queue)
            qlistener.start()
            yield qlistener
        finally:
            LOGGER.debug("Cleaning up %s", name)
            qlistener.stop()

    @contextmanager
    def manager_context(self):
        try:
            manager = Manager()
            yield manager

        finally:
            LOGGER.debug("Cleaning up Manager")
            manager.shutdown()

    def pytest_runtestloop(self, session):
        if (
            session.testsfailed
            and not session.config.option.continue_on_collection_errors
        ):
            raise session.Interrupted(
                "%d error%s during collection"
                % (session.testsfailed, "s" if session.testsfailed != 1 else "")
            )

        if session.config.option.collectonly:
            return True

        # get the number of tests per worker

        cpus = parse_config(session.config, 'cpus')
        if cpus:
            cpus=int(cpus)
        else:
            cpus=1

        if self.workers > 1:
            worker_noun, process_noun = ('workers', 'processes')
        else:
            worker_noun, process_noun = ('worker', 'process')

        if cpus > 1:
            test_noun, thread_noun = ('tests', 'threads')
        else:
            test_noun, thread_noun = ('test', 'thread')

        print('pytest-parallel: {} {} ({}), {} {} cpus ({})'
              .format(self.workers, worker_noun, process_noun,
                      cpus, test_noun, thread_noun))


        try:
            with ExitStack() as es:
                manager = es.enter_context(self.manager_context())

                log_queue = es.enter_context(self.queue_wrapper(manager, 'log_queue'))
                logserver = es.enter_context(self.queue_listener(log_queue, 'logserver'))

                es.enter_context(self.queue_handler(log_queue))
                errors = es.enter_context(self.queue_wrapper(manager, 'errors'))

                # Reports about tests will be gathered from workers
                # using this queue. Workers will push reports to the queue,
                # and a separate thread will rerun pytest_runtest_logreport
                # for them.
                # This way, report generators like JUnitXML will work as expected.

                self.execute(session, cpus, errors, manager, es, log_queue)

                if not errors.empty():
                    import six

                    self.exitstatus = 1
                    thread_name, errinfo = errors.get()
                    errors.task_done()
                    err = pickle.loads(errinfo)
                    err[1].__traceback__ = err[2]

                    exc = RuntimeError(
                        "pytest-parallel got {} errors, raising the first from {}."
                        .format(errors.qsize() + 1, thread_name)
                    )

                    six.raise_from(exc, err[1])

        finally:

            # cleanup references to the meters
            reg_meters = None
            all_multi = None
            common_meters = None
            self.meters = None
            self.multi_meters = None

            # clean up sql connection
            if self._config.db_client:
                self._config.db_client.close()
            if self._config.mdb_client:
                self._config.mdb_client.close()

            atexit.unregister(self.cleanup)

        return True

    def cleanup(self):
        if self.dbclient:
            self.dbclient.unlock_all()
        if self.multi_dbclient:
            self.multi_dbclient.unlock_all()

    def terminate_children(self):
        if self.started:
            try:
                if type(self.started) is list:
                    for p in self.started:
                        pid = p.pid
                        if pid:
                            os.kill(pid, signal.SIGINT)
                else:
                    pid = self.started
                    if pid:
                        os.kill(pid, signal.SIGINT)
            except Exception as e:
                LOGGER.exception("terminate_children failed")
                pass
            LOGGER.debug("terminate_children compete")
        self.started = None


    def send_response(self, event_name, **arguments):
        self.responses_queue.put((event_name, arguments))

    @pytest.hookimpl(hookwrapper=True)
    def pytest_runtest_makereport(self, item, call):
        #workdir = item.funcargs('workdir')
        res = yield
        if self._config.parallel_worker:
            if workdir_key in item.stash:
                workdir = item.stash[workdir_key]
                with open(os.path.join(workdir, 'results.log'), "a") as f:
                    #f.write(res._result.longreprtext)
                    res._result.toterminal(TerminalWriter(file=f))


    @pytest.hookimpl(tryfirst=True)
    def pytest_runtest_call(self, item):
        if 'workdir' in item.funcargs:
            workdir = item.funcargs['workdir']
            item.stash[workdir_key] = workdir

    def pytest_sessionfinish(self, session, exitstatus):
        if self.exitstatus is not None:
            session.exitstatus = self.exitstatus


    def pytest_runtest_logreport(self, report):
        # We want workers to report to it's master.
        # Without this "if", master will try to report to itself.
        if self._config.parallel_worker:
            data = self._config.hook.pytest_report_to_serializable(
                config=self._config, report=report
            )
            self.send_response('testreport', report=data)

    def on_testreport(self, report):
        report = self._config.hook.pytest_report_from_serializable(
            config=self._config, data=report
        )
        self._config.hook.pytest_runtest_logreport(report=report)

    def process_responses(self, queue):
        while True:
            try:
                event_name, kwargs = queue.get()
                if event_name == 'quit':
                    queue.task_done()
                    break
            except ConnectionRefusedError:
                time.sleep(.1)
                continue

            callback_name = 'on_' + event_name
            try:
                callback = getattr(self, callback_name)
                callback(**kwargs)
            except BaseException as e:
                self._log('Exception during calling callback %s: %s', callback_name)
                logging.exception('Exception {0}'.format(e))
            finally:
                try:
                    queue.task_done()
                except ConnectionRefusedError:
                    pass
