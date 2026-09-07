"""
Report figures — Automated Canopy Control System
=================================================

Generates publication-ready figures from results/all_evaluation_results.csv,
using the same hybrid control logic as demo_terminal.py.

Requires demo_terminal.py in the same folder (the Controller is imported
from it, so figures and demo can never diverge).

Usage
-----
    python plot_report.py
    python plot_report.py --date 2026-07-15
    python plot_report.py --heat-open 0.5 --solar-gate --hysteresis 0.10 --min-dwell 15

Output
------
    figures/fig1_daily_timeline.png     decision logic over one day
    figures/fig2_transitions.png        actuator cycles, with vs without safety layer
    figures/fig3_triggers.png           open minutes by trigger, per day
    figures/fig4_season.png             open fraction across the evaluation period
"""

import argparse
import os
import sys

import numpy as np
import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.dates as mdates

try:
    from demo_terminal import Controller, num, pick_column, load_results
except ImportError:
    sys.exit("demo_terminal.py must be in the same folder as this script.")

try:
    from fan_control import FanController
    HAS_FANS = True
except ImportError:
    HAS_FANS = False


# ----------------------------------------------------------------------
# Style
# ----------------------------------------------------------------------

INK = "#1b2a33"
GREY = "#8d9ba3"
RAIN = "#2b7fb8"
HEAT = "#c0492f"
OPEN_C = "#d9a441"
OKGREEN = "#3d8b7d"
FAN1 = "#7bb5c4"
FAN2 = "#2f6f8f"

plt.rcParams.update({
    "figure.dpi": 130,
    "savefig.dpi": 200,
    "font.size": 9,
    "axes.edgecolor": INK,
    "axes.labelcolor": INK,
    "axes.titlesize": 10,
    "axes.titleweight": "bold",
    "axes.spines.top": False,
    "axes.spines.right": False,
    "xtick.color": INK,
    "ytick.color": INK,
    "text.color": INK,
    "grid.color": "#d8dee1",
    "grid.linewidth": 0.6,
    "legend.frameon": False,
    "figure.facecolor": "white",
})


# ----------------------------------------------------------------------
# Simulation
# ----------------------------------------------------------------------

def attach_solar(df, args):
    if not args.solar_gate:
        return df
    try:
        import pvlib
    except ImportError:
        sys.exit("--solar-gate needs pvlib.  Run:  pip install pvlib")
    idx = pd.DatetimeIndex(df["Date/Time"]).tz_localize(args.tz)
    sp = pvlib.solarposition.get_solarposition(
        idx, latitude=args.lat, longitude=args.lon)
    df = df.copy()
    df["_solar_elev"] = sp["elevation"].to_numpy()
    return df


def run_day(day_df, cols, args, hysteresis, min_dwell, with_fans=False):
    """Return per-minute state/reason arrays for one day."""
    ctrl = Controller(
        cols,
        heat_open=args.heat_open,
        solar_gate=args.solar_gate,
        hysteresis=hysteresis,
        min_dwell=min_dwell,
    )
    fans = FanController() if (with_fans and HAS_FANS) else None

    states, reasons, stages = [], [], []

    for _, row in day_df.iterrows():
        state, reason, _, _, rain_on, heat, _ = ctrl.step(row)
        states.append(1 if state == "OPEN" else 0)
        reasons.append(reason)

        if fans is not None:
            ts = row["Date/Time"].to_pydatetime()
            stage, _ = fans.step(heat, state, ts, rain_on)
            stages.append(stage)

    if with_fans:
        return (np.array(states), reasons,
                np.array(stages) if stages else None)
    return np.array(states), reasons


def transitions(states):
    return int(np.abs(np.diff(states)).sum())


# ----------------------------------------------------------------------
# Figure 1 — daily timeline
# ----------------------------------------------------------------------

