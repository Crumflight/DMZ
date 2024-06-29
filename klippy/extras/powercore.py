from . import pwm_in
import math
from typing import TYPE_CHECKING
from simple_pid import PID
import logging
if TYPE_CHECKING:
    from ..toolhead import ToolHead, Move
    from ..configfile import ConfigWrapper
    from ..klippy import Printer
    from ..gcode import GCodeDispatch


class PowerCore:
    def __init__(self, config: "ConfigWrapper"):
        self._pwm_reader = PowerCorePWMReader(config)
        self.printer: "Printer" = config.get_printer()
        self.reactor = self.printer.get_reactor()
        self.toolhead: "ToolHead" = None
        self.printer.register_event_handler(
            "klippy:connect", self._handle_connect
        )
        self.gcode: "GCodeDispatch" = self.printer.lookup_object("gcode")
        self.gcode.register_command(
            "GET_DUTY_CYCLE",
            self.cmd_get_duty_cycle,
            desc="Get the current duty cycle",
        )
        self.gcode.register_command(
            "ENABLE_POWERCORE_FEED_SCALING",
            self.cmd_enable_scaling,
            desc="Enable scaling",
        )
        self.gcode.register_command(
            "DISABLE_POWERCORE_FEED_SCALING",
            self.cmd_disable_scaling,
            desc="Disable scaling",
        )
        self.target_duty_cycle: float = config.getfloat(
            "target_duty_cycle", 0.75, minval=0.0, maxval=1.0
        )
        self.min_feedrate: float = config.getfloat(
            "min_feedrate", 0.1, minval=0.0
        )  # mm/min
        self.max_feedrate: float = config.getfloat(
            "max_feedrate", 64.0, minval=0.0
        )  # mm/min
        self.adjustment_accel = config.getfloat(
            "powercore_adjustment_accel", 500.0, above=0.0
        )
        self.scaling_enabled = True
        self.pid_controller = PID(
            Kp=config.getfloat("kp", 1.0),
            Ki=config.getfloat("ki", 0.0),
            Kd=config.getfloat("kd", 0.0),
            setpoint=self.target_duty_cycle,
            output_limits=(0, 1),
            sample_time=None,
            time_fn=self.reactor.monotonic,
        )

    def _handle_connect(self):
        self.toolhead = self.printer.lookup_object("toolhead")

    def cmd_get_duty_cycle(self, gcmd):
        duty_cycle = self._pwm_reader.get_current_duty_cycle()
        gcmd.respond_info(f"duty_cycle: {duty_cycle}")

    def cmd_enable_scaling(self, gcmd):
        self.enable_scaling()
        gcmd.respond_info("Enabled powercore move scaling")

    def cmd_disable_scaling(self, gcmd):
        self.disable_scaling()
        gcmd.respond_info("Disabled powercore move scaling")


    def enable_scaling(self):
        self.pid_controller.reset()
        self.scaling_enabled = True

    def disable_scaling(self):
        self.scaling_enabled = False

    def check_move(self, move: "Move"):
        
        if not self.scaling_enabled:
            return
        else:
            self.scale_move(move)

    def scale_move(self, move: "Move"):
        logging.info("scale_move")
        current_duty_cycle = self._pwm_reader.get_current_duty_cycle()
        output = self.pid_controller(current_duty_cycle)
        # output it 0-1, scale it to min_feedrate-max_feedrate
        feedrate = self.min_feedrate + output * (
            self.max_feedrate - self.min_feedrate
        )
        logging.info(f"orig move feedrate: {math.sqrt(move.max_cruise_v2)}")
        logging.info(f"new move feedrate: {feedrate}")
        logging.info(f"current duty cycle: {current_duty_cycle}")
        logging.info(f"pid output: {output}")
        # feedrate is in mm/min, set_speed expects mm/sec
        move.set_speed(feedrate * 60, self.adjustment_accel)
        self.gcode.respond_info(
            f"Current duty cycle: {current_duty_cycle}, output: {output}, feedrate: {feedrate}"
        )


class PowerCorePWMReader:
    def __init__(self, config):
        printer = config.get_printer()
        self._pwm_counter = None

        pin = config.get("alrt_pin")
        pwm_frequency = config.getfloat("alrt_pwm_frequency", 100.0, above=0.0)
        report_interval = config.getfloat(
            "alrt_report_interval", 0.1, above=0.1
        )
        additional_timeout_ticks = config.getint("additional_timeout_ticks", 0, minval=0)
        self._pwm_counter = pwm_in.PWMIn(
            printer, pin, report_interval, pwm_frequency, additional_timeout_ticks
        )

    def get_current_duty_cycle(self):
        return round(self._pwm_counter.get_duty_cycle(), 3)


def load_config(config):
    return PowerCore(config)
