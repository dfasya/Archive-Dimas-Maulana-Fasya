"""
Fan control layer — UMCS 2.0
=============================

Extends the canopy decision layer to the three operating modes defined in
the project proposal:

    Eco       fans off, shade and passive airflow only
    Comfort   fans at low speed
    Heatwave  fans at high speed, concentrated cooling

The proposal specifies a cascade rather than a simple threshold: block
radiation with the canopy first, let the Venturi structure amplify natural
wind second, and run the fans only when those are not enough. This module
implements that ordering — the fans never run unless the canopy is open.

It also enforces the two operating limits stated in the proposal:
operation restricted to the 11:00-17:00 window, and a daily runtime budget
of about three to four hours.

Hardware: EC fan with a 0-10V control input, driven through a
Shelly Dimmer 0/1-10V PM (Gen3 or Gen4) over its local HTTP RPC API.
The older "Shelly Plus 0-10V Dimmer" outputs passive PWM and needs a
separate 10V supply — it is not a drop-in substitute.

Usage
-----
    from fan_control import FanController, FanDriver

    fans = FanController()
    driver = FanDriver("192.168.0.51")

    stage = fans.step(row, canopy_state, timestamp)
    driver.set_stage(stage)

    python fan_control.py --ip 192.168.0.51 --dry-run --test
"""

import argparse
import sys
import time
from datetime import datetime

try:
    import requests
except ImportError:
    requests = None


# ----------------------------------------------------------------------
# Operating envelope, from the proposal
# ----------------------------------------------------------------------

WINDOW_START_H = 11          # 폭염 시간대 한정
WINDOW_END_H = 17
DAILY_BUDGET_MIN = 240       # 일일 총 3~4시간 이내
MIN_STAGE_DWELL_MIN = 10     # avoid audible speed hunting

# heat_score thresholds for each stage, with hysteresis built in
STAGE_UP = {1: 0.50, 2: 0.75}
STAGE_DOWN = {1: 0.42, 2: 0.67}

# 0-10V output level per stage
STAGE_OUTPUT = {0: 0, 1: 40, 2: 85}     # percent of full scale


class FanController:
    """
    Decides a fan stage: 0 (Eco), 1 (Comfort), or 2 (Heatwave).

    Independent of the canopy controller, but subordinate to it — the fans
    are gated on the canopy actually being open.
    """

    def __init__(self,
                 window=(WINDOW_START_H, WINDOW_END_H),
                 budget_min=DAILY_BUDGET_MIN,
                 min_dwell_min=MIN_STAGE_DWELL_MIN):
        self.window = window
        self.budget_min = budget_min
        self.min_dwell = min_dwell_min

        self.stage = 0
        self.held_min = 0
        self.used_today_min = 0
        self._day = None

    # ------------------------------------------------------------------

    def _roll_day(self, ts):
        day = ts.date()
        if self._day != day:
            self._day = day
            self.used_today_min = 0

    def _target_stage(self, heat):
        """Stage from heat score, with asymmetric thresholds."""
        if self.stage >= 2:
            return 2 if heat >= STAGE_DOWN[2] else (
                1 if heat >= STAGE_DOWN[1] else 0)
        if self.stage == 1:
            if heat >= STAGE_UP[2]:
                return 2
            return 1 if heat >= STAGE_DOWN[1] else 0
        return 2 if heat >= STAGE_UP[2] else (
            1 if heat >= STAGE_UP[1] else 0)

    # ------------------------------------------------------------------

    def step(self, heat_score, canopy_state, ts, raining=False):
        """
        Advance one minute. Returns (stage, reason).

        heat_score    0..1
        canopy_state  "OPEN" or "CLOSED"
        ts            datetime
        raining       bool
        """
        self._roll_day(ts)

        # --- hard gates, in priority order ---

        if raining:
            return self._force(0, "rain")

        if canopy_state != "OPEN":
            return self._force(0, "canopy closed")

        if not (self.window[0] <= ts.hour < self.window[1]):
            return self._force(0, "outside operating window")

        if self.used_today_min >= self.budget_min:
            return self._force(0, "daily budget spent")

        # --- normal staging ---

        want = self._target_stage(heat_score)

        if want != self.stage and self.held_min < self.min_dwell:
            self.held_min += 1
            self._account()
            return self.stage, "dwell lock"

        if want != self.stage:
            self.stage = want
            self.held_min = 0
        else:
            self.held_min += 1

        self._account()
        reason = {0: "eco", 1: "comfort", 2: "heatwave"}[self.stage]
        return self.stage, reason

    def _force(self, stage, reason):
        if stage != self.stage:
            self.stage = stage
            self.held_min = 0
        else:
            self.held_min += 1
        return self.stage, reason

    def _account(self):
        if self.stage > 0:
            self.used_today_min += 1

    # ------------------------------------------------------------------

    def budget_left(self):
        return max(0, self.budget_min - self.used_today_min)