def fig_daily(day_df, cols, args, day, outdir):
    states, reasons, stages = run_day(
        day_df, cols, args, args.hysteresis, args.min_dwell, with_fans=True)
    t = day_df["Date/Time"].to_numpy()

    show_fans = stages is not None and len(stages) == len(t)
    n_panels = 5 if show_fans else 4
    ratios = [2, 2, 2, 1.1, 1.1] if show_fans else [2, 2, 2, 1.1]

    fig, ax = plt.subplots(n_panels, 1,
                           figsize=(9.5, 8.4 if show_fans else 7.2),
                           sharex=True, gridspec_kw={"height_ratios": ratios})

    # -- rain shading on every panel
    rain_on = (day_df[cols["rain_flag"]] == 10).to_numpy() if cols["rain_flag"] else np.zeros(len(t), bool)

    # panel 1: temperature
    ax[0].plot(t, day_df[cols["temp"]], color=HEAT, lw=1.2)
    ax[0].set_ylabel("Air temp\n(°C)")
    ax[0].grid(alpha=.5)

    # panel 2: heat score + threshold
    if cols["heat"]:
        ax[1].plot(t, day_df[cols["heat"]], color=HEAT, lw=1.2, label="heat_score")
    if args.heat_open is not None:
        ax[1].axhline(args.heat_open, ls="--", lw=.9, color=GREY,
                      label=f"heat threshold {args.heat_open:.2f}")
    if args.solar_gate and "_solar_elev" in day_df.columns:
        night = day_df["_solar_elev"].to_numpy() <= 0
        ax[1].fill_between(t, 0, 1, where=night, color=INK, alpha=.07,
                           step="mid", label="sun below horizon")
    ax[1].set_ylim(0, 1.02)
    ax[1].set_ylabel("Heat\nscore")
    ax[1].legend(loc="upper left", fontsize=7.5, ncol=3)
    ax[1].grid(alpha=.5)

    # panel 3: model probability
    ax[2].plot(t, day_df[cols["p_open"]], color=RAIN, lw=1.2, label="P(OPEN) — HMM")
    up = 0.5 + args.hysteresis / 2
    dn = 0.5 - args.hysteresis / 2
    ax[2].axhline(up, ls="--", lw=.9, color=GREY)
    ax[2].axhline(dn, ls=":", lw=.9, color=GREY)
    ax[2].text(t[5], up + .03, f"open {up:.2f}", fontsize=7, color=GREY)
    ax[2].text(t[5], dn - .09, f"close {dn:.2f}", fontsize=7, color=GREY)
    ax[2].fill_between(t, 0, 1, where=rain_on, color=RAIN, alpha=.15,
                       step="mid", label="rain observed")
    ax[2].set_ylim(0, 1.02)
    ax[2].set_ylabel("Model\nP(OPEN)")
    ax[2].legend(loc="upper right", fontsize=7.5)
    ax[2].grid(alpha=.5)

    # panel 4: canopy state, coloured by trigger
    ax[3].step(t, states, where="post", color=INK, lw=1.3)
    for r, c in (("rain", RAIN), ("heat", HEAT), ("model", OPEN_C)):
        mask = np.array([x == r for x in reasons])
        if mask.any():
            ax[3].fill_between(t, 0, 1, where=mask, color=c, alpha=.55,
                               step="post", label=r)
    ax[3].set_yticks([0, 1])
    ax[3].set_yticklabels(["CLOSED", "OPEN"])
    ax[3].set_ylim(-.15, 1.15)
    ax[3].set_ylabel("Canopy")
    ax[3].legend(loc="upper right", fontsize=7.5, ncol=3)
    ax[3].grid(alpha=.5)

    if show_fans:
        ax[4].step(t, stages, where="post", color=INK, lw=1.2)
        ax[4].fill_between(t, 0, stages, where=(stages == 1), color=FAN1,
                           alpha=.6, step="post", label="Comfort (low)")
        ax[4].fill_between(t, 0, stages, where=(stages == 2), color=FAN2,
                           alpha=.6, step="post", label="Heatwave (high)")
        ax[4].set_yticks([0, 1, 2])
        ax[4].set_yticklabels(["OFF", "LOW", "HIGH"])
        ax[4].set_ylim(-.2, 2.3)
        ax[4].set_ylabel("Fans")
        ax[4].legend(loc="upper right", fontsize=7.5, ncol=2)
        ax[4].grid(alpha=.5)

    last = ax[-1]
    last.xaxis.set_major_formatter(mdates.DateFormatter("%H:%M"))
    last.xaxis.set_major_locator(mdates.HourLocator(interval=3))
    last.set_xlabel("Time of day")

    n_open = int(states.sum())
    title = (f"Canopy control decision sequence — {day}\n"
             f"open {n_open} of {len(states)} min "
             f"({n_open / len(states) * 100:.1f}%),  "
             f"{transitions(states)} actuator cycles")
    if show_fans:
        title += f",  fans {int((stages > 0).sum())} min"

    fig.suptitle(title, fontsize=10.5, fontweight="bold", y=.985)

    fig.tight_layout(rect=[0, 0, 1, .955])
    path = os.path.join(outdir, "fig1_daily_timeline.png")
    fig.savefig(path, bbox_inches="tight")
    plt.close(fig)
    return path


