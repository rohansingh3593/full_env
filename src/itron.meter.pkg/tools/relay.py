
import serial
import minimalmodbus
import time
import logging
import hid
import abc

logger = logging.getLogger(__name__)

class AbstractRelayController(abc.ABC):
    '''Abstract class for controlling relay'''

    @abc.abstractclassmethod
    def __init__(self,*args):
        pass

    @abc.abstractclassmethod
    def __enter__(self):
        return self

    @abc.abstractclassmethod
    def __exit__(self,*args):
        pass

    @abc.abstractclassmethod
    def on(self,*args,**kwargs):
        pass

    @abc.abstractclassmethod
    def off(self,*args,**kwargs):
        pass

    @abc.abstractclassmethod
    def cycle(self,*args,**kwargs):
        pass

class PowerSupplyAsRelaySerial(AbstractRelayController):
    '''
    Class to control power supply through serial
    http://www.hanmatek.cn/en/index.php?s=/Show/index/cid/14/id/12.html
    '''
    rDepth = 100
    def __init__(self, logger, port, baudrate=9600) -> None:
        self.port = port
        self.baudrate = baudrate
        self.logger = logger

    def __str__(self):
        return f"HM305 ({self.port}, {self.baudrate})"

    def __enter__(self):
        self.open_device()
        return self

    def __exit__(self, *args):
        pass

    def open_device(self):
        '''Method to open device'''
        try:
            self.supply = minimalmodbus.Instrument(self.port, 1, minimalmodbus.MODE_RTU)
            self.supply.serial.baudrate = self.baudrate
            self.supply.serial.startbits = 1
            self.supply.serial.stopbits = 1
            self.supply.serial.parity = serial.PARITY_NONE
            self.supply.serial.bytesize = 8
            self.supply.timeout = 0.5

            self.logger.debug(f'{self}: Device opened successfully')
        except Exception as e:
            self.logger.error(e)
            assert False, f'{self}: Something went wrong while opening device'

    def cycle(self) -> None:
        self.logger.debug(f"{self}: power cycle")
        self.off()
        time.sleep(1)
        self.on()
        time.sleep(1)

    def on(self) -> None:
        logger.debug(f"{self}: power on")
        self.set_power(1)

    def off(self) -> None:
        logger.debug(f"{self}: power off")
        self.set_power(0)

    def set_power(self, status):
        r = 0
        value = "Error"
        while r <= self.rDepth:
            value = self.write_power(status)
            if not value == "Error":
                return value
            r += 1
            time.sleep(0.001)
        return False

    def write_power(self, status):
        try:
            self.supply.write_register(1, status, 0)
            return True
        except:
            return "Error"


class RelayControllerSerial(AbstractRelayController):
    '''
    Class to control relay through serial
    https://numato.com/product/1-channel-usb-powered-relay-module/
    '''

    def __init__(self,logger, port, baudrate=19200) -> None:
        self.port = port
        self.baudrate = baudrate
        self.logger = logger

    def __enter__(self):
        self.open_device()
        return self

    def __exit__(self,*args):
        self.close_device()

    def open_device(self):
        '''Method to open device'''
        try:
            self.serPort = serial.Serial(self.port, self.baudrate, timeout=1)
            self.logger.debug('Device opened successfully')
        except Exception as e:
            self.logger.error(e)
            assert False, 'Something went wrong while opening device'

    def close_device(self):
        '''Method to close device'''
        try:
            self.serPort.close()
            self.logger.debug('Device closed successfully')
        except Exception as e:
            self.logger.error(e)
            assert False, 'Something went wrong while closing device'

    def command(self,cmd):
        '''Run command on relay'''
        try:
            self.logger.debug(f'Running command: {cmd}')
            self.serPort.write(f'{cmd}\n\r'.encode())
        except Exception as e:
            self.logger.error(f'Error while running command: {cmd}')
            self.logger.error(e)
            assert False, f'Error while running command: {cmd}'


    def cycle(self) -> None:
        self.logger.debug("relay cycle")
        self.command("relay on 0")
        time.sleep(1)
        self.command("relay off 0")
        time.sleep(1)

    def on(self) -> None:
        logger.debug("relay on")
        self.command("relay on 0")

    def off(self) -> None:
        logger.debug("relay off")
        self.command("relay off 0")

