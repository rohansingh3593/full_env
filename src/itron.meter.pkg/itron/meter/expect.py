# -*- coding: utf-8 -*-
"""

Allows interactive communication with remote shell.  This allows executing commands that execute forever
and need to be stopped.

The methods provided allow searching for regular expressions in output and responding to the output.

"""

import re
import socket
import time
from threading import Thread
from queue import Queue, Empty
import logging

class NonBlockingStreamReader:

    def __init__(self, stream,name):
        '''
        stream: the stream to read from.
                Usually a process' stdout or stderr.
        '''

        self._s = stream
        self._q = Queue()
        self.cur = None

        def _populateQueue(stream, queue):
            '''
            Collect lines from 'stream' and put them in 'quque'.
            '''

            while not stream.closed:
                data = stream.recv(1)
                if data:
                    queue.put(data)

            queue.put(None)

        self._t = Thread(target = _populateQueue,
                args = (self._s, self._q), name=name)
        self._t.daemon = True
        self._t.start() #start collecting lines from the stream

    def read(self, size, timeout = None):
        try:
            ret = self._q.get(block = timeout is not None,
                timeout = timeout)
            if ret == None:
                return None
            return ret
        except Empty:
            return ""
class Match:
    def __init__(self, match, lines_before, line, partial=False):
        self.lines_before = lines_before
        self.partial_line = partial
        self.line = line
        self.match = match

    def __repr__(self):
        return f'Match(match="{self.match}", line="{self.line}", {"partial" if self.partial_line else "line"})'

