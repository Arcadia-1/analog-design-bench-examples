#!/usr/bin/env python3
"""Electrical signoff for the current-biased five-transistor OTA."""

import tempfile
import time
from itertools import product
from pathlib import Path

from utils import run_spice, write_results


HERE = Path(__file__).resolve().parent
MODEL = "/opt/sky130/continuous/sky130.lib.spice"
CORNERS = ("tt", "ss", "ff", "sf", "fs")
SUPPLIES = (1.62, 1.80, 1.98)
TEMPERATURES = (-40, 27, 85, 125)  # the order of the temperature vector in tb_op_ac_noise.spi
GRID = list(product(CORNERS, SUPPLIES, TEMPERATURES))
NOMINAL = ("tt", 1.80, 27)
NOMINAL_BIAS = "50u"
# Reference-current tolerance inside the public 45-55 uA range.
GRID_BIASES = ("45u", "55u")
REJECTION_BIASES = ("45u", "50u", "55u")  # the order of the bias vector in the rejection benches
# Rejection: the nominal point, then every corner at both supply and both temperature extremes, with
# both ends of the reference-current tolerance.
REJECTION_POINTS = ((NOMINAL, NOMINAL_BIAS),) + tuple(
    ((corner, vdd, temp), bias)
    for corner, vdd, temp, bias in product(
        CORNERS, (SUPPLIES[0], SUPPLIES[-1]), (TEMPERATURES[0], TEMPERATURES[-1]), GRID_BIASES
    )
)
REJECTION_BENCHES = ("cmrr", "psrr_plus", "psrr_minus")
GRID_METRICS = ("power_w", "dc_gain_db", "ugb_hz", "phase_margin_deg", "input_noise_vrms")
GRID_LIMITS = (
    ("gain_40db", "dc_gain_db", 40, True, 1, "dB"),
    ("ugb_100mhz", "ugb_hz", 100e6, True, 1e-6, "MHz"),
    ("phase_margin_60deg", "phase_margin_deg", 60, True, 1, "deg"),
    ("power_0p5mw", "power_w", 0.5e-3, False, 1e3, "mW"),
    ("noise_50uvrms", "input_noise_vrms", 50e-6, False, 1e6, "uVrms"),
)
CHECK_NAMES = tuple(spec[0] for spec in GRID_LIMITS) + ("cmrr_40db", "psrr_30db")


def name(point: tuple[str, float, int], bias: str) -> str:
    corner, vdd, temp = point
    return f"{corner}/{vdd:.2f}V/{temp:+d}C/{bias}A"


def index(point: tuple[str, float, int]) -> int:
    _, vdd, temp = point
    return SUPPLIES.index(vdd) * len(TEMPERATURES) + TEMPERATURES.index(temp)


def rejection_index(point: tuple[str, float, int], bias: str) -> int:
    """The point's number in the rejection benches: 12 * supply index + 3 * temperature index + bias index."""
    _, vdd, temp = point
    return 12 * SUPPLIES.index(vdd) + 3 * TEMPERATURES.index(temp) + REJECTION_BIASES.index(bias)


def edits(point: tuple[str, float, int], bias: str) -> dict[str, str]:
    corner, vdd, temp = point
    return {
        f'.lib "{MODEL}" tt': f'.lib "{MODEL}" {corner}',
        ".param supply=1.8": f".param supply={vdd:.12g}",
        ".param temperature=27": f".param temperature={temp}",
        ".param ibias=50u": f".param ibias={bias}",
    }


def run_grid(points: list[tuple[str, float, int]], bias: str) -> list[dict[str, object]]:
    """One ngspice process per corner; the bench loops over that corner's supply/temperature points."""
    rows = []
    for corner in CORNERS:
        batch = [point for point in points if point[0] == corner]
        if not batch:
            continue
        replacements = edits(batch[0], bias) | {
            "set points = ( 0 1 2 3 4 5 6 7 8 9 10 11 )":
                "set points = ( " + " ".join(str(index(point)) for point in batch) + " )"
        }
        with tempfile.TemporaryDirectory(prefix="ota5t-grid-") as work:
            values = run_spice(HERE / "benches" / "tb_op_ac_noise.spi", work, replacements)
        for point in batch:
            row = {"name": name(point, bias)}
            for metric in GRID_METRICS:
                key = f"m{index(point)}_{metric}"
                if key in values:
                    row[metric] = values[key]
            rows.append(row)
    return rows