# ----------------------------------------------------------------------
# Figure 2 — transitions with vs without safety layer
# ----------------------------------------------------------------------

def fig_transitions(df, cols, args, outdir):
    days, raw_t, safe_t = [], [], []

    for day, sub in df.groupby("_day"):
        sub = sub.sort_values("Date/Time")
        if len(sub) < 600:
            continue
        s_raw, _ = run_day(sub, cols, args, 0.0, 0)
        s_safe, _ = run_day(sub, cols, args, args.hysteresis, args.min_dwell)
        days.append(day)
        raw_t.append(transitions(s_raw))
        safe_t.append(transitions(s_safe))

    x = np.arange(len(days))
    fig, ax = plt.subplots(figsize=(10, 4.2))

    ax.bar(x - .2, raw_t, .4, label="no safety layer", color=HEAT, alpha=.85)
    ax.bar(x + .2, safe_t, .4,
           label=f"hysteresis {args.hysteresis:.2f} + dwell {args.min_dwell} min",
           color=OKGREEN, alpha=.9)

    ax.set_xticks(x)
    ax.set_xticklabels([str(d) for d in days], rotation=90, fontsize=6.5)
    ax.set_ylabel("Actuator cycles per day")
    ax.grid(axis="y", alpha=.5)
    ax.legend(loc="upper left")

    tot_r, tot_s = sum(raw_t), sum(safe_t)
    red = (1 - tot_s / max(tot_r, 1)) * 100
    ax.set_title(
        f"Actuator cycling, with and without the safety layer\n"
        f"total {tot_r} → {tot_s} cycles across {len(days)} days  "
        f"({red:.0f}% reduction)")

    fig.tight_layout()
    path = os.path.join(outdir, "fig2_transitions.png")
    fig.savefig(path, bbox_inches="tight")
    plt.close(fig)
    return path, tot_r, tot_s


# ----------------------------------------------------------------------
# Figure 3 — open minutes by trigger
# ----------------------------------------------------------------------

def fig_triggers(df, cols, args, outdir):
    days, by_rain, by_heat, by_model = [], [], [], []

    for day, sub in df.groupby("_day"):
        sub = sub.sort_values("Date/Time")
        if len(sub) < 600:
            continue
        states, reasons = run_day(sub, cols, args, args.hysteresis, args.min_dwell)
        days.append(day)
        by_rain.append(sum(1 for r in reasons if r == "rain"))
        by_heat.append(sum(1 for r in reasons if r == "heat"))
        by_model.append(sum(1 for r in reasons if r == "model"))

    x = np.arange(len(days))
    fig, ax = plt.subplots(figsize=(10, 4.2))

    ax.bar(x, by_rain, .7, label="rain", color=RAIN)
    ax.bar(x, by_heat, .7, bottom=by_rain, label="heat", color=HEAT)
    bottom2 = np.array(by_rain) + np.array(by_heat)
    ax.bar(x, by_model, .7, bottom=bottom2, label="model (residual)", color=OPEN_C)

    ax.set_xticks(x)
    ax.set_xticklabels([str(d) for d in days], rotation=90, fontsize=6.5)
    ax.set_ylabel("Minutes open")
    ax.grid(axis="y", alpha=.5)
    ax.legend(loc="upper left")
    ax.set_title("Canopy opening by trigger, per evaluation day")

    fig.tight_layout()
    path = os.path.join(outdir, "fig3_triggers.png")
    fig.savefig(path, bbox_inches="tight")
    plt.close(fig)
    return path


