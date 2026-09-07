\# Smart Canopy — Contributions



Archived copy of a team research prototype for an automated

microclimate canopy (UMCS 2.0 project).



\## Upstream



Base pipeline by @Sekigahara:

https://github.com/Sekigahara/prototype-automatic-umbrella



\- KMA 1-minute weather data ingestion

\- Gaussian HMM with walk-forward evaluation

\- Rain-triggered opening, later extended with a heat score



\## My contributions



\### Control layer



\- Deterministic heat rule as a separate decision path, independent

&#x20; of the HMM output

\- Solar elevation gate (pvlib) — heat is only actionable while the

&#x20; sun is above the horizon

\- Hysteresis (asymmetric open/close thresholds) and minimum dwell

&#x20; time to limit actuator cycling

\- Three-mode fan staging (Eco / Comfort / Heatwave) following the

&#x20; proposal's cascade: shade first, fans only when that is not enough



\### Hardware drivers



\- canopy\_driver.py — Shelly 2PM cover module over local HTTP RPC,

&#x20; with S2 4min duty-cycle protection for the SL45RM-50/12 motor

\- fan\_control.py — three transports for EC fans: direct GPIO PWM,

&#x20; Modbus RTU over RS-485, and 0-10V via a Shelly dimmer



\### Tooling



\- demo\_terminal.py — per-minute terminal replay with three verbosity

&#x20; levels, trigger attribution, and transition logging

\- demo\_batch.py — per-day summary across all evaluation folds

\- plot\_report.py — report figures, reusing the same controller

\- INTEGRATION.md — hardware wiring and setup guide



\## Findings



\- The HMM underperforms a one-line persistence baseline on rain

&#x20; (F1 0.891 vs 0.927); it trades precision for recall

\- The heat score fires at night, when there is no solar radiation

&#x20; to block — hence the solar gate

\- Without hysteresis and dwell limits, actuator cycling reaches

&#x20; 25 cycles per day; the safety layer cuts the total by about 31%

\- future\_heat\_score is nearly identical to the current heat\_score,

&#x20; so the heat component is not genuinely forecast



\## Notes



Raw KMA CSVs are not included. results/all\_evaluation\_results.csv

is sufficient to run the demo and regenerate all figures.

