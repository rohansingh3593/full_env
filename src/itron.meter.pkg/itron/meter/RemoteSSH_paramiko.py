from curses import def_prog_mode
import os
import socket
import logging
import subprocess
import paramiko
import scp
import time
from itron.meter.expect import ParamikoExpect

class CommandResult:
    def __init__(self, out, err, code):
        self.stdout = out
        self.stderr = err
        self.exit_code = code

class SSHAuthenticationError(Exception):
    "Raised when authentication fails"
    pass

class SSHConnectError(Exception):
    "Raised when connect failed"
    pass

from paramiko.buffered_pipe import PipeTimeout as SSHTimeout


class SSHClient:
    def __init__(self, server_ip = None, server_username = None, server_password = None, server_alias = None, timeout = None, port = 22, key_file = None, logger = None):
        self.server_alias = server_alias
        self.server_ip = server_ip
        self.server_username = server_username
        self.server_password = server_password
        self.key_file = key_file
        self.timeout = timeout
        self.server_port = port
        self.transport=None
        self.logger = logger
        self.client, self.transport = self._connect_and_login(self.server_ip, self.server_port, self.server_username, self.server_password, self.timeout, self.key_file)
        self.tpclient = self.client.get_transport()
        self.scpclient = scp.SCPClient(self.tpclient, socket_timeout=60.0, progress =self.progress)
        self.progress_time = time.time() + 10 # counter for rate limiting progress bar

    def _logger(self):
        #log_obj = Loggers()
        #logger = log_obj.get_logger('KaizenBot')
        if self.logger:
            return self.logger
        else:
            raise Exception("logger is empty")

    def _connect_and_login(self, server_ip, server_port, server_username, server_password, timeout, key_file=None):
        """This connects to ``server_ip`` and returns an SSHClient.
        """
        try:
            client = paramiko.SSHClient()
            logging.getLogger("paramiko").setLevel(logging.WARNING)
            client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
            client.connect(hostname=server_ip, port = server_port,
                    username=server_username, password=server_password,
                    timeout=timeout, key_filename=key_file, look_for_keys=False, allow_agent=False)
            client.invoke_shell()
            transport = client.get_transport()
            transport.set_keepalive(30)
        except paramiko.AuthenticationException:
            self._logger().exception("Authentication failed, please verify your credentials")
            raise SSHAuthenticationError
        except paramiko.SSHException as sshException:
            self._logger().exception("Could not establish SSH connection: %s" % sshException)
            raise SSHConnectError
        except socket.timeout as e:
            self._logger().exception("Connection timed out: %s" %e)
            raise SSHConnectError
        except paramiko.buffered_pipe.PipeTimeout:
            raise SSHTimeout
        except paramiko.ssh_exception.NoValidConnectionsError:
            raise SSHConnectError
        except Exception as e:
            self._logger().exception(e)
            raise Exception(e)
        else:
            return client, transport

    def _execute_command(self, command, codec='utf-8', **kwargs):
        commandout = ''
        timeout = kwargs['timeout'] if 'timeout' in kwargs else 120

        try:
            stdin, stdout, stderr = self.client.exec_command(command, timeout=timeout)
        except paramiko.SSHException as message:
            expect_error = kwargs.get('expect_error', False)
            if not expect_error:
                self._logger().exception(message)
            raise Exception("Executing command '{}' failed: {}".format(command, str(message)))

        try:
            commandout = stdout.read();
            commandout_err = stderr.read();
            if codec:
                commandout = commandout.decode(codec)
                commandout_err = commandout_err.decode(codec)
            code = stdout.channel.recv_exit_status()
        except (socket.timeout, paramiko.buffered_pipe.PipeTimeout):
            expect_error = kwargs.get('expect_error', False)
            if not expect_error:
                self._logger().exception("Command '{}' taking too long to execute".format(command))
            raise

        stdin.flush()
        return CommandResult(commandout, commandout_err, code)

    def _invoke_shell(self):
        try:
            return self.client.invoke_shell()
        except Exception as e:
            self._logger().exception(e)
            raise Exception(e)

    def _put_file(self, file, remote_dest):
        """This copies the file ``file`` from the local machine to ``remote_dest`` on the server.
        """
        if(not os.path.isfile(file)):
            self._logger().debug("file '{}' not found".format(file))
            raise FileNotFoundError("file '{}' not found".format(file))

        self.scpclient.put(file, remote_dest)

    def _get_file(self, file, local_dest):
        """This copies the file ``file`` from the remote machine/server to ``local_dest`` on the local machine.
        """
        if local_dest:
            self.scpclient.get(file, local_dest)
        else:
            self.scpclient.get(file)

    def _file_exists(self, file):
        """This checks if file ``file`` exists on the server.
        In such case, it returns ``True`` otherwise ``False``.
        """
        output = self._execute_command('test -f {} && echo 1 || echo 0'.format(file))
        if(output.find('1') != -1):     # if(output == '1\n'):
            return True
        return False

    def _dir_exists(self, dir):
        """This checks if directory ``dir`` exists on the server.
        In such case, it returns ``True`` otherwise ``False``.
        """
        output = self._execute_command('test -d {} && echo 1 || echo 0'.format(dir))
        if(output.find('1') != -1):     # if(output == '1\n'):
            return True
        return False

    def _get_os_name(self):
        """This returns the os name of the server.
        """
        command = r'systeminfo | findstr /B /C:"OS Name"'
        try:
            output = self._execute_command(command)
        except Exception as e:
            self._logger().exception(e)
            output = str(e)

        if 'Windows' in output:
            return 'nt'
        else:
            return 'posix'

    def list_dir(self, dir, type = None, pattern = None):
        """This returns the python list of all the directories and files in directory ``dir``.

        if ``type`` is ``d``, it returns list of directories.
        if ``type`` is ``f`` it returns list of regular files.

        if ``pattern`` is given, only list matching the pattern is returned.
        """
        if pattern is not None:
            pattern = '| grep %s' % pattern
        else:
            pattern = ''

        if type in ['f', 'd']:
            type = ' -type %s' % type
        else:
            type = ''

        if not self._dir_exists(dir):
            raise NotADirectoryError("Directory '{}' does not exist".format(dir))

        command = 'find %s -maxdepth 1' % dir + type + pattern
        output = self._execute_command(command)

        output = output.split('\n')
        while('' in output):
            output.remove('')
        return output

    def progress(self, filename, size, sent):
        if time.time() > self.progress_time:
            self._logger().info("%s\'s progress: %.2f%%   \r" % (filename, float(sent)/float(size)*100) )
            self.progress_time = time.time() + 10