# ----------------------------------------------------------------------
# Hardware driver
# ----------------------------------------------------------------------

class FanDriver:
    """Shelly Dimmer 0/1-10V PM, local RPC API."""

    def __init__(self, ip, light_id=0, timeout=5.0, dry_run=False):
        self.ip = ip
        self.id = light_id
        self.timeout = timeout
        self.dry_run = dry_run
        self.level = None

    def _rpc(self, method, **params):
        url = f"http://{self.ip}/rpc/{method}"
        params.setdefault("id", self.id)

        if self.dry_run:
            print(f"    [dry-run] GET {url} {params}")
            return {"dry_run": True}

        if requests is None:
            print("    [fan] requests not installed")
            return None

        try:
            r = requests.get(url, params=params, timeout=self.timeout)
            r.raise_for_status()
            return r.json()
        except requests.RequestException as e:
            print(f"    [fan] request failed: {e}")
            return None

    # ------------------------------------------------------------------

    def status(self):
        return self._rpc("Light.GetStatus")

    def set_level(self, percent):
        """0 turns the output off; 1-100 sets the 0-10V level."""
        percent = max(0, min(100, int(percent)))

        if percent == self.level:
            return False

        if percent == 0:
            ok = self._rpc("Light.Set", on=False)
        else:
            ok = self._rpc("Light.Set", on=True, brightness=percent)

        if ok is None:
            return False

        print(f"    [fan] level {self.level} -> {percent}%")
        self.level = percent
        return True

    def set_stage(self, stage):
        return self.set_level(STAGE_OUTPUT.get(stage, 0))

    def power(self):
        """Instantaneous power draw, for the energy-saving claim."""
        st = self.status()
        if isinstance(st, dict):
            return st.get("apower")
        return None


# ----------------------------------------------------------------------
# Hardware driver — direct PWM (recommended)
# ----------------------------------------------------------------------

class PwmFanDriver:
    """
    DC fan with a PWM speed input, driven straight from a Raspberry Pi GPIO.

    This is the simplest path and the one to try first. PWM is a physical
    signal, not a protocol — there is no register map to obtain from the
    manufacturer, and a wrong setting makes the fan run at the wrong speed
    rather than misconfiguring anything.

    Typical four-wire DC fan:

        red     +24V   from a separate 24V supply
        black   GND    common with the Pi's ground
        blue    PWM    to a Pi GPIO pin
        white   TACH   optional, RPM feedback

    The PWM frequency the fan expects varies by model. 25 kHz is the most
    common for four-wire fans. If the fan buzzes or does not respond, try
    1 kHz, 10 kHz, then 25 kHz.

    Some fans expect 5V logic while the Pi outputs 3.3V. Many accept 3.3V
    anyway — test before adding a level shifter.
    """

    def __init__(self, pin=18, frequency=25000, tach_pin=None,
                 pulses_per_rev=2, dry_run=False):
        self.pin = pin
        self.frequency = frequency
        self.tach_pin = tach_pin
        self.pulses_per_rev = pulses_per_rev
        self.dry_run = dry_run
        self.level = None
        self.pwm = None
        self.tach = None

        if dry_run:
            return

        try:
            from gpiozero import PWMOutputDevice, DigitalInputDevice
        except ImportError:
            sys.exit("This driver needs gpiozero.  Run:  pip install gpiozero")

        self.pwm = PWMOutputDevice(pin, frequency=frequency, initial_value=0)

        if tach_pin is not None:
            self.tach = DigitalInputDevice(tach_pin)
            self._pulses = 0
            self.tach.when_activated = self._count

    def _count(self):
        self._pulses += 1

    # ------------------------------------------------------------------

    def set_level(self, percent):
        """0-100 percent duty cycle."""
        percent = max(0, min(100, int(percent)))

        if percent == self.level:
            return False

        if self.dry_run:
            print(f"    [dry-run] GPIO{self.pin} duty = {percent}% "
                  f"@ {self.frequency} Hz")
        else:
            self.pwm.value = percent / 100.0

        print(f"    [fan] level {self.level} -> {percent}%")
        self.level = percent
        return True

    def set_stage(self, stage):
        return self.set_level(STAGE_OUTPUT.get(stage, 0))

    def rpm(self, sample_seconds=2.0):
        """Measure speed from the tach line, if connected."""
        if self.dry_run or self.tach is None:
            return None
        self._pulses = 0
        time.sleep(sample_seconds)
        return int(self._pulses / self.pulses_per_rev / sample_seconds * 60)

    def close(self):
        if self.pwm:
            self.pwm.value = 0
            self.pwm.close()


