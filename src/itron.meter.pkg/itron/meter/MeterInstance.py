
import logging
import abc

class MeterInstanceBase(abc.ABC):
    def __repr__(self):
        return f"{str(self.__class__.__name__)}({self.ip_address})"

    @abc.abstractclassmethod
    def lock(self):
        pass

    @abc.abstractclassmethod
    def unlock(self):
        pass

    def __str__(self):
        return self.ip_address


class MeterInstanceUser(MeterInstanceBase):
    def __init__(self, ip_address):
        self.ip_address = ip_address
        self.locked = False

    def unlock(self):
        assert self.locked == True, "trying"
        self.locked = False

    def lock(self):
        assert self.locked == False
        self.locked = True
        return self.locked

class MeterInstanceDB(MeterInstanceBase):
    """ This is a class to hold all of the information about a meter
    that was selected from the database """
    def __init__(self, info, parent_db):
        self.info = info
        self.parent_db = parent_db
        self.locked = False

    @property
    def ip_address(self):
        return self.info['NODE_IP']


    def lock(self):
        assert self.locked == False
        self.locked = self.parent_db.lock_node(self.ip_address)
        if self.locked:
            logging.getLogger().info("Meter %s locked", self.ip_address)
        return self.locked

    def unlock(self):
        assert self.locked == True
        self.parent_db.unlock_node(self.ip_address)
        self.locked = False
        logging.getLogger().info("Meter %s unlocked", self.ip_address)