class RemoteSSH():
    def __init__(self, hostname,logger, timeout=120, timeout_ok=False, no_scp=False):
        pkey = os.path.join(os.getenv('HOME'), ".ssh", "id_rsa")
        self.logger = logging.LoggerAdapter(logger, {"meter": hostname})
        self.hostname = hostname
        try:
            self.server = SSHClient(hostname, 'root', 'itron',timeout=timeout, logger=self.logger)
            self.paramiko_client = self.server.client
        except Exception:
            self.logger.info("Meter non-responsive")
            raise

    def upload_keys( self ):
        self.logger.info("Uploading SSH keys...")
        home = os.getenv("HOME")
        # note, ignore errors from this action.  We may not have any keys to upload
        subprocess.run(["sshpass", "-p", "itron", "ssh-copy-id",  "-i",  f"{home}/.ssh/id_rsa.pub", f"root@{self.hostname}"], check=True)

    def disconnect(self):
        self.paramiko_client.close()

    def invoke_shell(self, **kwargs):
        open_shell(self.paramiko_client, "ssh meter")

    def expect_shell(self, timeout=2*60, **kwargs):
        self.logger.info("Starting shell with timeout of %s", timeout)
        return ParamikoExpect(self.server.client, timeout=timeout, logger=self.logger, **kwargs)

    def command(self, cmd, **kwargs):
        code, data = self.execute_command(cmd,**kwargs)
        if code:
            self.logger.error("Command %s returned non zero code %s", cmd, code)
        assert code == 0, "Error executing command %s" % (cmd)
        return data

    def command_with_code(self, cmd, **kwargs):
        code, data = self.execute_command(cmd,**kwargs)
        return code, data

    def command_with_all(self, cmd, **kwargs):
        code, stdout, stderr = self._execute_command(cmd,**kwargs)
        if kwargs.get('splitlines', True):
            stdout = stdout.splitlines()
            stderr = stderr.splitlines()
        return code, stdout, stderr

    def execute_command(self, cmd, **kwargs):
        code, data, _ = self._execute_command(cmd, **kwargs)
        if kwargs.get('splitlines', True):
            return code, data.splitlines()
        else:
            return code, data

    def _execute_command(self, cmd, **kwargs):
        code = 0
        if cmd:
            self.logger.debug(f"RemoteCMD: {cmd}")
        try:
            result = self.server._execute_command(cmd,**kwargs)
        except (socket.timeout, paramiko.buffered_pipe.PipeTimeout) as e:
            expect_error = kwargs.get('expect_error', False)
            if not expect_error:
                self.logger.exception("Exception during execute")
            raise
        except BaseException as e:
            self.logger.exception("Exception during execute")
            code = 1 #TODO: rewrite execute_command to use recv_exit_status()
            raise

        self.logger.debug(f"result code: %s\nstdout: %s\nstderr: %s", result.exit_code, result.stdout, result.stderr)
        if result.exit_code:
            code = result.exit_code
        return code, result.stdout, result.stderr

    def put_file(self, src, target):
        self.server._put_file(src, target)

    def get_file(self, src, target):
        self.server._get_file(src,target)

    def ls(self, path):
        """ returns a list of files in path """
        data = self.ls_list(path)
        return '\n'.join(data)

    def ls_list(self, path):
        """ returns a list of files in path """
        code, data = self.execute_command(f"ls -1 {path}")
        #assert(code == 0)
        return data

    def mkdir(self, path):
        code, _ = self.execute_command(f"mkdir -p {path}")
        assert(code == 0)

