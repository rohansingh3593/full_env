"""
The affinity scheduler segregates work items workers with the same resource affinity.




"""
from xdist.scheduler.loadgroup import LoadGroupScheduling
from xdist.workermanage import WorkerController
from xdist.workermanage import parse_spec_config
from collections import OrderedDict

class SubLoadGroupScheduling(LoadGroupScheduling):
    """ wrapper around the load group scheduler that proxies
        the scope function back to the parent
    """
    def __init__(self, parent, config, log=None):
        self.parent = parent
        super().__init__(config, log)
        self.nodes_pending_add = []

    def schedule(self):
        # we need to override this, as this scheduler wants to delete workers
        self.numnodes = 1
        super().schedule()

    def _split_scope(self, nodeid):
        return self.parent._split_scope(nodeid)

    @property
    def nodes_with_pending(self):
        nodes = super().nodes
        nodes.extend(self.nodes_pending_add)
        return nodes

    def add_node(self, node):
        # delay adding node, as the load sched
        # may try to assign work before collection
        # for that node is done.
        # this keeps the node out of the node
        # list until it is collected
        self.nodes_pending_add.append(node)

    def remove_node(self, node):
        # handle case where the node was deleted before
        # we get the collection
        if node in self.nodes_pending_add:
            del self.nodes_pending_add[node]
        else:
            super().remove_node(node)

    def add_node_collection(self, node: WorkerController, collection):
        if node in self.nodes_pending_add:
            super().add_node(node)
            self.nodes_pending_add.remove(node)
            super().add_node_collection(node, collection)
        else:
            self.log.warning("node collection came in for a node we don't know about.  Probably removed")


    def _assign_work_unit(self, node):
        """Assign a work unit to a node."""
        assert self.workqueue

        # Grab a unit of work
        scope, work_unit = self.workqueue.popitem(last=False)

        # Keep track of the assigned work
        assigned_to_node = self.assigned_work.setdefault(node, default=OrderedDict())
        assigned_to_node[scope] = work_unit

        # Ask the node to execute the workload
        worker_collection = self.registered_collections[node]
        nodeids_indexes = [
            worker_collection.index(nodeid)
            for nodeid, completed in work_unit.items()
            if not completed
        ]

        # above code same as normal, but we need to remap the indexes
        # so let the parent do that.
        self.parent.send_runtest_some(node, nodeids_indexes)


