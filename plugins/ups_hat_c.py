# functions for get UPS status - needs enable "i2c" in raspi-config, smbus installed (sudo apt-get install -y python-smbus)


import logging
import time

import pwnagotchi
import pwnagotchi.plugins as plugins
import pwnagotchi.ui.fonts as fonts
from pwnagotchi.ui.components import LabeledValue
from pwnagotchi.ui.view import BLACK

# Config Register (R/W)
_REG_CONFIG = 0x00
# SHUNT VOLTAGE REGISTER (R)
_REG_SHUNTVOLTAGE = 0x01

# BUS VOLTAGE REGISTER (R)
_REG_BUSVOLTAGE = 0x02

# POWER REGISTER (R)
_REG_POWER = 0x03

# CURRENT REGISTER (R)
_REG_CURRENT = 0x04

# CALIBRATION REGISTER (R/W)
_REG_CALIBRATION = 0x05


class UPS:
    def __init__(self, i2c_bus=1, addr=0x43):
        # only import when the module is loaded and enabled
        import smbus
        self._bus = smbus.SMBus(i2c_bus)
        self._addr = addr

        # Set chip to known config values to start
        self._cal_value = 0
        self._current_lsb = 0
        self._power_lsb = 0
        self.set_calibration_32V_2A()

    def read(self, address):
        data = self._bus.read_i2c_block_data(self._addr, address, 2)
        return ((data[0] * 256) + data[1])

    def write(self, address, data):
        temp = [0, 0]
        temp[1] = data & 0xFF
        temp[0] = (data & 0xFF00) >> 8
        self._bus.write_i2c_block_data(self._addr, address, temp)

    def set_calibration_32V_2A(self):
        """Configures to INA219 to be able to measure up to 32V and 2A of current. Counter
           overflow occurs at 3.2A.
           ..note :: These calculations assume a 0.1 shunt ohm resistor is present
        """

        self._cal_value = 0
        self._current_lsb = 1  # Current LSB = 100uA per bit
        self._cal_value = 4096
        self._power_lsb = .002  # Power LSB = 2mW per bit

        # Set Calibration register to 'Cal' calculated above
        self.write(_REG_CALIBRATION, self._cal_value)

        # Set Config register to take into account the settings above
        self.bus_voltage_range = 0x01
        self.gain = 0x03
        self.bus_adc_resolution = 0x0D
        self.shunt_adc_resolution = 0x0D
        self.mode = 0x07
        self.config = self.bus_voltage_range << 13 | \
                      self.gain << 11 | \
                      self.bus_adc_resolution << 7 | \
                      self.shunt_adc_resolution << 3 | \
                      self.mode
        self.write(_REG_CONFIG, self.config)

    def getBusVoltage_V(self):
        self.write(_REG_CALIBRATION, self._cal_value)
        self.read(_REG_BUSVOLTAGE)
        return (self.read(_REG_BUSVOLTAGE) >> 3) * 0.004

    def getCurrent_mA(self):
        value = self.read(_REG_CURRENT)
        if value > 32767:
            value -= 65535
        if (value * self._current_lsb) < 0:
            return ""
        else:
            return "+"


class UPSC(plugins.Plugin):
    __author__ = 'HannaDiamond'
    __version__ = '1.0.2'
    __license__ = 'MIT'
    __description__ = 'A plugin that will add a battery capacity and charging indicator for the UPS HAT C'

    def __init__(self):
        self.ups = None
        self.charge_state = None
        self.last_state_change = None
        self.last_state_capacity = None
        self.last_capacity = None

    def on_loaded(self):
        self.ups = UPS(i2c_bus=self.options.get("i2c_bus", 1),
                       addr=self.options.get("addr", 0x43))

    def on_ui_setup(self, ui):
        if self.options.get("label_on", True):
            ui.add_element('ups', LabeledValue(color=BLACK, label='BAT', value="--%",
                                               position=(int(self.options.get("bat_x_coord", ui.width()/2)),
                                                         int(self.options.get("bat_y_coord", 0))),
                                               label_font=fonts.Bold, text_font=fonts.Medium))
        else:
            ui.add_element('ups', LabeledValue(color=BLACK, label='', value="--%",
                                               position=(int(self.options.get("bat_x_coord", ui.width()/2)),
                                                         int(self.options.get("bat_y_coord",0))),
                                               label_font=fonts.Bold, text_font=fonts.Medium))

    def on_unload(self, ui):
        with ui._lock:
            try:
                ui.remove_element('ups')
            except Exception as e:
                logging.error(e)

    def on_ui_update(self, ui):
        bus_voltage = self.ups.getBusVoltage_V()
        capacity = int((bus_voltage - 3) / 1.2 * 100)
        if (capacity > 100): capacity = 100
        if (capacity < 0): capacity = 0

        charging = self.ups.getCurrent_mA()
        charge_state = (charging == "+")
        if self.charge_state != charge_state and self.last_capacity:  # state change
            now = time.time()
            if self.last_state_change: # if we started the timer
                dur = now - self.last_state_change
                s = int(dur)%60
                m = int(dur/60)%60
                h = int(dur/3600)
                logging.info("UPS Hat C %s for %d:%02d:%02d from %s%% to %s%%"
                             % ("Charging" if self.charge_state else "Draining",
                                h, m, s,
                                self.last_state_capacity, self.last_capacity))
            self.last_state_change = now
            self.last_state_capacity = self.last_capacity
            self.charge_state = charge_state
        self.last_capacity = capacity
        ui.set('ups', str(capacity) + "%" + charging)

        if capacity <= self.options.get('shutdown', 1):
            logging.info('[ups_hat_c] Empty battery (<= %s%%): shutting down' % self.options['shutdown'])
            ui.update(force=True, new_data={'status': 'Battery exhausted, bye ...'})
            time.sleep(3)
            pwnagotchi.shutdown()
