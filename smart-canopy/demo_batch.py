"""
Batch scan — Automated Canopy Control System
=============================================

Runs every evaluation day in results/all_evaluation_results.csv and reports,
per day: minutes open, trigger breakdown, and number of actuator transitions.

No animation. Produces a summary table and writes results/demo_summary.csv.

Usage
-----
    python demo_batch.py
    python demo_batch.py --sort transitions
    python demo_batch.py --file results/all_evaluation_results.csv
"""

import argparse
import os
import sys

import pandas as pd


def pick_column(df, *candidates):
    for c in candidates:
        if c in df.columns:
            return c
    return None


def main():
    ap = argparse.ArgumentParser(description="Batch scan of all evaluation days")
    ap.add_argument("--file", default=os.path.join("results", "all_evaluation_results.csv"))
    ap.add_argument("--sort", default="date",
                    choices=["date", "transitions", "open", "tmax"],
                    help="sort order of the output table")
    ap.add_argument("--out", default=os.path.join("results", "demo_summary.csv"))
    args = ap.parse_args()

    if not os.path.exists(args.file):
        sys.exit(f"\nFile not found: {args.file}\nRun from the project root.\n")

    df = pd.read_csv(args.file)
    df["Date/Time"] = pd.to_datetime(df["Date/Time"], errors="coerce")
    df = df.dropna(subset=["Date/Time"]).sort_values("Date/Time")
    df["_day"] = df["Date/Time"].dt.date

    c_temp = pick_column(df, "Temperature (°C)", "Temperature (C)")
    c_rain = pick_column(df, "Precipitation Presence (Presence/Absence)")
    c_heat = pick_column(df, "heat_score", "Actual_Heat_Score", "future_heat_score")
    c_pope = pick_column(df, "P_OPEN")

    if c_pope is None:
        sys.exit("Column 'P_OPEN' not found — cannot analyse.")

    rows = []

    for day, sub in df.groupby("_day"):
        sub = sub.sort_values("Date/Time")

        state = (sub[c_pope] >= 0.5).astype(int)
        transitions = int((state.diff().abs() == 1).sum())

        rain_on = (sub[c_rain] == 10) if c_rain else pd.Series(False, index=sub.index)
        heat_on = (sub[c_heat] >= 0.5) if c_heat else pd.Series(False, index=sub.index)

        open_mask = state == 1

        rows.append({
            "date": day,
            "n_min": len(sub),
            "tmax": round(sub[c_temp].max(), 1) if c_temp else None,
            "rain_min": int(rain_on.sum()),
            "open_min": int(open_mask.sum()),
            "open_pct": round(open_mask.mean() * 100, 1),
            "by_rain": int((open_mask & rain_on).sum()),
            "by_heat": int((open_mask & heat_on & ~rain_on).sum()),
            "transitions": transitions,
        })

    out = pd.DataFrame(rows)

    sort_map = {
        "date": ("date", True),
        "transitions": ("transitions", False),
        "open": ("open_min", False),
        "tmax": ("tmax", False),
    }
    key, asc = sort_map[args.sort]
    out = out.sort_values(key, ascending=asc)

    # ------------------------------------------------------------------
    # Print
    # ------------------------------------------------------------------

    print()
    print("=" * 92)
    print("  CANOPY CONTROL — PER-DAY SUMMARY")
    print("=" * 92)
    print(
        f"  {'DATE':<12}{'MIN':>7}{'TMAX':>7}{'RAIN':>7}"
        f"{'OPEN':>8}{'OPEN%':>8}{'BY RAIN':>9}{'BY HEAT':>9}{'TRANSITIONS':>13}"
    )
    print("-" * 92)

    for _, r in out.iterrows():
        flag = ""
        if r["transitions"] > 10:
            flag = "  <-- high"
        print(
            f"  {str(r['date']):<12}{r['n_min']:>7,}{r['tmax']:>7.1f}{r['rain_min']:>7}"
            f"{r['open_min']:>8}{r['open_pct']:>8.1f}{r['by_rain']:>9}{r['by_heat']:>9}"
            f"{r['transitions']:>13}{flag}"
        )

    print("-" * 92)

    t = out["transitions"]
    print(f"  Days analysed          : {len(out)}")
    print(f"  Transitions per day    : mean {t.mean():.1f}  median {t.median():.0f}  max {t.max()}")
    print(f"  Days above 10          : {(t > 10).sum()}")
    print(f"  Total actuator cycles  : {t.sum():,}")
    print("=" * 92)
    print()

    os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
    out.to_csv(args.out, index=False)
    print(f"  Saved: {args.out}")
    print()


if __name__ == "__main__":
    main()
