"""
Canopy hardware driver — Shelly cover module
=============================================

Bridges the decision layer in demo_terminal.py to a physical canopy driven
by a tubular AC motor through a Shelly 2PM (Gen3/Gen4) in cover profile.

Talks to the module over its local HTTP RPC API. No cloud account needed.

Motor: SL45RM-50/12, 50 Nm, 220/230 V, S2 4 min duty.
The S2 rating is why RUN_LIMIT_S and COOLDOWN_S exist — the motor is not
rated for continuous operation and must be given time to cool.

MAINS WIRING IS NOT A SOFTWARE TASK.
The 220 V connection between module and motor must be done by a licensed
electrician. This file assumes that work is already complete and the
module's cover mode has been calibrated.

Usage
-----
    from canopy_driver import CanopyDriver

    canopy = CanopyDriver("192.168.0.50")
    print(canopy.status())
    canopy.apply("OPEN")      # only acts if the state actually changes
    canopy.apply("CLOSED")

    python canopy_driver.py --ip 192.168.0.50 --test
"""

import argparse
import sys
import time

try:
    import requests
except ImportError:
    sys.exit("This driver needs requests.  Run:  pip install requests")


# ----------------------------------------------------------------------
# Duty-cycle limits, from the motor nameplate
# ----------------------------------------------------------------------

RUN_LIMIT_S = 240       # S2 4 min — maximum continuous run
COOLDOWN_S = 600        # rest after hitting the run limit
MIN_COMMAND_GAP_S = 60  # minimum spacing between commands


class CanopyDriver:

    def __init__(self, ip, cover_id=0, timeout=5.0, dry_run=False):
        self.ip = ip
        self.id = cover_id
        self.timeout = timeout
        self.dry_run = dry_run

        self.state = None          # last commanded state
        self.last_command_t = 0.0
        self.run_started_t = None
        self.cooldown_until = 0.0

    # ------------------------------------------------------------------
    # Transport
    # ------------------------------------------------------------------

    def _rpc(self, method, **params):
        url = f"http://{self.ip}/rpc/{method}"
        params.setdefault("id", self.id)

        if self.dry_run:
            print(f"    [dry-run] GET {url} {params}")
            return {"dry_run": True}

        try:
            r = requests.get(url, params=params, timeout=self.timeout)
            r.raise_for_status()
            return r.json()
        except requests.RequestException as e:
            print(f"    [driver] request failed: {e}")
            return None

    # ------------------------------------------------------------------
    # Primitives
    # ------------------------------------------------------------------

    def status(self):
        """Cover status: state, current_pos (0-100), power draw."""
        return self._rpc("Cover.GetStatus")

    def open(self):
        return self._rpc("Cover.Open")

    def close(self):
        return self._rpc("Cover.Close")

    def stop(self):
        return self._rpc("Cover.Stop")

    def go_to(self, pos):
        """Move to a position, 0 = closed, 100 = open."""
        return self._rpc("Cover.GoToPosition", pos=int(pos))

    # ------------------------------------------------------------------
    # Duty-cycle guard
    # ------------------------------------------------------------------

    def _guard(self, now):
        """Return None if the command may proceed, else a reason string."""
        if now < self.cooldown_until:
            left = int(self.cooldown_until - now)
            return f"cooling down, {left}s remaining"

        if now - self.last_command_t < MIN_COMMAND_GAP_S:
            left = int(MIN_COMMAND_GAP_S - (now - self.last_command_t))
            return f"command spacing, {left}s remaining"

        return None

    def _watch_run(self, now):
        """Enforce the S2 continuous-run limit."""
        if self.run_started_t is None:
            return
        if now - self.run_started_t > RUN_LIMIT_S:
            print("    [driver] run limit reached — stopping and cooling down")
            self.stop()
            self.run_started_t = None
            self.cooldown_until = now + COOLDOWN_S

    # ------------------------------------------------------------------
    # Main entry point
    # ------------------------------------------------------------------

    def apply(self, desired):
        """
        Drive the canopy toward `desired` ("OPEN" or "CLOSED").

        Sends nothing if the canopy is already in that state, or if a
        duty-cycle limit is active. Returns True when a command was sent.
        """
        now = time.time()
        self._watch_run(now)

        if desired == self.state:
            return False

        blocked = self._guard(now)
        if blocked:
            print(f"    [driver] holding {self.state}: {blocked}")
            return False

        print(f"    [driver] {self.state} -> {desired}")
        result = self.open() if desired == "OPEN" else self.close()

        if result is None:
            print("    [driver] command not acknowledged, state unchanged")
            return False

        self.state = desired
        self.last_command_t = now
        self.run_started_t = now
        return True

    def sync(self):
        """
        Daily re-synchronisation. The motor reports no position of its own,
        so drive fully closed and treat that as the known reference point.
        """
        print("    [driver] synchronising to fully closed")
        self.close()
        self.state = "CLOSED"
        self.last_command_t = time.time()
        self.run_started_t = time.time()


