"""
Terminal replay demo — Automated Canopy Control System
=======================================================

Replays recorded per-minute predictions from results/all_evaluation_results.csv
and prints canopy state to the terminal in real time (accelerated).

This is a REPLAY of stored model output. It does not re-run the HMM.

Verbosity
---------
    (default)   compact one-line-per-minute view
    -v          extra sensor columns, HMM state, hourly blocks, event log
    -vv         full multi-line telemetry block per printed minute

Usage
-----
    python demo_terminal.py
    python demo_terminal.py --date 2026-07-15 --speed 90 -v
    python demo_terminal.py --date 2026-07-30 --speed 12 --step 10 -vv
    python demo_terminal.py --list

Options
-------
    --file      path to results CSV      (default: results/all_evaluation_results.csv)
    --date      evaluation day to replay (default: hottest available day)
    --speed     simulated minutes per real second (default: 60)
    --step      print every Nth minute   (default: 1)
    --list      show available dates and exit
    --no-color  disable ANSI colours
"""

import argparse
import os
import sys
import time

import pandas as pd


# ----------------------------------------------------------------------
# Terminal colours
# ----------------------------------------------------------------------

class C:
    RESET = "\033[0m"
    BOLD = "\033[1m"
    DIM = "\033[2m"
    RED = "\033[91m"
    GREEN = "\033[92m"
    YELLOW = "\033[93m"
    BLUE = "\033[94m"
    MAGENTA = "\033[95m"
    CYAN = "\033[96m"
    GREY = "\033[90m"

    @classmethod
    def disable(cls):
        for name in dir(cls):
            if name.isupper():
                setattr(cls, name, "")


def enable_windows_ansi():
    if os.name == "nt":
        try:
            import ctypes
            kernel32 = ctypes.windll.kernel32
            kernel32.SetConsoleMode(kernel32.GetStdHandle(-11), 7)
        except Exception:
            C.disable()


# ----------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------

def load_results(path):
    if not os.path.exists(path):
        sys.exit(
            f"\nFile not found: {path}\n"
            f"Run this script from the project root, or pass --file <path>.\n"
        )

    df = pd.read_csv(path)

    if "Date/Time" not in df.columns:
        sys.exit("Column 'Date/Time' not found. Is this the right results file?")

    df["Date/Time"] = pd.to_datetime(df["Date/Time"], errors="coerce")
    df = df.dropna(subset=["Date/Time"]).sort_values("Date/Time")
    df["_day"] = df["Date/Time"].dt.date
    return df


def pick_column(df, *candidates):
    for c in candidates:
        if c in df.columns:
            return c
    return None


def num(row, col, default=float("nan")):
    if col is None or col not in row:
        return default
    try:
        return float(row[col])
    except (TypeError, ValueError):
        return default


def bar(value, width=20):
    filled = int(round(max(0.0, min(1.0, value)) * width))
    return "#" * filled + "." * (width - filled)


def compass(deg):
    if deg != deg:
        return "--"
    pts = ["N", "NNE", "NE", "ENE", "E", "ESE", "SE", "SSE",
           "S", "SSW", "SW", "WSW", "W", "WNW", "NW", "NNW"]
    return pts[int((deg % 360) / 22.5 + 0.5) % 16]


def fmt_dur(minutes):
    h, m = divmod(int(minutes), 60)
    return f"{h}h{m:02d}m" if h else f"{m}m"


# ----------------------------------------------------------------------
# Header
# ----------------------------------------------------------------------

