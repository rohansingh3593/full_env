import pytest
import json
import time
import os
from xdist.workermanage import NodeManager
import random
from xdist.dsession import DSession
from itron.plugins.affinitysched import LoadAffinityScheduling

class MeterScheduler(LoadAffinityScheduling):
    def __init__(self, config, logger, lock_timeout, max_meters, log=None, meters=None, multi=None):
        self._meters = [m for m in meters] if meters else []
        self._multi_meters = [m for m in multi] if multi else []
        self.max_meters = max_meters
        self.new_nodes = []
        self.locked_meters = []
        self.locked_multi_meters = []
        self.lock_timeout = time.time()+lock_timeout
        self.lock_timeout_seconds = lock_timeout
        self.track_locks = []
        self.track_locks_multi = []
        self.logger = logger
        self.cur_locked = 0
        self.cur_locked_multi = 0
        self.rate_limit_lock_message = time.time() - 10
        self.allow_no_nodes = True

        # initial work must generate the affinity for each task
        super().__init__(config, log)

    def __del__(self):
        self.close()

    def close(self):
       self.unlock_meters()
       self.unlock_multi_meters()
       self.update_json()


    def remove_node(self, node):
        """ manage lock lists """
        super().remove_node(node)

        track = getattr(node, "locked_meter", None)
        if track:
            if type(track) is list:
                for meter in track:
                    meter.unlock()
                    self.cur_locked_multi -= 1
                    self.track_locks_multi.remove(meter.ip_address)

                self.locked_multi_meters.remove(track)
            else:
                track.unlock()
                self.track_locks.remove(track.ip_address)
                self.cur_locked -= 1
                self.locked_meters.remove(track)
        self.check_lock_state()
        self.update_json()

    def check_lock_state(self):
        # update tracking objects
        assert self.cur_locked == len(self.track_locks)
        total = len(self.locked_meters)
        assert self.cur_locked == total
        assert self.cur_locked_multi == sum([len(m) for m in self.locked_multi_meters])

    def update_json(self):
        path = os.getenv("BUILD_ARTIFACTSTAGINGDIRECTORY", ".")
        filename = os.path.join(path, "parallel.locks.json")

        with open(filename, "w") as fh:
            json.dump(self.track_locks, fh, indent=4)
        with open(filename, "w") as fh:
            json.dump(self.track_locks, fh, indent=4)

    def unlock_meters(self):
        if self.locked_meters:
            for m in self.locked_meters:
                if m.locked:
                    m.unlock()
                    self.track_locks.remove(m.ip_address)
            self.locked_meters=[]

    def unlock_multi_meters(self):
        if self.locked_multi_meters:
            for mm in self.locked_multi_meters:
                for m in mm:
                    if m.locked:
                        m.unlock()
                self.track_locks_multi.remove(mm)
            self.locked_multi_meters=[]

    def poll(self, config, session: DSession, nodemanager: NodeManager):
        # todo: re-schedule items taken from the queue, but not yet processed

        if not self.collection:
            return

        if time.time() > self.rate_limit_lock_message:
            changed,  notify,  needed = self.poll_single(session)
            changed2, notify2, needed = self.poll_multi(session)
            self.rate_limit_lock_message = time.time() + random.randint(50,70)

            if changed or changed2:
                self.update_json()

            if time.time() > self.rate_limit_lock_message:
                if notify or notify2:
                    # rate limit lock messages
                    self.logger.info("Meters locked.  Need %s more.  Waiting")

                self.rate_limit_lock_message = time.time() + random.randint(50,70)

        # if we still have tests, then allow no nodes... we might be waiting for a lock
        locks = [not sched.tests_finished for sched in self.affinity_scheduler.values()]
        self.allow_no_nodes = any(locks)
        if self.allow_no_nodes == False:
            self.logger.info("All schedulers are finished")

    def poll_single(self, session: DSession):
        """ greedy locker, if there are tests left to operate on,
            try to get as many meters (up to the limit) as we can.

            if there are multi-meters configured as the same nodes
            the multi-meters will be starved until all tests are
            finished
        """
        changed = False
        notify = False
        needed = 0
        if 'single_meter' in self.affinity_collections and self.affinity_collections['single_meter']:
            if not self.affinity_scheduler['single_meter'].tests_finished:
                assert self._meters or self.cur_locked, "single meter tests were selected, but no meters were specified"
                for meter in self._meters:

                    needed = self.max_meters-self.cur_locked
                    # if we can't lock anymore meters, just skip
                    if needed < 1:
                        continue

                    if not meter.locked:
                        if meter.lock():
                            self.track_locks.append(meter.ip_address)
                            env = {
                                'PYTEST_XDIST_AFFINITY': 'single_meter',
                                'PYTEST_XDIST_METER_TARGET': meter.ip_address
                            }
                            node = session.start_new_node_with_env( env)
                            self.locked_meters.append(meter)
                            self._meters.remove(meter)
                            node.locked_meter = meter
                            self.cur_locked += 1
                            changed = True
                            self.check_lock_state()
                        else:
                            if self.lock_timeout < time.time() and self.cur_locked == 0:
                                self.logger.error("Meter lock timeout %s exceeded", self.lock_timeout_seconds)
                                raise ValueError("Meter lock timeout.  use --lock_timeout option to extend timeout")

                            notify = True

        return changed, notify, needed


    def poll_multi(self, session: DSession):

        changed = False
        notify = False

        needed = 0
        if 'multi_meter' in self.affinity_collections and self.affinity_collections['multi_meter']:
            if not self.affinity_scheduler['multi_meter'].tests_finished:
                assert self._multi_meters or self.cur_locked_multi, "multi_meter tests were selected, but no meters were specified"

                for mm in self._multi_meters:
                    if self.max_meters < len(mm):
                        self.logger.warning("The maximum number (--max-meters=%s) of meters is less than the number of multi_meters (%s).  This probably will cause an infinite wait for enough meters to execute the tests.",
                                            self.max_meters, len(mm))

                    needed = self.max_meters-self.cur_locked_multi
                    # if we can't lock anymore meters, just skip
                    if  needed < len(mm):
                        continue

                    if not any(m.locked for m in mm):
                        locks = [m for m in mm if m.lock()]
                        locked = len(locks) == len(mm)
                        if not locked:
                            # unlock and try again later
                            for x in locks:
                                x.unlock()
                            if self.lock_timeout < time.time() and self.cur_locked_multi == 0:
                                self.logger.error("Meter lock timeout %s exceeded", self.lock_timeout_seconds)
                                raise ValueError("Meter lock timeout.  use --lock_timeout option to extend timeout")

                            notify = True
                        else:
                            env = {
                                'PYTEST_XDIST_AFFINITY': 'multi_meter',
                                'PYTEST_XDIST_MULTI_METER_TARGET': ','.join([i.ip_address for i in mm])
                            }
                            node = session.start_new_node_with_env( env)
                            node.locked_meter = mm
                            changed = True
                            for m in mm:
                                self.track_locks_multi.append(m.ip_address)
                                self.cur_locked_multi += 1

                            self.locked_multi_meters.append(mm)
                            self._multi_meters.remove(mm)
                            self.check_lock_state()


        return changed, notify, needed
