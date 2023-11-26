import shutil
import os
import subprocess
import logging
from subprocess import run

logger = logging.getLogger(__name__)


class STM32Programmer():
    """STM32 cli programmer

    Raises:
        FileNotFoundError: binary file not found

    Returns:
        _type_: _description_
    """

    binary = shutil.which("STM32_Programmer_CLI")

    def __init__(self) -> None:

        if STM32Programmer.binary is None:
            raise Exception(f"File does not exist: {STM32Programmer.binary}")

        cmd = f"{STM32Programmer.binary} --version | grep 'version' | cut -d: -f2"
        logger.debug(cmd)
        data = run(cmd, capture_output=True, shell=True)
        logger.debug(data.stdout)
        self.version = data.stdout.splitlines()[0].decode().strip()

    def __str__(self):
        return f"{self.__class__.__name__}, version: {self.version}"

    @staticmethod
    def getDevice(interface) -> str:
        """Get a device name from given interface

        Args:
            interface (str): interface name

        Returns:
            str: device name
        """

        cmd = f"{STM32Programmer.binary} -l {interface} | grep 'Device Index' | cut -d: -f2"
        logger.debug(cmd)
        data = run(cmd, capture_output=True, shell=True)
        output = data.stdout.splitlines()
        errors = data.stderr.splitlines()

        print(output)
        if not output:
            return Exception(f"Failed to get device name for {interface} interface")

        return output[0].decode().strip()

    @staticmethod
    def programDevice(device, firmware_folder, tsv_filename, timeout) -> bool:
        """Flash device using tsv file (define the flash memory partitions)

        Args:
            device (str): device name
            firmware_folder (str): folder path
            tsv_filename (str): tsv filename
            timeout (int): time to complete the flashing process or kill

        Returns:
            bool: True if success, False otherwise
        """

        tsv = os.path.join(firmware_folder, tsv_filename)
        logger.debug(tsv)
        cmd = [STM32Programmer.binary, "-c", f"port={device}", "-w", tsv]
        print(cmd)

        result = False
        try:
            p = subprocess.Popen(cmd, stdout=subprocess.PIPE)
            for line in p.stdout:
                line = line.decode().strip()
                if line:
                    logger.debug(line)
                    if "Flashing service completed successfully" in line:
                        result = True
            p.wait(timeout)
        except subprocess.TimeoutExpired:
            p.terminate()
            raise
        except:
            raise

        return p.returncode == 0 and result
