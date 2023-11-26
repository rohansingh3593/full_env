import os
import socket
import logging
from click import progressbar
from pssh.clients.native.single import SSHClient
import pssh
import pssh.exceptions
import subprocess

class SSHAuthenticationError(Exception):
    "Raised when authentication fails"
    pass

class SSHConnectError(Exception):
    "Raised when connect failed"
    pass

from  pssh.exceptions import Timeout as SSHTimeout

class RemoteSSH(SSHClient):
    def __init__(self, hostname,logger, timeout=120, timeout_ok=False, no_scp=False):
        pkey = os.path.join(os.getenv('HOME'), ".ssh", "id_rsa")
        self.logger = logging.LoggerAdapter(logger, {"meter": hostname})
        self.hostname = hostname
        try:
            super().__init__(hostname, user='root',password='itron',timeout=timeout)
        except (socket.timeout, pssh.exceptions.Timeout, pssh.exceptions.ConnectionError):
            self.logger.info("Meter non-responsive")
            raise SSHConnectError

        except pssh.exceptions.AuthenticationError:
            self.upload_keys()
            super().__init__(hostname, user='root',pkey=pkey,timeout=timeout)

    def upload_keys( self ):
        self.logger.info("Uploading SSH keys...")
        home = os.getenv("HOME")
        subprocess.run(["sshpass", "-p", "itron", "ssh-copy-id",  "-i",  f"{home}/.ssh/id_rsa.pub", f"root@{self.hostname}"], check=False)

    def command(self, cmd, **kwargs):
        code, data = self.execute_command(cmd,**kwargs)
        return data

    def command_with_code(self, cmd, **kwargs):
        code, data = self.execute_command(cmd,**kwargs)
        return code, data

    def execute_command(self, cmd, expect_error=False, **kwargs):
        if cmd:
            self.logger.debug(f"RemoteCMD: {cmd}")
        try:
            result = self.run_command(cmd,**kwargs)
        except (pssh.exceptions.SSHError, pssh.exceptions.SSHException) as message:
            if not expect_error:
                self._logger().exception(message)
            raise Exception("Executing command '{}' failed: {}".format(cmd, str(message)))
            
        code = 0
        if result.exit_code:
            code = result.exit_code
        return code, list(result.stdout)

    def put_file(self, src, target):
        def progress(filename, size, sent):
            self.logger.info("%s\'s progress: %.2f%%   \r" % (filename, float(sent)/float(size)*100) )

        channel = SSHClient(self.hostname, user='root',password='itron',timeout=120)
        channel.scp_send(src, target)
        channel.disconnect()

    def get_file(self, src, target):
        def progress(filename, size, sent):
            self.logger.info("%s\'s progress: %.2f%%   \r" % (filename, float(sent)/float(size)*100) )
        channel = SSHClient(self.hostname, user='root',password='itron',timeout=120)
        target = os.path.realpath(target)
        channel.scp_recv(src, target)
        channel.disconnect()

    def ls(self, path):
        """ returns a list of files in path """
        data = self.ls_list(path)
        return '\n'.join(data)

    def ls_list(self, path):
        """ returns a list of files in path """
        code, data = self.execute_command(f"ls -1 {path}")
        assert(code == 0)
        return data

    def mkdir(self, path):
        code, _ = self.execute_command(f"mkdir -p {path}")
        assert(code == 0)
