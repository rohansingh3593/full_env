from curses import def_prog_mode
import os
import socket
import logging
import subprocess
import paramiko
import scp
import time
import serial
from itron.meter.expect import ParamikoExpect
import re
import random
import string
from xmodem import XMODEM

class CommandResult:
    def __init__(self, out, err, code):
        self.lines = out
        self.stderr = err
        self.exit_code = code




class SSHAuthenticationError(Exception):
    "Raised when authentication fails"
    pass

class SSHConnectError(Exception):
    "Raised when connect failed"
    pass

from paramiko.buffered_pipe import PipeTimeout as SSHTimeout



class FakeChannel:
    """ class that fakes an SSH channel
        used by asyncronous reader so must be thread safe
    """

    def __init__(self, serial: serial):
        self.serial = serial
        self.chanid = ''.join(random.choices(string.ascii_uppercase +
                             string.digits, k=7))
        self._closed = False
        self.send(b'\n\n')

    def invoke_shell(self):
        # todo: create remote shell using RPC
        return self

    def close(self):
        self._closed = True

    @property
    def closed(self):
        return self._closed

    def send(self, bytes, timeout=None):
        return self.serial.write(bytes)

    def recv(self, count=1):
        return self.serial.read(1)

class SerialExpect(ParamikoExpect):

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # itron xmodem send/recv
        self.send_x = 'lsx'
        self.recv_x = 'lrx'

    def bypass_read(self, count=1, timeout=60):
        """ bypass expect and read data directly from serial
            used by XMODEM
            """
        stop = time.time() + timeout
        buf = b''
        while len(buf) < count and time.time() < stop:
            b = self.nbout.read(1,timeout=stop-time.time())
            buf += b
        return buf

    def command_with_output(self, command, timeout=None):
        if not timeout:
            timeout = self.timeout
        # we should be at a prompt, or something is wrong
        if not re.match(self.PROMPT, self.current_line, re.DOTALL):
            self.logger.warning("you are not at a prompt and trying to execute a command")
            match = self.expect_prompt()

        # now send the command to the shell
        self.send_line(command)

        # wait for prompt or timeout....
        ret = self.expect_prompt(timeout=timeout)

        # now get the result code from the shell
        self.send_line('echo $?')
        match = self.expect(r'^(\d+)[\r\n]$', timeout=timeout)
        code = int(match.match[1])
        self.expect_prompt()
        return code, ret

    def close(self):
        self.channel.close()

    def _get_file(self, src, target):
        # todo: use xmodem pypi module to upload file
        self.send_line(f"{self.send_x} {src}")
        self.expect(f'{self.send_x} {src}')
        self.expect("\n",execption_on_timeout=False,timeout=2)
        self.expect("\n",execption_on_timeout=False,timeout=2)
        xm = XMODEM(self.bypass_read, self.channel.send)
        with open(target, 'wb') as filestream:
            result = xm.recv(filestream, retry=100)
        self.expect_prompt()
        assert result != None, "Failed to recieve file"
        self.logger.debug("Received %s bytes to remote system", result)

    def _put_file(self, src, target):
        self.send_line(f"{self.recv_x} {target}")
        self.expect(f'{self.recv_x} {target}')
        self.expect("\n",execption_on_timeout=False,timeout=2)
        xm = XMODEM(self.bypass_read, self.channel.send)
        with open(src, 'rb') as filestream:
            for i in range(10):
                result = xm.send(filestream,retry=100)
                if result:
                    break
        self.expect_prompt()
        assert result != None, "Failed to recieve file"
        self.logger.debug("Sent %s bytes to remote system", result)

    def set_xmodem(self, send, recv):
        self.logger.warning("You should probably not set these, unless you are talking to non-itron hardware")
        self.send_x = send
        self.recv_x = recv


class SerialClient:
    def __init__(self, server_ip = None, server_username = None, server_password = None, server_alias = None, timeout = None, port = 22, key_file = None, logger = None):
        opts = server_ip.split(';')
        options = {}
        for o in opts:
            v = o.split('=')
            options[v[0]] = v[1]

        self.serial = serial.Serial(options['dev'], options['baud'], timeout=0)
        self.pexpect = SerialExpect(FakeChannel(self.serial),  encoding='utf-8', logger=logger)

    def close(self):
        self.pexpect.close()

    def _execute_command(self, command, codec='utf-8', **kwargs):
        commandout = ''
        timeout = kwargs['timeout'] if 'timeout' in kwargs else 120

        code, ret = self.pexpect.command_with_output(command)
        if ret:
            lines = [line.rstrip('\r\n') for line in ret.lines_before]
        else:
            lines = []
        return CommandResult(lines[1:], "", code)

    def execute_command(self, cmd, **kwargs):
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
        return code, result.stdout.splitlines()

    def invoke_shell(self):
        # we already are a sh
        return self

    def _put_file(self, src, target):
        return self.pexpect._put_file(src,target)

    def _get_file(self, src, target):
        return self.pexpect._get_file(src,target)


class RemoteSerial:
    def __init__(self, hostname,logger, timeout=120, timeout_ok=False, no_scp=False):
        pkey = os.path.join(os.getenv('HOME'), ".ssh", "id_rsa")
        self.logger = logging.LoggerAdapter(logger, {"meter": hostname})
        self.hostname = hostname
        try:
            self.server = SerialClient(hostname, 'root', 'itron',timeout=timeout, logger=self.logger)
        except Exception:
            self.logger.info("Meter non-responsive")
            raise

    def upload_keys( self ):
        pass

    def disconnect(self):
        self.server.close()

    def invoke_shell(self, **kwargs):
        # unimplemented
        assert False

    def expect_shell(self, timeout=2*60, **kwargs):
        return self.server

    def command(self, cmd, **kwargs):
        code, data = self.execute_command(cmd,**kwargs)
        return data

    def command_with_code(self, cmd, **kwargs):
        code, data = self.execute_command(cmd,**kwargs)
        return code, data

    def execute_command(self, cmd, **kwargs):
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

        self.logger.debug(f"result code: %s\n", result.exit_code)
        if result.exit_code:
            code = result.exit_code
        return code, result.lines

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


class Timeout(ValueError):
    pass