class LoadAffinityScheduling:

    """ provides Affinity to tests and nodes by subdividing the
        work and createing a child scheduler for each affinity group and
        assigning only those tests to that affinity group.

        Each affinity group manages its own queue status and this
        class aggregates the status to xdist

        when xdist gets work completion, the data is forwared to the
        corect affinity group scheduler

       """
    def __init__(self, config, log=None):
        self.numnodes = len(parse_spec_config(config))
        self.collection = None
        self.collections = {}
        self.scheduled = {}
        self.config = config
        self.allow_no_nodes = True
        self.log = log
        self.affinity_scheduler = {'default': SubLoadGroupScheduling(self, config, log) }
        self.node_affinity = {}
        self.affinity_collections = {'default': []}

    @property
    def nodes(self):
        return [node  for child in self.affinity_scheduler.values() for node in child.nodes_with_pending]

    def send_runtest_some(self, node, child_ids):
        """ called by child scheduler to send work to the node.
            This method needs to fixup the sub-schedulers indexes
            back to the correct indexes that the node understands,
            as the child only knows about a subset of the nodeids
            """
        # convert ids to real ids
        sched = self.sub_scheduler(node)
        assert sched.collection_is_completed
        parent_ids = [self.collection.index(sched.collection[childid]) for childid in child_ids]
        node.send_runtest_some(parent_ids)

    def need_work(self, node):
        sched = self.sub_scheduler(node)
        sched.need_work(node)

    def add_node(self, node):

        env = node.workerinfo['spec'].env

        if 'PYTEST_XDIST_AFFINITY' in env:
            affinity = env['PYTEST_XDIST_AFFINITY']
        else:
            affinity = 'default'

        #for debugging only, should not hack around with node
        node.affinity = affinity

        self.node_affinity[node] = node.affinity

        if affinity not in self.affinity_scheduler:
            self.affinity_scheduler[affinity] = SubLoadGroupScheduling(self, self.config, self.log)
        self.affinity_scheduler[affinity].add_node(node)

    def remove_node(self, node):
        if node not in self.node_affinity:
            assert node in self.node_affinity

        affinity = self.node_affinity[node]
        sched = self.affinity_scheduler[affinity]
        sched.remove_node(node)


    def sub_scheduler(self, node: WorkerController) -> SubLoadGroupScheduling:
        # get the correct scheduler
        assert node in self.node_affinity
        affinity = self.node_affinity[node]
        sched = self.affinity_scheduler[affinity]
        return sched


    def add_node_collection(self, node: WorkerController, collection):
        """ affinity steers work items to the correct affinity group.
            an affinity group is a group of workers that share the same caracteristics.
            The characteristics are defined by the application, and are typically
            resources (cpu type, resource availiabilty, etc.)

            Since each worker is assigned an affinity, and there can be multiple workers
            with the same affinity, the scheduler groups work items with workers
            sharing the same affinity.  The default affinity is the cpe
        """
        if node not in self.nodes:
            assert node in self.nodes
        self.collections[node] = collection
        if len(self.collections) == 1:
            self.collection = collection

            # break apart collection by affinity
            for nodeid in collection:
                affinity, scope = self._split_scope(nodeid, both=True)
                if affinity not in self.affinity_scheduler:
                    self.affinity_scheduler[affinity] = SubLoadGroupScheduling(self, self.config, self.log)
                if affinity not in self.affinity_collections:
                    self.affinity_collections[affinity] = []

                self.affinity_collections[affinity].append(nodeid)

        assert node in self.node_affinity
        affinity = self.node_affinity[node]
        sched = self.sub_scheduler(node)
        # let child have sub-collection based on affinity
        collection = self.affinity_collections[affinity]
        sched.add_node_collection(node, collection)

        # setup scheduler asyncronously, and don't add more than one item
        if sched.collection_is_completed and not self.scheduled.get(sched,False):
            self.scheduled[sched] = True
            sched.schedule()
        else:
            # this is a new node that arrived after schedule
            sched._reschedule(node)

    def _split_scope(self, nodeid, both=False):
        items = nodeid.split('&')
        ret = {
            'affinity': 'default',
            # default to filename group (all tests in file run on one meter)
            'group': 'bytest'
        }
        for x in items[1:]:
            entry = x.split('=')
            if len(entry) == 2:
                name = entry[0]
                value = entry[1]
                ret[name] = value

        if ret['group'] == 'bytest':
            ret['group'] = nodeid.split('&')[0]
        if ret['group'] == 'byfile':
            ret['group'] = nodeid.split('::')[0]
        if ret['group'] == 'bymodule':
            ret['group'] = nodeid.split('::')[0].rsplit('.', 1)[0]

        self.log.debug("Scope decided for %s: (%s)" %( nodeid, ret))

        if both:
            return ret['affinity'], ret['group']
        else:
            return ret['group']

    def schedule(self):
        # first take the collection, split it up
        for child in self.affinity_scheduler.values():
            if child.collection_is_completed and not self.scheduled.get(child,False):
                self.scheduled[child] = True
                child.schedule()

    def mark_test_complete(self, node, item_index, duration=0):
        """ convert the item index to it's child index for,
            and call the sub-scheduler (children don't know about
            items with different affinity """
        sched = self.sub_scheduler(node)

        # get the correct scheduler
        assert node in self.node_affinity
        affinity = self.node_affinity[node]

        item_id = self.collection[item_index]
        if item_id not in self.affinity_collections[affinity]:
            self.log("error")

        assert item_id in self.affinity_collections[affinity]
        sub_index = self.affinity_collections[affinity].index(item_id)
        sched.mark_test_complete(node, sub_index)

    @property
    def collection_is_completed(self):
        if len(self.collections):
            return True
        return False
        #return all([child.collection_is_completed for child in self.affinity_scheduler.values()])

    @property
    def has_pending(self):
        return all([child.has_pending for child in self.affinity_scheduler.values()])

    @property
    def tests_finished(self):
        finished = [child.tests_finished for child in self.affinity_scheduler.values()]
        return all(finished)
