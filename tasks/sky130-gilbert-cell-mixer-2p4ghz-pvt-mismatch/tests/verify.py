#!/usr/bin/env python3
"""Electrical signoff for the 2.4 GHz double-balanced Gilbert-cell mixer."""

import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from itertools import product
from pathlib import Path

from utils import run_spice, write_results


HERE = Path(__file__).resolve().parent
MODEL = "/opt/sky130/continuous/sky130.lib.spice"
JOBS = 4  # the verifier container has 4 CPUs; each ngspice run is single-threaded

GAIN_MIN_DB = 3.0
ISOLATION_MAX_DB = -45.0
SPUR_MAX_DBC = -30.0
OFFSET_MAX_V = 30e-3
COMMON_MODE_MIN_V = 0.45
HEADROOM_MIN_V = 0.20
POWER_MAX_W = 2.0e-3
PIN_POWER_MAX_W = 10e-6
IIP3_MIN_DBM = -2.0
FUNDAMENTAL_SLOPE = (0.8, 1.2)
IM3_SLOPE_MAX = 4.0  # no lower bound: slower IM3 growth only makes the scored (lower) extrapolation pessimistic
TONE_GAIN_DIFFERENCE_MAX_DB = 1.5
COMPRESSION_MAX_DB = 1.0

CORNERS = ("tt", "ss", "ff", "sf", "fs")
SUPPLY_TEMPERATURE = ((1.62, -40), (1.62, 125), (1.98, -40), (1.98, 125))
LOW_DRIVE = (0.30, "45u")   # LO amplitude per side (V peak), reference current
HIGH_DRIVE = (0.40, "55u")


@dataclass(frozen=True)
class Point:
    corner: str = "tt"
    supply: float = 1.80
    temperature: int = 27
    rf_ghz: float = 2.40
    lo_amp: float = 0.35
    iref: str = "50u"
    rf_imbalance: float = 0.0  # fractional differential amplitude imbalance
    lo_imbalance: float = 0.0
    lo_phase_error: float = 0.0  # degrees
    seed: int = 1

    @property
    def name(self) -> str:
        text = (f"{self.corner}/{self.supply:.2f}V/{self.temperature:+d}C/{self.rf_ghz:.2f}GHz"
                f"/LO{self.lo_amp:.2f}V/{self.iref}A")
        if self.rf_imbalance or self.lo_imbalance or self.lo_phase_error:
            text += f"/imb{self.rf_imbalance:+.0%},{self.lo_imbalance:+.0%},{self.lo_phase_error:+g}deg"
        if self.corner == "tt_mm":
            text += f"/seed{self.seed}"
        return text


NOMINAL = Point()


def flipped(sign_rf: int, sign_lo: int, sign_phase: int) -> dict[str, float]:
    return dict(rf_imbalance=0.02 * sign_rf, lo_imbalance=0.02 * sign_lo, lo_phase_error=2.0 * sign_phase)


# Single-tone points. Every corner at both supply and both temperature extremes, once at low drive
# (smallest LO, smallest reference current) and once at high drive, alternating the RF band edges.
GRID_POINTS = tuple(
    Point(corner, supply, temperature, rf, lo_amp, iref)
    for k, (corner, (supply, temperature)) in enumerate(product(CORNERS, SUPPLY_TEMPERATURE))
    for (lo_amp, iref), rf in ((LOW_DRIVE, (2.30, 2.50)[k % 2]), (HIGH_DRIVE, (2.50, 2.30)[k % 2]))
)
IMBALANCE_POINTS = (
    Point("tt", 1.98, 125, 2.45, 0.35, "50u", **flipped(1, 1, 1)),
    Point("ss", 1.62, 125, 2.35, 0.30, "45u", **flipped(-1, 1, -1)),
    Point("ff", 1.98, -40, 2.45, 0.40, "55u", **flipped(1, -1, -1)),
    Point("sf", 1.62, -40, 2.35, 0.30, "55u", **flipped(-1, -1, 1)),
    Point("fs", 1.98, 125, 2.40, 0.40, "45u", **flipped(1, 1, -1)),
)
MISMATCH_POINTS = tuple(
    Point("tt_mm", 1.80, 27, (2.35, 2.40, 2.45)[k % 3], 0.35, "50u",
          **flipped((1, -1)[k % 2], (1, 1, -1, -1)[k % 4], (1, -1, -1, 1, 1)[k % 5]), seed=seed)
    for k, seed in enumerate(range(52001, 52013))
)
RF_POINTS = (NOMINAL,) + GRID_POINTS + IMBALANCE_POINTS + MISMATCH_POINTS

# Linearity: the nominal point and every corner at both supply and both temperature extremes,
# alternating low and high drive.
LINEARITY_POINTS = (NOMINAL,) + tuple(
    Point(corner, supply, temperature, 2.40, *(LOW_DRIVE, HIGH_DRIVE)[k % 2])
    for k, (corner, (supply, temperature)) in enumerate(product(CORNERS, SUPPLY_TEMPERATURE))
)