# ----------------------------------------------------------------------
# Hardware driver — Modbus RTU over RS-485
# ----------------------------------------------------------------------

class ModbusFanDriver:
    """
    EC fan driven over RS-485 / Modbus RTU.

    Wiring, if the Python host is a Raspberry Pi or a PC:

        host --USB-- RS-485 adapter --A/B twisted pair-- fan

    Wiring, if an ESP32 sits between host and fan:

        host --WiFi-- ESP32 --MAX485-- A/B twisted pair -- fan

    The USB adapter route is simpler and needs no microcontroller. Use the
    MAX485 route only if the control logic itself runs on the ESP32.

    BEFORE THIS WILL WORK you need the register map from the fan
    manufacturer. The defaults below are placeholders — every vendor uses
    a different layout, and writing to the wrong register can misconfigure
    the drive.
    """

    # --- placeholders: replace with the vendor's actual register map ---
    REG_SPEED = 0x0000        # holding register for speed setpoint
    REG_RPM = 0x0002          # input register, measured RPM
    REG_STATUS = 0x0003       # input register, alarm / status word
    SPEED_MAX = 1000          # value written for 100% speed

    def __init__(self, port="/dev/ttyUSB0", slave=1, baudrate=9600,
                 parity="N", stopbits=1, bytesize=8, timeout=1.0,
                 dry_run=False):
        self.port = port
        self.slave = slave
        self.dry_run = dry_run
        self.level = None
        self.client = None

        if dry_run:
            return

        try:
            from pymodbus.client import ModbusSerialClient
        except ImportError:
            sys.exit("This driver needs pymodbus.  Run:  pip install pymodbus")

        self.client = ModbusSerialClient(
            port=port, baudrate=baudrate, parity=parity,
            stopbits=stopbits, bytesize=bytesize, timeout=timeout,
        )
        if not self.client.connect():
            print(f"    [fan] could not open {port}")

    # ------------------------------------------------------------------

    def set_level(self, percent):
        """0-100 percent of full speed."""
        percent = max(0, min(100, int(percent)))

        if percent == self.level:
            return False

        value = int(self.SPEED_MAX * percent / 100)

        if self.dry_run:
            print(f"    [dry-run] write reg 0x{self.REG_SPEED:04X} "
                  f"= {value}  ({percent}%)  slave={self.slave}")
        else:
            r = self.client.write_register(
                self.REG_SPEED, value, slave=self.slave)
            if r.isError():
                print(f"    [fan] write failed: {r}")
                return False

        print(f"    [fan] level {self.level} -> {percent}%")
        self.level = percent
        return True

    def set_stage(self, stage):
        return self.set_level(STAGE_OUTPUT.get(stage, 0))

    def rpm(self):
        """Measured speed — the main advantage of Modbus over 0-10V."""
        if self.dry_run:
            return None
        r = self.client.read_input_registers(self.REG_RPM, count=1,
                                             slave=self.slave)
        return None if r.isError() else r.registers[0]

    def status_word(self):
        if self.dry_run:
            return None
        r = self.client.read_input_registers(self.REG_STATUS, count=1,
                                             slave=self.slave)
        return None if r.isError() else r.registers[0]

    def close(self):
        if self.client:
            self.client.close()