class RelayControllerHid(AbstractRelayController):
    '''
        Class to control relay through hid
        https://robu.in/product/usb-control-module-1-channel-5v-relay-module/
    '''

    def __init__(self,USB_CFG_VENDOR_ID,USB_CFG_DEVICE_ID,logger):
        self.logger = logger
        self.USB_CFG_VENDOR_ID = USB_CFG_VENDOR_ID
        self.USB_CFG_DEVICE_ID = USB_CFG_DEVICE_ID
        self.device = hid.device()

    def __enter__(self):
        self.open_device()
        return self

    def __exit__(self,*args):
        self.close_device()

    def open_device(self):
        '''Method to open device'''
        try:
            self.device.open(vendor_id=self.USB_CFG_VENDOR_ID, product_id=self.USB_CFG_DEVICE_ID)
            self.logger.debug('Device opened successfully')
        except Exception as e:
            self.logger.error(e)
            assert False, 'Something went wrong while opening device'

    def close_device(self):
        '''Method to close device'''
        try:
            self.device.close()
            self.logger.debug('Device closed successfully')
        except Exception as e:
            self.logger.error(e)
            assert False, 'Something went wrong while closing device'

    def command(self,cmd):
        '''Run command on relay'''
        try:
            self.logger.debug(f'Running command: {cmd}')
            self.device.write(cmd)
        except Exception as e:
            self.logger.error(f'Error while running command: {cmd}')
            self.logger.error(e)
            assert False, f'Error while running command: {cmd}'

    def on(self):
        '''Switch on the relay'''
        self.logger.debug("Switching relay on")
        self.command([0, 0xFE, 0, 0, 0, 0, 0, 0, 1])

    def off(self):
        '''Switch off the relay'''
        self.logger.debug("Switching relay off")
        self.command([0, 0xFC, 0, 0, 0, 0, 0, 0, 1])

    def cycle(self):
        '''Method for power cycle'''
        self.logger.debug("relay cycle")
        self.on()
        time.sleep(1)
        self.off()

class RelayController(AbstractRelayController):
    '''
        Generic class to control relay through hid/serial
        https://robu.in/product/usb-control-module-1-channel-5v-relay-module/
        https://numato.com/product/1-channel-usb-powered-relay-module/
    '''
    def __init__(self,relay_type,vendor_id,device_id,port,baudrate,logger):
        self.relay_type = relay_type
        self.logger = logger
        if relay_type == 'hid':
            self.vendor_id = vendor_id
            self.device_id = device_id
            self.device = hid.device()
        elif relay_type == 'serial':
            self.port = port
            self.baudrate = baudrate
            self.device = serial.Serial()
        else:
            assert False, 'Invalid relay type'

    def __enter__(self):
        self.open_relay()
        return self

    def __exit__(self,*args):
        self.close_relay()

    def open_relay(self):
        '''Method to open relay'''
        try:
            if self.relay_type == 'hid':
                self.device.open(vendor_id=self.vendor_id, product_id=self.device_id)
            elif self.relay_type == 'serial':
                self.device.port = self.port
                self.device.baudrate = self.baudrate
                self.device.timeout = 1
                self.device.open()
            self.logger.debug('Relay opened successfully')
        except Exception as e:
            self.logger.error(e)
            assert False, 'Something went wrong while opening relay'

    def close_relay(self):
        '''Method to close relay'''
        try:
            self.device.close()
            self.logger.debug('Relay closed successfully')
        except Exception as e:
            self.logger.error(e)
            assert False, 'Something went wrong while closing relay'

    def command(self,cmd):
        '''Run command on relay'''
        try:
            self.logger.debug(f'Running command: {cmd}')
            self.device.write(cmd)
        except Exception as e:
            self.logger.error(f'Error while running command: {cmd}')
            self.logger.error(e)
            assert False, f'Error while running command: {cmd}'

    def on(self):
        '''Switch on the relay'''
        self.logger.debug("Switching relay on")
        if self.relay_type == 'hid':
            self.command([0, 0xFE, 0, 0, 0, 0, 0, 0, 1])
        elif self.relay_type == 'serial':
            self.command("relay on 0")

    def off(self):
        '''Switch off the relay'''
        self.logger.debug("Switching relay off")
        if self.relay_type == 'hid':
            self.command([0, 0xFC, 0, 0, 0, 0, 0, 0, 1])
        elif self.relay_type == 'serial':
            self.command("relay off 0")

    def cycle(self):
        '''Method for power cycle'''
        self.logger.debug("relay cycle")
        self.on()
        time.sleep(1)
        self.off()

if __name__ == '__main__':
    pass
    # USBPOWRL002("/dev/ttyACM1").cycle()
    # time.sleep(5)
    # USBPOWRL002("/dev/ttyACM1").cycle()

    # USB_CFG_VENDOR_ID = 0x16c0  # 5824 = voti.nl
    # USB_CFG_DEVICE_ID = 0x05DF  # obdev's shared PID for HIDs
    # logging.basicConfig(level=logging.DEBUG)
    # log = logging.getLogger(__name__)
    # fileh = logging.FileHandler('./results.log')
    # log.addHandler(fileh)
    # with RelayController('hid',USB_CFG_VENDOR_ID,USB_CFG_DEVICE_ID,'','',log) as relay_controller:
    #     relay_controller.on()