def banner(day, sub, cols, args):
    W = 96
    print()
    print(C.BOLD + "=" * W + C.RESET)
    print(C.BOLD + "  AUTOMATED CANOPY CONTROL SYSTEM  --  TERMINAL REPLAY" + C.RESET)
    print(C.BOLD + "=" * W + C.RESET)

    t0 = sub["Date/Time"].min()
    t1 = sub["Date/Time"].max()

    print(f"  {'Evaluation day':<26}: {day}")
    print(f"  {'Time span':<26}: {t0:%H:%M} - {t1:%H:%M}")
    print(f"  {'Records':<26}: {len(sub):,} minutes")

    if cols["temp"]:
        print(f"  {'Temperature range':<26}: "
              f"{sub[cols['temp']].min():.1f} - {sub[cols['temp']].max():.1f} C")
    if cols["rh"]:
        print(f"  {'Humidity range':<26}: "
              f"{sub[cols['rh']].min():.0f} - {sub[cols['rh']].max():.0f} %")
    if cols["rain_flag"]:
        print(f"  {'Rain minutes':<26}: {int((sub[cols['rain_flag']] == 10).sum())}")

    basis = "rain + heat" if cols["heat"] else "rain only"
    print(f"  {'Decision basis':<26}: {basis}")
    if args.hysteresis > 0:
        up = 0.5 + args.hysteresis / 2
        dn = 0.5 - args.hysteresis / 2
        print(f"  {'Open / close threshold':<26}: {up:.3f} / {dn:.3f}"
              f"   (hysteresis {args.hysteresis:.2f})")
    else:
        print(f"  {'Open threshold':<26}: P(OPEN) >= 0.500")

    if args.heat_open is not None:
        gate = "sun above horizon" if args.solar_gate else "no solar gate"
        print(f"  {'Heat rule':<26}: heat_score >= {args.heat_open:.2f}   ({gate})")
    else:
        print(C.GREY + f"  {'Heat rule':<26}: disabled"
              " (add --heat-open 0.5 to enable)" + C.RESET)

    if args.min_dwell > 0:
        print(f"  {'Minimum dwell time':<26}: {args.min_dwell} min")
    print(f"  {'Replay speed':<26}: {args.speed:.0f} sim-min / real-sec  (step {args.step})")

    if args.verbose:
        det = [k for k, v in cols.items() if v]
        print(C.GREY + f"  {'Columns detected':<26}: {', '.join(det)}" + C.RESET)

    print(C.GREY + f"  {'Source':<26}: replay of recorded model output" + C.RESET)
    print(C.BOLD + "=" * W + C.RESET)

    if args.verbose >= 2:
        return

    if args.verbose == 1:
        print(
            f"  {'TIME':<9}{'Ta':>6}{'RH':>5}{'WIND':>12}{'hPa':>8}"
            f"{'RAIN':>6}{'HEAT':>7}{'ST':>4}  {'P(OPEN)':<32}{'DECISION':<9}"
        )
    else:
        print(
            f"  {'TIME':<8} {'Ta':>6} {'RH':>6} {'WIND':>6} {'RAIN':>5}  "
            f"{'P(OPEN)':<22} {'STATE':<8}"
        )
    print(C.BOLD + "-" * W + C.RESET)


# ----------------------------------------------------------------------
# Row rendering
# ----------------------------------------------------------------------

class Controller:
    """
    Hybrid decision layer.

        rain  : model output P_OPEN (HMM)
        heat  : deterministic rule on heat_score, optionally gated by sun
        safety: hysteresis + minimum dwell time on the combined decision
    """

    def __init__(self, cols, heat_open=None, solar_gate=False,
                 hysteresis=0.0, min_dwell=0):
        self.cols = cols
        self.heat_open = heat_open
        self.solar_gate = solar_gate
        self.hyst = hysteresis
        self.min_dwell = min_dwell
        self.state = None
        self.held = 0

    def raw(self, row):
        """Un-smoothed decision, before hysteresis and dwell."""
        cols = self.cols
        p_open = num(row, cols["p_open"], 0.0)
        rain_on = num(row, cols["rain_flag"], 0.0) == 10
        heat = num(row, cols["heat"], 0.0)
        if heat != heat:
            heat = 0.0

        elev = num(row, "_solar_elev", 1.0)
        sun_up = (elev > 0) if self.solar_gate else True

        # asymmetric thresholds
        up = 0.5 + self.hyst / 2.0
        down = 0.5 - self.hyst / 2.0
        thr = up if self.state != "OPEN" else down

        want_model = p_open >= thr
        want_heat = (
            self.heat_open is not None
            and heat >= self.heat_open
            and sun_up
        )

        if want_model:
            reason = "rain" if rain_on else "model"
            return "OPEN", reason, p_open, rain_on, heat
        if want_heat:
            return "OPEN", "heat", p_open, rain_on, heat
        return "CLOSED", "", p_open, rain_on, heat

    def step(self, row):
        want, reason, p_open, rain_on, heat = self.raw(row)

        if self.state is None:
            self.state = want
            self.held = 0
            blocked = False
        elif want != self.state and self.held < self.min_dwell:
            blocked = True          # dwell lock: hold current state
        else:
            blocked = False
            if want != self.state:
                self.state = want
                self.held = 0

        self.held += 1

        state = self.state
        if state != "OPEN":
            reason = ""

        if state == "OPEN":
            col = C.CYAN if reason == "rain" else (
                C.MAGENTA if reason == "heat" else C.YELLOW)
        else:
            col = C.GREEN

        return state, reason, col, p_open, rain_on, heat, blocked