RF_METRICS = (
    "conversion_gain_db", "lo_if_db", "rf_if_db", "lo_rf_db", "spur_dbc", "output_offset_v",
    "output_common_mode_v", "output_headroom_v", "supply_power_w",
    "rfp_power_w", "rfn_power_w", "lop_power_w", "lon_power_w",
)
LINEARITY_METRICS = (
    "low_iip3_dbm", "high_iip3_dbm", "fundamental_slope", "im3_slope", "tone_gain_difference_db",
    "compression_db",
)
CHECK_NAMES = (
    "conversion_gain_3db", "isolation_45db", "spur_30dbc", "output_operating_point",
    "power_2mw", "pin_power_10uw", "iip3_m2dbm", "compression_1db",
)


def ghz(value: float) -> str:
    return f"{value:.12g}Gig"


def common_edits(point: Point) -> dict[str, str]:
    return {
        f'.lib "{MODEL}" tt': f'.lib "{MODEL}" {point.corner}',
        ".param supply=1.8": f".param supply={point.supply:.12g}",
        ".param temperature=27": f".param temperature={point.temperature}",
        ".param iref=50u": f".param iref={point.iref}",
        ".param lo_amp=0.35": f".param lo_amp={point.lo_amp:.12g}",
    }


def rf_edits(point: Point) -> dict[str, str]:
    lo = round(point.rf_ghz - 0.2, 6)
    return common_edits(point) | {
        ".option seed=1": f".option seed={point.seed}",
        ".param rf_freq=2.4Gig": f".param rf_freq={ghz(point.rf_ghz)}",
        ".param lo_freq=2.2Gig": f".param lo_freq={ghz(lo)}",
        ".param rf_imbalance=0": f".param rf_imbalance={point.rf_imbalance:.12g}",
        ".param lo_imbalance=0": f".param lo_imbalance={point.lo_imbalance:.12g}",
        ".param lo_phase_error=0": f".param lo_phase_error={point.lo_phase_error:.12g}",
        "AT=2.4Gig": f"AT={ghz(point.rf_ghz)}",
        "AT=2.2Gig": f"AT={ghz(lo)}",
    }


def simulate(bench: str, point: Point) -> dict[str, object]:
    edits = rf_edits(point) if bench == "rf" else common_edits(point)
    with tempfile.TemporaryDirectory(prefix=f"gilbert-{bench}-") as work:
        values = run_spice(HERE / "benches" / f"tb_{bench}.spi", work, edits)
    return {"name": point.name, **values}


def complete(rows, metrics) -> bool:
    return all(all(metric in row for metric in metrics) for row in rows)


def worst(rows, metric: str, minimum: bool):
    row = (min if minimum else max)(rows, key=lambda item: float(item[metric]))
    return float(row[metric]), row["name"]


def at_most(check: str, rows, metric: str, limit: float, scale: float, unit: str):
    value, where = worst(rows, metric, minimum=False)
    return check, value <= limit, f"worst={value * scale:.4g}{unit} at {where} (max {limit * scale:g}{unit})"


def at_least(check: str, rows, metric: str, limit: float, scale: float, unit: str):
    value, where = worst(rows, metric, minimum=True)
    return check, value >= limit, f"worst={value * scale:.4g}{unit} at {where} (min {limit * scale:g}{unit})"


def rf_checks(rows) -> list[tuple[str, bool, str]]:
    for row in rows:
        row["isolation_db"] = max(float(row[key]) for key in ("lo_if_db", "rf_if_db", "lo_rf_db"))
        row["pin_power_w"] = max(float(row[f"{pin}_power_w"]) for pin in ("rfp", "rfn", "lop", "lon"))
    isolation = {key: worst(rows, key, minimum=False) for key in ("lo_if_db", "rf_if_db", "lo_rf_db")}
    offset, offset_at = worst(rows, "output_offset_v", minimum=False)
    common_mode, common_mode_at = worst(rows, "output_common_mode_v", minimum=True)
    headroom, headroom_at = worst(rows, "output_headroom_v", minimum=True)
    pins = {pin: worst(rows, f"{pin}_power_w", minimum=False) for pin in ("rfp", "rfn", "lop", "lon")}
    return [
        at_least("conversion_gain_3db", rows, "conversion_gain_db", GAIN_MIN_DB, 1, "dB"),
        ("isolation_45db", max(value for value, _ in isolation.values()) <= ISOLATION_MAX_DB,
         "; ".join(f"{key[:-3].replace('_', '-').upper()}={value:.4g}dB at {where}"
                   for key, (value, where) in isolation.items()) + f" (max {ISOLATION_MAX_DB:g}dB)"),
        at_most("spur_30dbc", rows, "spur_dbc", SPUR_MAX_DBC, 1, "dBc"),
        ("output_operating_point",
         offset <= OFFSET_MAX_V and common_mode >= COMMON_MODE_MIN_V and headroom >= HEADROOM_MIN_V,
         f"offset={offset * 1e3:.3g}mV at {offset_at} (max {OFFSET_MAX_V * 1e3:g}); "
         f"common mode={common_mode:.3g}V at {common_mode_at} (min {COMMON_MODE_MIN_V:g}); "
         f"headroom={headroom:.3g}V at {headroom_at} (min {HEADROOM_MIN_V:g})"),
        at_most("power_2mw", rows, "supply_power_w", POWER_MAX_W, 1e3, "mW"),
        ("pin_power_10uw", max(value for value, _ in pins.values()) <= PIN_POWER_MAX_W,
         "; ".join(f"{pin}={value * 1e6:.3g}uW at {where}" for pin, (value, where) in pins.items())
         + f" (max {PIN_POWER_MAX_W * 1e6:g}uW each)"),
    ]