def run_rejection() -> list[dict[str, object]]:
    """One ngspice process per bench and corner; the bench loops over that corner's points."""
    rows = []
    for bench in REJECTION_BENCHES:
        for corner in CORNERS:
            batch = [(point, bias) for point, bias in REJECTION_POINTS if point[0] == corner]
            if not batch:
                continue
            replacements = edits(*batch[0]) | {
                "set points = ( 16 )": "set points = ( " + " ".join(str(rejection_index(*item)) for item in batch) + " )"
            }
            with tempfile.TemporaryDirectory(prefix=f"ota5t-{bench}-") as work:
                values = run_spice(HERE / "benches" / f"tb_{bench}.spi", work, replacements)
            metric = "cmrr_db_1khz" if bench == "cmrr" else "psrr_db_1khz"
            for point, bias in batch:
                row = {"bench": bench, "name": name(point, bias)}
                key = f"r{rejection_index(point, bias)}_{metric}"
                if key in values:
                    row[metric] = values[key]
                rows.append(row)
    return rows


def threshold(rows, expected: int, spec) -> tuple[str, bool, str]:
    check, metric, limit, minimum, scale, unit = spec
    if len(rows) != expected or any(metric not in row for row in rows):
        return check, False, f"missing {metric} in {expected - sum(metric in row for row in rows)} run(s)"
    row = (min if minimum else max)(rows, key=lambda item: float(item[metric]))
    value = float(row[metric])
    passed = value >= limit if minimum else value <= limit
    bound = "min" if minimum else "max"
    return check, passed, f"worst={value * scale:.4g}{unit} at {row['name']} ({bound} {limit * scale:g}{unit})"


def blocked(reason: str, names=CHECK_NAMES) -> list[tuple[str, bool, str]]:
    return [(check, False, f"blocked: {reason}") for check in names]


def finish(results, processes: int, started: float) -> None:
    write_results(results)
    print(f"ngspice_processes={processes} wall_clock_s={time.monotonic() - started:.3f}")


def main() -> None:
    started = time.monotonic()
    # Gate: nominal loop gain and UGB at tt/1.80 V/27 C with 50 uA.
    nominal = run_grid([NOMINAL], NOMINAL_BIAS)
    gate = [threshold(nominal, 1, spec) for spec in GRID_LIMITS[:2]]
    if not all(passed for _, passed, _ in gate):
        reason = "nominal gain/UGB gate failed: " + "; ".join(message for _, _, message in gate)
        finish(blocked(reason), 1, started)
        return

    rows = nominal + [row for bias in GRID_BIASES for row in run_grid(GRID, bias)]
    expected = 1 + len(GRID) * len(GRID_BIASES)
    grid_results = [threshold(rows, expected, spec) for spec in GRID_LIMITS]
    if not all(passed for _, passed, _ in grid_results):
        finish(grid_results + blocked("PVT grid failed", CHECK_NAMES[-2:]), 1 + 2 * len(CORNERS), started)
        return

    rejection = run_rejection()
    cmrr = [row for row in rejection if row["bench"] == "cmrr"]
    psrr = [row for row in rejection if row["bench"] != "cmrr"]
    finish(
        grid_results + [
            threshold(cmrr, len(REJECTION_POINTS), ("cmrr_40db", "cmrr_db_1khz", 40, True, 1, "dB")),
            threshold(psrr, 2 * len(REJECTION_POINTS), ("psrr_30db", "psrr_db_1khz", 30, True, 1, "dB")),
        ],
        1 + 2 * len(CORNERS) + len(REJECTION_BENCHES) * len(CORNERS),
        started,
    )


if __name__ == "__main__":
    main()