def render_compact(row, cols, prev_state, dec):
    state, reason, col, p_open, rain_on, _, blocked = dec
    ts = row["Date/Time"]

    line = (
        f"  {ts:%H:%M:%S}  {num(row, cols['temp']):6.1f} "
        f"{num(row, cols['rh']):6.1f} {num(row, cols['wind']):6.1f} "
        f"{('YES' if rain_on else '-'):>5}  "
        f"{col}{bar(p_open)}{C.RESET} {p_open:5.3f}  "
        f"{col}{C.BOLD}{state:<7}{C.RESET}"
    )
    if reason:
        line += C.DIM + f" ({reason})" + C.RESET
    if prev_state is not None and state != prev_state:
        line += C.RED + " <<< TRANSITION" + C.RESET

    print(line)
    return state


def render_verbose(row, cols, prev_state, dec):
    state, reason, col, p_open, rain_on, heat, blocked = dec
    ts = row["Date/Time"]

    wd = num(row, cols["wind_dir"])
    hmm_state = num(row, cols["hmm_state"])
    st = f"{int(hmm_state)}" if hmm_state == hmm_state else "-"

    line = (
        f"  {ts:%H:%M:%S} "
        f"{num(row, cols['temp']):6.1f}"
        f"{num(row, cols['rh']):5.0f}"
        f"{num(row, cols['wind']):8.1f} {compass(wd):<3}"
        f"{num(row, cols['pressure']):8.1f}"
        f"{('  RAIN' if rain_on else '     -'):>6}"
        f"{heat:7.3f}"
        f"{st:>4}  "
        f"{col}{bar(p_open, 24)}{C.RESET} {p_open:5.3f}  "
        f"{col}{C.BOLD}{state:<7}{C.RESET}"
    )
    if reason:
        line += C.DIM + f"[{reason}]" + C.RESET

    print(line)
    return state


def render_block(row, cols, prev_state, idx, dec):
    state, reason, col, p_open, rain_on, heat, blocked = dec
    ts = row["Date/Time"]

    print()
    print(C.GREY + f"  --- minute {idx} --- {ts:%Y-%m-%d %H:%M:%S} " + "-" * 38 + C.RESET)
    print(f"    SENSORS   temperature    {num(row, cols['temp']):8.1f} C")
    print(f"              humidity       {num(row, cols['rh']):8.1f} %")
    print(f"              wind           {num(row, cols['wind']):8.1f} m/s   "
          f"{compass(num(row, cols['wind_dir']))} ({num(row, cols['wind_dir']):.0f} deg)")
    print(f"              pressure       {num(row, cols['pressure']):8.1f} hPa")
    print(f"              precipitation  {num(row, cols['precip_mm']):8.2f} mm   "
          f"presence: {'YES' if rain_on else 'no'}")

    if cols["eff_heat"]:
        print(f"    DERIVED   effective_heat {num(row, cols['eff_heat']):8.2f}")
    if cols["heat"]:
        print(f"              heat_score     {heat:8.3f}")
    if cols["rain_score"]:
        print(f"              rain_score     {num(row, cols['rain_score']):8.3f}")

    hmm_state = num(row, cols["hmm_state"])
    if hmm_state == hmm_state:
        print(f"    MODEL     hmm_state      {int(hmm_state):8d}")
        probs = []
        for i in range(8):
            c = f"State_{i}_Prob"
            if c in row:
                probs.append(f"S{i}={num(row, c):.2f}")
        if probs:
            print(C.GREY + f"              posteriors     {'  '.join(probs)}" + C.RESET)

    print(f"    OUTPUT    P(OPEN)        {col}{bar(p_open, 30)}{C.RESET} {p_open:.4f}")
    print(f"              decision       {col}{C.BOLD}{state}{C.RESET}"
          + (C.DIM + f"   (trigger: {reason})" + C.RESET if reason else ""))

    if prev_state is not None and state != prev_state:
        print(C.RED + C.BOLD + f"    >>> STATE TRANSITION: {prev_state} -> {state}" + C.RESET)

    return state


