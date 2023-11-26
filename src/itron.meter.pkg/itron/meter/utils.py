import os
import time
import datetime
import subprocess
import platform    # For getting the operating system name
from zipfile import ZipFile, ZipInfo


class MyZipFile(ZipFile):

    def extract(self, member, path=None, pwd=None):
        if not isinstance(member, ZipInfo):
            member = self.getinfo(member)

        if path is None:
            path = os.getcwd()

        ret_val = self._extract_member(member, path, pwd)
        attr = member.external_attr >> 16
        if attr != 0:
            os.chmod(ret_val, attr)
        return ret_val

    def extractall(self, path=None, members=None, pwd=None):
        if members is None:
            members = self.namelist()

        if path is None:
            path = os.getcwd()
        else:
            path = os.fspath(path)

        for zipinfo in members:
            self.extract(zipinfo, path, pwd)


def ping(logger, host, with_socket=True, port=4059):
    """
    Returns True if host (str) responds to a ping request.
    Remember that a host may not respond to a ping (ICMP) request even if the host name is valid.
    """

    connected = False
    if with_socket:
        import socket
        PORT = port
        for res in socket.getaddrinfo(host, PORT, socket.AF_UNSPEC,
                                      socket.SOCK_STREAM, 0, socket.AI_PASSIVE):
            af, socktype, proto, canonname, sa = res
            try:
                s = socket.socket(af, socktype, proto)
                s.settimeout(10.0)
                s.connect(sa)
                connected = True
            except (OSError, socket.timeout, ConnectionRefusedError) as msg:
                continue
            finally:
                s.close()
            break
        if not connected:
            logger.debug('ping port %s - could not connect meter down', PORT)
            time.sleep(2)
            return False
        else:
            return True

    # Option for the number of packets as a function of
    param = '-n' if platform.system().lower() == 'windows' else '-c'

    # Building the command. Ex: "ping -c 1 google.com"
    command = ['ping', param, '1', host]

    return subprocess.call(command) == 0


def ctime_to_seconds(ctime_str):
    """Convert ctime to time in seconds

    Args:
        ctime_str (str): ctime with TZ e.g. "Sat Mar 18 04:47:19 UTC 2023"

    Returns:
        float : floating point number expressed in seconds
    """

    return datetime.datetime.strptime(ctime_str, "%a %b %d %H:%M:%S %Z %Y").timestamp()