# ----------------------------------------------------------------------
# Figure 4 — seasonal overview
# ----------------------------------------------------------------------

def fig_season(df, cols, args, outdir):
    days, frac, tmax = [], [], []

    for day, sub in df.groupby("_day"):
        sub = sub.sort_values("Date/Time")
        if len(sub) < 600:
            continue
        states, _ = run_day(sub, cols, args, args.hysteresis, args.min_dwell)
        days.append(pd.Timestamp(day))
        frac.append(states.mean() * 100)
        tmax.append(sub[cols["temp"]].max())

    fig, ax1 = plt.subplots(figsize=(10, 3.8))
    ax1.plot(days, frac, "o-", color=OKGREEN, lw=1.3, ms=4, label="canopy open (%)")
    ax1.set_ylabel("Time open (%)", color=OKGREEN)
    ax1.tick_params(axis="y", labelcolor=OKGREEN)
    ax1.set_ylim(0, 105)
    ax1.grid(alpha=.5)

    ax2 = ax1.twinx()
    ax2.plot(days, tmax, "s--", color=HEAT, lw=1, ms=3, alpha=.75,
             label="daily max temperature")
    ax2.set_ylabel("Max air temperature (°C)", color=HEAT)
    ax2.tick_params(axis="y", labelcolor=HEAT)
    ax2.spines["right"].set_visible(True)

    ax1.xaxis.set_major_formatter(mdates.DateFormatter("%b"))
    ax1.set_title("Seasonal behaviour across the evaluation period")

    h1, l1 = ax1.get_legend_handles_labels()
    h2, l2 = ax2.get_legend_handles_labels()
    ax1.legend(h1 + h2, l1 + l2, loc="upper left")

    fig.tight_layout()
    path = os.path.join(outdir, "fig4_season.png")
    fig.savefig(path, bbox_inches="tight")
    plt.close(fig)
    return path


# ----------------------------------------------------------------------
# Main
# ----------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser(description="Generate report figures")
    ap.add_argument("--file", default=os.path.join("results", "all_evaluation_results.csv"))
    ap.add_argument("--date", default="2026-07-15")
    ap.add_argument("--outdir", default="figures")
    ap.add_argument("--heat-open", type=float, default=0.5)
    ap.add_argument("--solar-gate", action="store_true", default=True)
    ap.add_argument("--no-solar-gate", dest="solar_gate", action="store_false")
    ap.add_argument("--lat", type=float, default=35.18)
    ap.add_argument("--lon", type=float, default=129.08)
    ap.add_argument("--tz", default="Asia/Seoul")
    ap.add_argument("--hysteresis", type=float, default=0.10)
    ap.add_argument("--min-dwell", type=int, default=15)
    args = ap.parse_args()

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

    df = attach_solar(df, args)
    os.makedirs(args.outdir, exist_ok=True)

    day = pd.to_datetime(args.date).date()
    day_df = df[df["_day"] == day].sort_values("Date/Time").reset_index(drop=True)
    if day_df.empty:
        sys.exit(f"No data for {day}.")

    print("\nGenerating figures ...")
    p1 = fig_daily(day_df, cols, args, day, args.outdir)
    print(f"  {p1}")
    p2, tr, ts = fig_transitions(df, cols, args, args.outdir)
    print(f"  {p2}")
    p3 = fig_triggers(df, cols, args, args.outdir)
    print(f"  {p3}")
    p4 = fig_season(df, cols, args, args.outdir)
    print(f"  {p4}")

    print(f"\n  Actuator cycles: {tr} without safety layer -> {ts} with "
          f"({(1 - ts / max(tr, 1)) * 100:.0f}% reduction)\n")


if __name__ == "__main__":
    main()
