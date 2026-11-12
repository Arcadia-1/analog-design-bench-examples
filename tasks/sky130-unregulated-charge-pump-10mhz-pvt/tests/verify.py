#!/usr/bin/env python3
"""Electrical signoff for the unregulated charge-pump contract."""

import math
import tempfile
import time
from pathlib import Path

from utils import run_spice, write_results


HERE = Path(__file__).resolve().parent
MODEL = "/opt/sky130/continuous/sky130.lib.spice"
NOMINAL = ("tt", 40)
PVT = (
    NOMINAL,
    ("ss", -10),
    ("ss", 125),
    ("ff", -10),
    ("ff", 125),
    ("sf", -10),
    ("sf", 125),
    ("fs", 40),
)
# Enabled conditions inside the public contract (40-50 uA load, 45-55 % clock duty).
ENABLED = (("50u", 0.45), ("40u", 0.55))
CHECK_NAMES = (
    "pvt_loaded_output_voltage",
    "pvt_output_ripple",
    "pvt_enabled_current",
    "pvt_disabled_current",
    "pvt_clock_enable_power",
)
ENABLED_METRICS = ("vout_avg", "vout_max", "vout_min", "i_avdd_avg", "p_clk_avg", "p_en_avg")
DISABLED_METRICS = ("i_avdd_avg", "p_clk_avg", "p_en_avg")
PIN_POWER_LIMIT_W = 1e-6


def blocked(reason: str) -> list[tuple[str, bool, str]]:
    return [(name, False, f"blocked: {reason}") for name in CHECK_NAMES]


def deck_edits(corner: str, temperature: int, **params: object) -> dict[str, str]:
    edits = {f'.lib "{MODEL}" tt': f'.lib "{MODEL}" {corner}', ".temp 27": f".temp {temperature}"}
    if "iload" in params:
        edits["iload=50u"] = f"iload={params['iload']}"
    if "duty" in params:
        edits["duty=0.5"] = f"duty={params['duty']}"
    return edits


def run_point(point: tuple[str, int]) -> list[dict[str, object]]:
    corner, temperature = point
    runs = []
    for iload, duty in ENABLED:
        with tempfile.TemporaryDirectory(prefix="charge-pump-enabled-") as work:
            row = run_spice(HERE / "benches" / "tb_charge_pump.spi", work,
                            deck_edits(corner, temperature, iload=iload, duty=duty))
        runs.append({"kind": "enabled", "name": f"{corner}/1.8V/{temperature:+d}C/{iload}A/{round(duty * 100)}%",
                     "metrics": ENABLED_METRICS, **row})
    with tempfile.TemporaryDirectory(prefix="charge-pump-disabled-") as work:
        row = run_spice(HERE / "benches" / "tb_charge_pump_pd.spi", work, deck_edits(corner, temperature))
    runs.append({"kind": "disabled", "name": f"{corner}/1.8V/{temperature:+d}C/disabled",
                 "metrics": DISABLED_METRICS, **row})
    return runs


def finite(run: dict[str, object]) -> bool:
    return all(name in run and math.isfinite(float(run[name])) for name in run["metrics"])


def checks(runs: list[dict[str, object]]) -> list[tuple[str, bool, str]]:
    if not runs or not all(finite(run) for run in runs):
        missing = [run["name"] for run in runs if not finite(run)]
        return blocked("missing or non-finite measurements in " + ", ".join(missing))
    enabled = [run for run in runs if run["kind"] == "enabled"]
    disabled = [run for run in runs if run["kind"] == "disabled"]
    for run in enabled:
        run["ripple"] = float(run["vout_max"]) - float(run["vout_min"])
    for run in runs:
        run["supply_current"] = abs(float(run["i_avdd_avg"]))
        run["pin_power"] = max(abs(float(run["p_clk_avg"])), abs(float(run["p_en_avg"])))

    low = min(enabled, key=lambda run: float(run["vout_avg"]))
    high = max(enabled, key=lambda run: float(run["vout_avg"]))
    ripple = max(enabled, key=lambda run: run["ripple"])
    on_current = max(enabled, key=lambda run: run["supply_current"])
    off_current = max(disabled, key=lambda run: run["supply_current"])
    pin = max(runs, key=lambda run: run["pin_power"])
    return [
        ("pvt_loaded_output_voltage",
         float(low["vout_avg"]) >= 2.2 and float(high["vout_avg"]) <= 2.8,
         f"range={float(low['vout_avg']):.6g}V at {low['name']} .. "
         f"{float(high['vout_avg']):.6g}V at {high['name']} (required 2.2..2.8V)"),
        ("pvt_output_ripple",
         0 <= ripple["ripple"] <= 5e-3,
         f"worst={ripple['ripple'] * 1e3:.6g}mVpp at {ripple['name']} (required <=5mVpp)"),
        ("pvt_enabled_current",
         on_current["supply_current"] <= 200e-6,
         f"worst={on_current['supply_current'] * 1e6:.6g}uA at {on_current['name']} (required <=200uA)"),
        ("pvt_disabled_current",
         off_current["supply_current"] <= 100e-9,
         f"worst={off_current['supply_current'] * 1e9:.6g}nA at {off_current['name']} (required <=100nA)"),
        ("pvt_clock_enable_power",
         pin["pin_power"] <= PIN_POWER_LIMIT_W,
         f"worst={pin['pin_power'] * 1e9:.6g}nW into CLK or EN at {pin['name']} (required <=1000nW)"),
    ]


def finish(results: list[tuple[str, bool, str]], processes: int, started: float) -> None:
    write_results(results)
    print(f"ngspice_processes={processes} wall_clock_s={time.monotonic() - started:.3f}")


def main() -> None:
    started = time.monotonic()
    runs = run_point(NOMINAL)
    nominal = checks(runs)
    if not all(passed for _, passed, _ in nominal):
        failures = "; ".join(f"{name}: {message}" for name, passed, message in nominal if not passed)
        finish(blocked(f"nominal tt/40C gate failed: {failures}"), len(runs), started)
        return
    for point in PVT[1:]:
        runs.extend(run_point(point))
    finish(checks(runs), len(runs), started)


if __name__ == "__main__":
    main()