def linearity_checks(rows) -> list[tuple[str, bool, str]]:
    for row in rows:
        row["iip3_dbm"] = min(float(row["low_iip3_dbm"]), float(row["high_iip3_dbm"]))
    iip3, iip3_at = worst(rows, "iip3_dbm", minimum=True)
    slopes = {key: (min(float(r[key]) for r in rows), max(float(r[key]) for r in rows))
              for key in ("fundamental_slope", "im3_slope")}
    flatness, flatness_at = worst(rows, "tone_gain_difference_db", minimum=False)
    valid = (FUNDAMENTAL_SLOPE[0] <= slopes["fundamental_slope"][0] <= slopes["fundamental_slope"][1]
             <= FUNDAMENTAL_SLOPE[1] and slopes["im3_slope"][1] <= IM3_SLOPE_MAX
             and flatness <= TONE_GAIN_DIFFERENCE_MAX_DB)
    return [
        ("iip3_m2dbm", iip3 >= IIP3_MIN_DBM and valid,
         f"worst={iip3:.4g}dBm at {iip3_at} (min {IIP3_MIN_DBM:g}dBm); "
         f"fundamental slope {slopes['fundamental_slope'][0]:.3g}..{slopes['fundamental_slope'][1]:.3g} "
         f"(in {FUNDAMENTAL_SLOPE[0]:g}..{FUNDAMENTAL_SLOPE[1]:g}); IM3 slope {slopes['im3_slope'][0]:.3g}.."
         f"{slopes['im3_slope'][1]:.3g} (max {IM3_SLOPE_MAX:g}); two-tone gain difference "
         f"{flatness:.3g}dB at {flatness_at} (max {TONE_GAIN_DIFFERENCE_MAX_DB:g})"),
        at_most("compression_1db", rows, "compression_db", COMPRESSION_MAX_DB, 1, "dB"),
    ]


def blocked(reason: str, names=CHECK_NAMES) -> list[tuple[str, bool, str]]:
    return [(check, False, f"blocked: {reason}") for check in names]


def finish(results, processes: int, started: float) -> None:
    write_results(results)
    print(f"ngspice_processes={processes} wall_clock_s={time.monotonic() - started:.3f}")


def main() -> None:
    started = time.monotonic()
    # Gate: the nominal single-tone point must simulate and convert.
    nominal = simulate("rf", NOMINAL)
    if not complete([nominal], RF_METRICS) or float(nominal["conversion_gain_db"]) < GAIN_MIN_DB:
        gain = nominal.get("conversion_gain_db")
        reason = ("nominal point produced no complete measurement" if not complete([nominal], RF_METRICS)
                  else f"nominal conversion gain {float(gain):.3g}dB below {GAIN_MIN_DB:g}dB")
        finish(blocked(reason), 1, started)
        return

    jobs = [("rf", point) for point in RF_POINTS[1:]] + [("linearity", point) for point in LINEARITY_POINTS]
    with ThreadPoolExecutor(JOBS) as pool:
        results = list(pool.map(lambda job: simulate(*job), jobs))
    rf_rows = [nominal] + results[:len(RF_POINTS) - 1]
    linearity_rows = results[len(RF_POINTS) - 1:]

    checks = []
    if complete(rf_rows, RF_METRICS):
        checks += rf_checks(rf_rows)
    else:
        missing = [row["name"] for row in rf_rows if not all(metric in row for metric in RF_METRICS)]
        checks += blocked(f"{len(missing)} single-tone run(s) incomplete, first {missing[0]}", CHECK_NAMES[:6])
    if complete(linearity_rows, LINEARITY_METRICS):
        checks += linearity_checks(linearity_rows)
    else:
        missing = [row["name"] for row in linearity_rows if not all(m in row for m in LINEARITY_METRICS)]
        checks += blocked(f"{len(missing)} linearity run(s) incomplete, first {missing[0]}", CHECK_NAMES[6:])
    finish(checks, 1 + len(jobs), started)


if __name__ == "__main__":
    main()