# ----------------------------------------------------------------------
# Periodic + event output
# ----------------------------------------------------------------------

def hourly_block(hour, acc):
    if acc["n"] == 0:
        return
    pct = acc["open"] / acc["n"] * 100
    col = C.RED if acc["trans"] > 2 else (C.YELLOW if acc["trans"] else C.GREEN)
    print(
        C.BLUE + f"  |  {hour:02d}:00-{hour:02d}:59  " + C.RESET
        + f"minutes={acc['n']:>4}  open={acc['open']:>4} ({pct:5.1f}%)  "
        + f"Ta {acc['tmin']:.1f}-{acc['tmax']:.1f}C  "
        + f"transitions={col}{acc['trans']}{C.RESET}"
    )


def event_line(ts, prev, state, reason, held):
    tag = (C.CYAN + "RAIN" + C.RESET) if reason == "rain" else (
        (C.YELLOW + "HEAT" + C.RESET) if reason == "heat" else (C.GREY + "----" + C.RESET)
    )
    print(
        C.BOLD + f"  [EVENT {ts:%H:%M}] " + C.RESET
        + f"{prev} " + C.RED + "->" + C.RESET + f" {state}   trigger={tag}   "
        + C.GREY + f"held previous state for {fmt_dur(held)}" + C.RESET
    )


# ----------------------------------------------------------------------
# Summary
# ----------------------------------------------------------------------

def summary(stats, day, verbose):
    W = 96
    print(C.BOLD + "-" * W + C.RESET)
    print(C.BOLD + "  SUMMARY" + C.RESET)
    print(C.BOLD + "-" * W + C.RESET)

    n = max(stats["n"], 1)
    print(f"  {'Day':<28}: {day}")
    print(f"  {'Minutes replayed':<28}: {stats['n']:,}")
    print(f"  {'Minutes OPEN':<28}: {stats['open']:,} ({stats['open'] / n * 100:.1f}%)")
    print(f"  {'Minutes CLOSED':<28}: {n - stats['open']:,} "
          f"({(n - stats['open']) / n * 100:.1f}%)")
    print(f"  {'  triggered by rain':<28}: {stats['rain']:,}")
    if stats["heat_available"]:
        print(f"  {'  triggered by heat':<28}: {stats['heat']:,}")
    if stats["model"]:
        print(f"  {'  model-only (no trigger)':<28}: {stats['model']:,}")

    print()
    print(f"  {'Longest OPEN streak':<28}: {fmt_dur(stats['max_open_streak'])}")
    print(f"  {'Longest CLOSED streak':<28}: {fmt_dur(stats['max_closed_streak'])}")
    print(f"  {'Mean P(OPEN)':<28}: {stats['p_sum'] / n:.4f}")
    peak = f"  at {stats['p_max_at']:%H:%M}" if stats["p_max_at"] is not None else ""
    print(f"  {'Peak P(OPEN)':<28}: {stats['p_max']:.4f}{peak}")

    t = stats["transitions"]
    col = C.RED if t > 10 else (C.YELLOW if t > 4 else C.GREEN)
    print()
    print(f"  {'State transitions':<28}: {col}{C.BOLD}{t}{C.RESET}")
    print(C.GREY + f"  {'':<28}  each transition = one full actuator cycle" + C.RESET)
    if stats["blocked"]:
        print(f"  {'Switches suppressed':<28}: {C.GREEN}{stats['blocked']}{C.RESET}"
              + C.GREY + "  (blocked by minimum dwell time)" + C.RESET)

    if t > 10:
        print(C.RED + f"  {'':<28}  NOTE: high cycling — no hysteresis in current logic"
              + C.RESET)

    if verbose and stats["events"]:
        print()
        print(C.BOLD + "  TRANSITION LOG" + C.RESET)
        for ts, prev, new, reason in stats["events"]:
            print(C.GREY + f"    {ts:%H:%M}  {prev:<6} -> {new:<6}  {reason}" + C.RESET)

    print(C.BOLD + "=" * W + C.RESET)
    print()