class ParamikoExpect():
    """
    This class provides an *expect script language* like interface to the
    shell, however, it is only line oriented.  It executes a shell, then changes
    the prompt to an interal easily recognizeable string, then allows the caller to
    send commands to the shell and monitor the progress of the command.

    WARNING: as with the expect script language, it is very easy to write a script that does not behave correctly
             when the output changes unexpectedly.   Therefore, unless the command outputs data and requires a ctrl-c
             to abort the command, this class should be avoided.


    API:
    expect_prompt(timeout) -
        Wait for a prompt from the shell
        returns a Match object that matches the prompt

    command(command) - you should be at a prompt for this
        equivilant to:
            send_line(command)
            expect_prompt()
            send_line('echo $?')
            match = expect(r'^{backslash}d+$')
            return match[1]

    send(string) -
        send data without a <CR>

    send_line(string) -
        send the string, followed by a <CR> to the shell.  this is generally used to start a command

    expect(conditions) -
        {conditions} is either a single python regular expression, or an array of expressions (match on any)

        returns a Match object if there was a match, and asserts if there was an error.

    login_match - the match case for the login prompt


    example, (NOTE: The first expect matches the send echo):
    with ParamikoExpect(meter.connection.server.client) as exp:
        print(exp.login_match)

        logging.info("send hi there")
        exp.send_line('echo "hi there"')

        # disect the the three lines (echo, output and prompt)
        print(str(exp.expect('hi there', timeout=4)))
        print(str(exp.expect('hi there', timeout=4)))
        logging.info("Got hi there")
        exp.expect_prompt()

        # example of using the match object
        exp.send_line("VAR1=1234;VAR2=456")
        exp.expect_prompt()
        exp.send_line("echo The value is: $VAR1")
        exp.expect("The value") # throw away the echo line
        result = exp.expect("is: \\d+$")
        print(result.match[1])

        code, output = exp.command("ls -lR /mnt/common")
        exp.expect_prompt()
        exp.send_line('exit')
        exp.expect()


    GOTCHA WARNINGS:   send_line data is echoed back,  so you can match what you sent.  Therefore something like:
        send_line("echo hi there")
        expect("hi there") - this matches the echo command line
        expect("hi there") - this matches the actual echo chars
        expect_prompt() - should find the prompt after the echo

    """

    def __init__(self, client, timeout=60*20, encoding='utf-8',logger=logging.getLogger(),prompt='[expect-prompt]# ', output_level=logging.DEBUG, no_prompt=False):
        self.channel = client.invoke_shell()
        self.nbout = NonBlockingStreamReader(self.channel, f"NBSR-channel-{self.channel.chanid}")
        self.lines_before = []
        self.encoding = encoding
        self.output_level = output_level
        self.timeout = timeout
        self.logger = logger
        self.match = None
        if no_prompt:
            self.login_match = self.expect(re.escape("# "))
            self.channel.send(f'PS1="{prompt}";\n'.encode(self.encoding))
            self.PROMPT = '.*' + re.escape(prompt) + r'$'
            # we should have the echo, then the prompt
            self.ps1_match = self.expect_prompt(timeout=10)
        else:
            self.PROMPT = None

    def set_prompt(self, prompt):
        self.PROMPT = prompt

    def __enter__(self):
        return self

    def __exit__(self, type, value, traceback):

        try:
            self.channel.close()

        except socket.timeout:
            self._logger().exception("Command '{}' taking too long to execute".format("shell"))
            raise

    def expect_prompt(self, **kwargs):
        return self.expect(self.PROMPT, **kwargs)

    def command(self, command, timeout=None):
        assert self.PROMPT, "Can't use this function without a prompt.  use set_prompt() first."
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


    def readline(self, timeout=None):
        """ Read a line from stdout and return it """
        def full_line_true(line,partial):
            return not partial

        match = self.expect(matcher=full_line_true, timeout=timeout)
        if match:
            ret = match.line
        else:
            ret = None
        return ret

    def send_ctrl_c(self):
        self.channel.send(chr(3))

    def send_line(self, line):
        self.channel.send((line+'\n').encode(self.encoding))

    def is_match(self, line, partial):
        re_strings = self.re_strings
        match = len(re_strings) == 0
        if type(re_strings) == str:
            match = re.search(re_strings, line, re.DOTALL)
        else:
            match = [rex
                 for rex in re_strings
                 if re.search( rex,
                        line, re.DOTALL)]
        self.match = match
        self.current_line = line
        self.current_partial = partial
        return bool(self.match)

    def lines_cb(self, line, partial_line):
        self.logger.log(self.output_level, 'console output %s line: "%s"', "partial" if partial_line else "full", line.encode("unicode_escape").decode('utf-8'))

    def expect(self, re_strings='', timeout=None, output_callback=None, matcher=None, execption_on_timeout=True):
        """ wait for a match from stdout.  If one is not found within timeout time, return """
        matcher = self.is_match if matcher is None else matcher
        if not timeout: timeout = self.timeout
        self.re_strings = re_strings
        self.lines_before = []

        # Create an empty line buffer and a line counter
        current_line = b''
        line_feed_byte = '\n'.encode(self.encoding)
        end_time = time.time() + timeout

        # Loop forever, Ctrl+C (KeyboardInterrupt) is used to break the tail
        while time.time() < end_time:

            # Read the output one byte at a time so we can detect \n correctly
            buffer = self.nbout.read(1,timeout=1)
            if buffer == None:
                if re_strings=='':
                    # don't throw exception if there was no match specified
                    return None

                raise ValueError("End of file before match found")

            if buffer:

                # Add the currently read buffer to the current line output
                current_line += buffer

                # Display the last read line in realtime when we reach a \n
                # character
                if buffer == line_feed_byte:
                    current_line_decoded = current_line.decode(self.encoding)
                    self.lines_cb(current_line_decoded, False)

                    if matcher(current_line_decoded, False):
                        return Match(self.match, self.lines_before, current_line_decoded)
                    else:
                        self.lines_before.append(current_line_decoded)

                    current_line = b''
            else:

                # test partial line when we have no more data
                partial_line_decoded = current_line.decode(self.encoding)
                if matcher(partial_line_decoded, True):
                    self.lines_cb(partial_line_decoded, True)
                    return Match(self.match, self.lines_before, self.current_line, partial=True)

        if execption_on_timeout:
            raise Timeout("Timeout while expecting %s", re_strings)

        return None

class Timeout(ValueError):
    pass
