"""
Module to support SSH and Serial connection to DIDC

This is a connection oriented interface.

"""

import sys
import logging
import time
import datetime

import rohan.meter.uart as uart
from rohan.meter.AbstractMeter import AbstractMeter
from tools.stm import STM32Programmer


class DIDC(AbstractMeter):
    """Implements DIDC device with its current functionality.
    
    Supports DIDC in Uboot and Linux modes.
    
    """    

    mode = ["uboot", "linux"]
    prompt = {"uboot": "STM32MP>", "linux": "#"}
    user = {"uboot": "", "linux": "root"}
    password = {"uboot": "", "linux": "rohan"}

    def __init__(self, port, mode) -> None:
        super().__init__()
        self.connection = None
        self.mode = mode

        self.connection = uart.UART(
            port, 115200, DIDC.prompt[self.mode], DIDC.user[self.mode], DIDC.password[self.mode])

    def __enter__(self):
        self.connect()
        # ttysetattr etc goes here before opening and returning the file object
        return self

    def __exit__(self, type, value, traceback):
        # Exception handling here
        self.disconnect()

    def coldstart(self):
        assert False, "not implemented"

    def download_db(self):
        assert False, "not implemented"

    def install(self):
        assert False, "not implemented"

    def disconnect(self) -> None:
        if self.connection:
            self.connection.close()

    def command(self, command, timeout=0.1) -> str:
        return self.connection.send(command, timeout)

    def get_version(self) -> str:
        """Get firmware version, it's Linux kernel built date for now.

        Returns:
            str: ctime
        """        
        return self.command("uname -v | cut -f4-9 -d' '")

    def program_firmware(self, firmware_folder, tsv_file) -> bool:
        """Program firmware by using STM32 Programmer

        Args:
            firmware_folder (str): images folder
            tsv_file (str): tsv filename

        Raises:
            Exception: 

        Returns:
            boot: True is successful, False otherwise
        """        
        if self.mode != "uboot":
            raise Exception("The board is not in Uboot mode")
        try:
            # switch board to DFU mode
            self.command("usb start", 5)
            self.command("stm32prog usb 0", -5)

            print(STM32Programmer())
            
            if STM32Programmer.programDevice(STM32Programmer.getDevice("usb"),
                                             firmware_folder, tsv_file, 180):
                print("Firmware upgrade success")
                return True
            else:
                print("Firmware upgrade failed")
        except Exception as e:
            print(e)
        return False


if __name__ == '__main__':
    logging.basicConfig(level=logging.DEBUG, stream=sys.stdout,
                        format='%(asctime)s - %(levelname)s - %(module)s - %(message)s')