# ----------------------------------------------------------------------
# Offline replay — check the logic against recorded data
# ----------------------------------------------------------------------

def replay(csv_path, date, heat_open=0.5):
    """Run the fan controller over one day of recorded results."""
    import pandas as pd

    df = pd.read_csv(csv_path)
    df["Date/Time"] = pd.to_datetime(df["Date/Time"])
    day = pd.to_datetime(date).date()
    sub = df[df["Date/Time"].dt.date == day].sort_values("Date/Time")

    if sub.empty:
        sys.exit(f"No data for {date}")

    fans = FanController()
    counts = {0: 0, 1: 0, 2: 0}
    changes = 0
    prev = 0

    heat_col = "heat_score" if "heat_score" in sub.columns else None
    rain_col = "Precipitation Presence (Presence/Absence)"

    print(f"\n  Fan staging replay — {day}\n")

    for _, row in sub.iterrows():
        ts = row["Date/Time"].to_pydatetime()
        heat = float(row[heat_col]) if heat_col else 0.0
        raining = float(row.get(rain_col, 0) or 0) == 10
        canopy = "OPEN" if (heat >= heat_open or raining) else "CLOSED"

        stage, reason = fans.step(heat, canopy, ts, raining)
        counts[stage] += 1

        if stage != prev:
            changes += 1
            print(f"    {ts:%H:%M}  stage {prev} -> {stage}   ({reason})")
            prev = stage

    print(f"\n    Eco       {counts[0]:>5} min")
    print(f"    Comfort   {counts[1]:>5} min")
    print(f"    Heatwave  {counts[2]:>5} min")
    print(f"    Fan runtime {counts[1] + counts[2]} min "
          f"(budget {DAILY_BUDGET_MIN} min)")
    print(f"    Stage changes {changes}\n")


# ----------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser(description="Fan control layer")
    ap.add_argument("--ip", help="Shelly dimmer IP address")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--test", action="store_true",
                    help="step through stages 0-1-2-0")
    ap.add_argument("--replay", metavar="DATE",
                    help="replay one day from the results CSV")
    ap.add_argument("--file", default="results/all_evaluation_results.csv")
    ap.add_argument("--pwm", type=int, metavar="GPIO",
                    help="drive a PWM fan from this Raspberry Pi GPIO pin, e.g. 18")
    ap.add_argument("--pwm-freq", type=int, default=25000)
    ap.add_argument("--tach", type=int, metavar="GPIO",
                    help="optional tach input pin for RPM readback")
    ap.add_argument("--modbus", metavar="PORT",
                    help="use Modbus RTU on this serial port, e.g. COM3 or /dev/ttyUSB0")
    ap.add_argument("--slave", type=int, default=1)
    ap.add_argument("--baud", type=int, default=9600)
    args = ap.parse_args()

    if args.replay:
        replay(args.file, args.replay)
        return

    if args.pwm is not None:
        driver = PwmFanDriver(pin=args.pwm, frequency=args.pwm_freq,
                              tach_pin=args.tach, dry_run=args.dry_run)
        print(f"\n  PWM on GPIO{args.pwm} at {args.pwm_freq} Hz\n")
    elif args.modbus:
        driver = ModbusFanDriver(port=args.modbus, slave=args.slave,
                                 baudrate=args.baud, dry_run=args.dry_run)
        print(f"\n  Modbus RTU on {args.modbus}, slave {args.slave}\n")
    elif args.ip:
        driver = FanDriver(args.ip, dry_run=args.dry_run)
        print(f"\n  Status: {driver.status()}\n")
    else:
        sys.exit("Pass --pwm GPIO, --modbus PORT, or --ip, "
                 "or use --replay to test the logic offline.")

    if args.test:
        for stage in (0, 1, 2, 0):
            print(f"  Stage {stage}:")
            driver.set_stage(stage)
            time.sleep(3)
            if hasattr(driver, "rpm"):
                r = driver.rpm()
                if r is not None:
                    print(f"    measured {r} rpm")
            else:
                p = driver.power()
                if p is not None:
                    print(f"    draw {p} W")
        print()


if __name__ == "__main__":
    main()
