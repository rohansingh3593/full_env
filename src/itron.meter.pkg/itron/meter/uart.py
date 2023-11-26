import serial
import time
import logging
import sys

from tools.relay import USBPOWRL002

logger = logging.getLogger(__name__)


class UART:
    """Serial connection interface for DIDC device (Uboot and Linux)
    """

    __version__ = '1.0'

    def __init__(self, port, baudrate, prompt, username="", password=""):
        logger.debug(f'{self.__class__.__name__}, ver {self.__version__}')
        self.prompt = prompt
        self.written = 0

        uboot = False
        if not username:
            uboot = True

        # login if needed
        try:
            self.ser = serial.Serial(port, timeout=0.1, baudrate=baudrate)
            # send Enter and see what is running there
            self.write()
            while True:
                if self.ser.inWaiting():
                    line = self.ser.readline().decode().strip()
                    logger.debug(line)
                    if uboot and line.find("autoboot") >= 0:
                        self.write()
                    # user log in
                    if line.find("login:") >= 0:
                        print("login")
                        self.write(username.encode())
                        continue
                    # user password
                    if password and line.find("Password:") >= 0:
                        print("password")
                        self.write(password.encode())
                        continue
                    # prompt found
                    if line == self.prompt:
                        # if line.find(self.prompt) >= 0:
                        break

        # except serial.SerialException:
        except Exception as e:
            logger.error(e)
            raise
        logger.debug('Opened connection to "{}" port'.format(port))

    def write(self, cmd=''):
        self.written = 1
        try:
            if cmd:
                self.ser.write(cmd)
            self.ser.write(b'\r')
        except Exception as e:
            logger.error(e)

    def send(self, cmd="", timeout=0.1):
        try:
            self.write(cmd.encode())
            # to support special command that never get prompt after
            if timeout < 0:
                time.sleep(-1*timeout)
                return
            return self.read()
        except Exception as e:
            logger.error(e)

    def read(self, max_tries=5):
        if self.written:
            time.sleep(0.1)
        response = []
        tries = 0
        line = []
        while True:
            if self.ser.inWaiting():
                line = self.ser.readline().decode().strip()
                logger.debug(line)
                if line == self.prompt:
                    # if line.find(self.prompt) >= 0:
                    break
                if line:
                    response.append(line.rstrip())
                tries = 0
            else:
                tries += 1
                time.sleep(0.2)
            if tries >= max_tries:
                break
        self.written = 0
        return '\n'.join(response[1:])

    def close(self):
        if self.ser.isOpen:
            self.ser.close()
            logger.debug('Closed connection to "{}"'.format(self.ser.port))


# unittest
'''
if __name__ == '__main__':
    #logging.basicConfig(level=logging.DEBUG, stream = sys.stdout, format = '%(asctime)s - %(levelname)s - %(module)s - %(message)s')

    s = None

    try:
        # get didc to known state
        #USBPOWRL002("/dev/ttyACM0").cycle()

        # connect to uboot
        #s = UART('/dev/ttyUSB0', 115200, 'STM32MP>')
        #s.send("version")

        # connect to linux
        #s = UART('/dev/ttyUSB0', 115200, '#', "root", "rohan")
        s = UART('/dev/ttyUSB0', 115200, '#', "root")
        print(s.send("uname -a"))
        s.send("exit")

    except Exception as e:
        print(e)
    else:
        print('.')

    if s:
        s.close()
'''