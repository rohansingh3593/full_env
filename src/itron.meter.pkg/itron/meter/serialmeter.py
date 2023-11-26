import pytest

from rohan.meter.Gen5Meter import SSHGen5Meter
from rohan.meter.remoteserial import RemoteSerial



# TODO: create SSHSerial class that implements SSH
# to serial console, then connect to meter via /dev/ttyXXX
# this requires meter name to include this info.
# Suggestion: meter name is 'serial://rohan.password@1.2.3.4:/dev/ttyXXX' in database


class SSHSerial(SSHGen5Meter):
    def __init__(self,address,logger,*args,timeout=10*60):
        super().__init__(address, logger, *args, timeout=timeout)
        # monkeypatch login
        self.mm.login = self.login


    def login( self, timeout_ok=False, no_scp=False) -> 'RemoteSerial':
        return

    def connect(self):
        self.connection = RemoteSerial(self.meter_name, self.logger)