# ----------------------------------------------------------------------
# Integration sketch
# ----------------------------------------------------------------------

def run_live(ip, poll_seconds=60, dry_run=False):
    """
    Live loop skeleton.

    Replace read_sensors() with the real sensor read, then feed the values
    through the same Controller used by demo_terminal.py so that simulation
    and hardware share one decision path.
    """
    from demo_terminal import Controller

    cols = {
        "p_open": "P_OPEN",
        "rain_flag": "Precipitation Presence (Presence/Absence)",
        "heat": "heat_score",
    }

    ctrl = Controller(cols, heat_open=0.5, solar_gate=True,
                      hysteresis=0.10, min_dwell=15)
    canopy = CanopyDriver(ip, dry_run=dry_run)
    canopy.sync()

    print(f"\n  Live loop started, polling every {poll_seconds}s. Ctrl+C to stop.\n")

    try:
        while True:
            row = read_sensors()                     # <- implement this
            state, reason, *_ = ctrl.step(row)
            canopy.apply(state)

            st = canopy.status()
            pos = st.get("current_pos") if isinstance(st, dict) else None
            print(f"  {time.strftime('%H:%M:%S')}  {state:<6} "
                  f"{'(' + reason + ')' if reason else '':<8} pos={pos}")

            time.sleep(poll_seconds)

    except KeyboardInterrupt:
        print("\n  Stopped.\n")


def read_sensors():
    """
    Placeholder. Must return a dict with the keys the Controller expects:

        P_OPEN                                        rain model output
        Precipitation Presence (Presence/Absence)     10 = raining
        heat_score                                    0..1
        _solar_elev                                   degrees

    Until the sensor layer exists, this returns a safe closed state.
    """
    return {
        "P_OPEN": 0.0,
        "Precipitation Presence (Presence/Absence)": 0.0,
        "heat_score": 0.0,
        "_solar_elev": -10.0,
    }


# ----------------------------------------------------------------------
# CLI
# ----------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser(description="Canopy hardware driver")
    ap.add_argument("--ip", required=True, help="Shelly module IP address")
    ap.add_argument("--dry-run", action="store_true",
                    help="print requests without sending them")
    ap.add_argument("--test", action="store_true",
                    help="read status, then open and close once")
    ap.add_argument("--live", action="store_true", help="run the live loop")
    ap.add_argument("--poll", type=int, default=60)
    args = ap.parse_args()

    if args.live:
        run_live(args.ip, args.poll, args.dry_run)
        return

    canopy = CanopyDriver(args.ip, dry_run=args.dry_run)

    print("\n  Cover status:")
    print(f"    {canopy.status()}\n")

    if args.test:
        print("  Opening ...")
        canopy.apply("OPEN")
        time.sleep(30)
        print(f"    {canopy.status()}")

        print("\n  Waiting out the command gap ...")
        time.sleep(MIN_COMMAND_GAP_S)

        print("  Closing ...")
        canopy.apply("CLOSED")
        time.sleep(30)
        print(f"    {canopy.status()}\n")


if __name__ == "__main__":
    main()