import termios
import sys
import tty
import select
def open_shell(connection, remote_name='SSH server'):
    """
    Opens a PTY on a remote server, and allows interactive commands to be run.
    Reassigns stdin to the PTY so that it functions like a full shell, as would
    be given by the OpenSSH client.
    Differences between the behavior of OpenSSH and the existing Paramiko
    connection can cause mysterious errors, especially with respect to
    authentication. By keeping the entire SSH2 connection within Paramiko, such
    inconsistencies are eliminated.
    Args:
        @connection
        A live paramiko SSH connection to the remote host.
    KWArgs:
        @remote_name="SSH server"
        The name to use to refer to the remote host during the connection
        closed message. Typically a valid FQDN or IP addr.
    """

    # get the current TTY attributes to reapply after
    # the remote shell is closed
    oldtty_attrs = termios.tcgetattr(sys.stdin)

    # invoke_shell with default options is vt100 compatible
    # which is exactly what you want for an OpenSSH imitation
    channel = connection.invoke_shell()

    def resize_pty():
        # resize to match terminal size
        tty_height, tty_width = \
                subprocess.check_output(['stty', 'size']).split()

        # try to resize, and catch it if we fail due to a closed connection
        try:
            channel.resize_pty(width=int(tty_width), height=int(tty_height))
        except paramiko.ssh_exception.SSHException:
            pass

    # wrap the whole thing in a try/finally construct to ensure
    # that exiting code for TTY handling runs
    try:
        stdin_fileno = sys.stdin.fileno()
        tty.setraw(stdin_fileno)
        tty.setcbreak(stdin_fileno)

        channel.settimeout(0.0)

        is_alive = True

        while is_alive:
            # resize on every iteration of the main loop
            resize_pty()

            # use a unix select call to wait until the remote shell
            # and stdin are ready for reading
            # this is the block until data is ready
            read_ready, write_ready, exception_list = \
                    select.select([channel, sys.stdin], [], [])

            # if the channel is one of the ready objects, print
            # it out 1024 chars at a time
            if channel in read_ready:
                # try to do a read from the remote end and print to screen
                try:
                    out = channel.recv(1024)

                    # remote close
                    if len(out) == 0:
                        is_alive = False
                    else:
                        # rely on 'print' to correctly handle encoding
                        print(out.decode('utf-8'), end='')
                        sys.stdout.flush()

                # do nothing on a timeout, as this is an ordinary condition
                except socket.timeout:
                    pass

            # if stdin is ready for reading
            if sys.stdin in read_ready and is_alive:
                # send a single character out at a time
                # this is typically human input, so sending it one character at
                # a time is the only correct action we can take

                # use an os.read to prevent nasty buffering problem with shell
                # history
                char = os.read(stdin_fileno, 1)

                # if this side of the connection closes, shut down gracefully
                if len(char) == 0:
                    is_alive = False
                else:
                    channel.send(char)

        # close down the channel for send/recv
        # this is an explicit call most likely redundant with the operations
        # that caused an exit from the REPL, but unusual exit conditions can
        # cause this to be reached uncalled
        channel.shutdown(2)

    # regardless of errors, restore the TTY to working order
    # upon exit and print that connection is closed
    finally:
        termios.tcsetattr(sys.stdin, termios.TCSAFLUSH, oldtty_attrs)
        print('Paramiko channel to %s closed.' % remote_name)