# ----------------------------------------------------------------------
# Main
# ----------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser(description="Terminal replay demo for canopy control")
    ap.add_argument("--file", default=os.path.join("results", "all_evaluation_results.csv"))
    ap.add_argument("--date", default=None)
    ap.add_argument("--speed", type=float, default=60.0)
    ap.add_argument("--step", type=int, default=1)
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--no-color", action="store_true")
    ap.add_argument("-v", "--verbose", action="count", default=0,
                    help="-v detailed columns, -vv full telemetry blocks")

    g = ap.add_argument_group("control layer")
    g.add_argument("--heat-open", type=float, default=None, metavar="T",
                   help="open when heat_score >= T (e.g. 0.5). Off by default.")
    g.add_argument("--solar-gate", action="store_true",
                   help="ignore heat while the sun is below the horizon (needs pvlib)")
    g.add_argument("--lat", type=float, default=35.18)
    g.add_argument("--lon", type=float, default=129.08)
    g.add_argument("--tz", default="Asia/Seoul")
    g.add_argument("--hysteresis", type=float, default=0.0, metavar="H",
                   help="threshold gap, e.g. 0.10 -> open at .55, close at .45")
    g.add_argument("--min-dwell", type=int, default=0, metavar="M",
                   help="minimum minutes to hold a state before switching")
    args = ap.parse_args()

    if args.no_color:
        C.disable()
    else:
        enable_windows_ansi()

    df = load_results(args.file)

    cols = {
        "temp": pick_column(df, "Temperature (°C)", "Temperature (C)"),
        "rh": pick_column(df, "Humidity (%)"),
        "wind": pick_column(df, "Wind Speed (m/s)"),
        "wind_dir": pick_column(df, "Wind Direction (deg)"),
        "pressure": pick_column(df, "Local Pressure (hPa)"),
        "precip_mm": pick_column(df, "1-minute Precipitation (mm)"),
        "rain_flag": pick_column(df, "Precipitation Presence (Presence/Absence)"),
        "p_open": pick_column(df, "P_OPEN"),
        "heat": pick_column(df, "heat_score", "Actual_Heat_Score", "future_heat_score"),
        "eff_heat": pick_column(df, "effective_heat"),
        "rain_score": pick_column(df, "future_rain_score", "Actual_Rain_Score"),
        "hmm_state": pick_column(df, "HMM_State"),
    }

    if cols["p_open"] is None:
        sys.exit("Column 'P_OPEN' not found — cannot run the demo.")

    days = sorted(df["_day"].unique())

    if args.list:
        print("\nAvailable evaluation days:\n")
        for d in days:
            sub = df[df["_day"] == d]
            tmax = sub[cols["temp"]].max() if cols["temp"] else float("nan")
            rain = int((sub[cols["rain_flag"]] == 10).sum()) if cols["rain_flag"] else 0
            print(f"  {d}   Tmax={tmax:5.1f}C   rain={rain:4d} min   n={len(sub):,}")
        print()
        return

    if args.date:
        day = pd.to_datetime(args.date).date()
        if day not in days:
            sys.exit(f"Date {day} not in results. Use --list to see available days.")
    else:
        day = df.loc[df[cols["temp"]].idxmax(), "_day"] if cols["temp"] else days[-1]

    full = df[df["_day"] == day].reset_index(drop=True)

    if args.solar_gate:
        try:
            import pvlib
            idx = pd.DatetimeIndex(full["Date/Time"]).tz_localize(args.tz)
            sp = pvlib.solarposition.get_solarposition(
                idx, latitude=args.lat, longitude=args.lon)
            full["_solar_elev"] = sp["elevation"].to_numpy()
        except ImportError:
            sys.exit("--solar-gate needs pvlib.  Run:  pip install pvlib")

    sub = full.iloc[::max(1, args.step)]
    delay = max(0.0, args.step / max(args.speed, 0.001))

    ctrl = Controller(
        cols,
        heat_open=args.heat_open,
        solar_gate=args.solar_gate,
        hysteresis=args.hysteresis,
        min_dwell=args.min_dwell,
    )

    banner(day, full, cols, args)

    stats = {
        "n": 0, "open": 0, "rain": 0, "heat": 0, "model": 0,
        "transitions": 0, "heat_available": cols["heat"] is not None,
        "p_sum": 0.0, "p_max": 0.0, "p_max_at": None,
        "max_open_streak": 0, "max_closed_streak": 0, "blocked": 0,
        "events": [],
    }

    prev = None
    last_change_idx = 0
    cur_hour = None
    hacc = {"n": 0, "open": 0, "trans": 0, "tmin": 99.0, "tmax": -99.0}

    try:
        for i, (_, row) in enumerate(sub.iterrows()):
            ts = row["Date/Time"]

            if args.verbose and cur_hour is not None and ts.hour != cur_hour:
                hourly_block(cur_hour, hacc)
                hacc = {"n": 0, "open": 0, "trans": 0, "tmin": 99.0, "tmax": -99.0}
            cur_hour = ts.hour

            dec = ctrl.step(row)
            _, reason, _, p_open, rain_on, heat, blocked = dec

            if args.verbose >= 2:
                state = render_block(row, cols, prev, i, dec)
            elif args.verbose == 1:
                state = render_verbose(row, cols, prev, dec)
            else:
                state = render_compact(row, cols, prev, dec)

            if blocked:
                stats["blocked"] += 1

            stats["n"] += 1
            stats["p_sum"] += p_open
            if p_open > stats["p_max"]:
                stats["p_max"] = p_open
                stats["p_max_at"] = ts

            if state == "OPEN":
                stats["open"] += 1
                hacc["open"] += 1
                if rain_on:
                    stats["rain"] += 1
                elif heat >= 0.5:
                    stats["heat"] += 1
                else:
                    stats["model"] += 1

            ta = num(row, cols["temp"])
            if ta == ta:
                hacc["tmin"] = min(hacc["tmin"], ta)
                hacc["tmax"] = max(hacc["tmax"], ta)
            hacc["n"] += 1

            if prev is not None and state != prev:
                stats["transitions"] += 1
                hacc["trans"] += 1
                held = (i - last_change_idx) * args.step
                if prev == "OPEN":
                    stats["max_open_streak"] = max(stats["max_open_streak"], held)
                else:
                    stats["max_closed_streak"] = max(stats["max_closed_streak"], held)
                stats["events"].append((ts, prev, state, reason or "none"))
                if args.verbose:
                    event_line(ts, prev, state, reason, held)
                last_change_idx = i

            prev = state
            time.sleep(delay)

        held = (len(sub) - last_change_idx) * args.step
        if prev == "OPEN":
            stats["max_open_streak"] = max(stats["max_open_streak"], held)
        elif prev is not None:
            stats["max_closed_streak"] = max(stats["max_closed_streak"], held)

        if args.verbose and cur_hour is not None:
            hourly_block(cur_hour, hacc)

    except KeyboardInterrupt:
        print(C.YELLOW + "\n  Interrupted by user." + C.RESET)

    summary(stats, day, args.verbose)


if __name__ == "__main__":
    main()
