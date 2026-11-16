#!/usr/bin/env python3
"""Signoff of /app/circuit.spi, a fully differential op amp with common-mode feedback.

Runs the benches in benches/ at the nominal point, the PVT and critical-load
points, the mismatch Monte Carlo runs, the temperature extremes and the
operating-range sweeps, and scores the design against SPEC.
"""

import json
import math
import os
import re
import shutil
import subprocess
import tempfile
import time
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

import numpy as np

from utils import write_results

TASK_ID = "sky130-fd-opamp-cmfb-pvt"
SUBCKT = "fd_opamp"
DUT_FILE = "/app/circuit.spi"
BENCHES = Path(__file__).resolve().parent / "benches"
REPORT_DIR = Path("/logs/verifier/reports/analog-signoff")
VCM_SOURCE_OHM = 1e3  # every bench feeds the DUT's vcm pin through this resistor

COND = {
    "model_lib": "/opt/sky130/continuous/sky130.lib.spice",
    "vdd": 1.8,
    "vcm_fraction": 0.5,
    "temperature": 27.0,
    "ibias": 1e-05,
    "cl": 2e-12,
    "rl": 1e6,
    "r_net": 1e5,
    "r_gm": 1e3,
    "c_net": 1e-12,
    "large_step": 0.5,
    "thd_amplitude_pp": 1.0,
    "thd_frequency": 1e4,
    "thd_settle_cycles": 2,
    "thd_measure_cycles": 2,
    "fpbw_amplitude_pp": 1.0,
    "fpbw_thd_limit_db": -40.0,
    "f_low": 0.1,
    "ibias_tracking_fraction": 0.2,
    "icmr_step": 0.025,
    "icmr_window_half_v": 0.05,
    "vcm_tracking_levels_v": [0.8, 1.0],
    "swing_gain_fraction": 0.9,
    "gm_fraction": 0.9,
    "operating_avo_min_db": 70.0,
    "operating_gbw_min_hz": 8e6,
    "operating_pm_min_deg": 60.0,
    "supply_sweep": [1.62, 1.98, 0.09],
    "temp_range_sweep": [-40.0, 125.0, 27.5],
    "power_ramps_s": [0.005, 0.01],
    "power_hold_s": 0.002,
    "power_tmax_s": 1e-06,
    "power_settle_band_v": 0.001,
    "power_cm_tolerance_v": 0.02,
    "oscillation_grid_s": 1e-07,
    "oscillation_window_s": 2e-06,
    "oscillation_threshold_v": 0.002,
    "oscillation_max_busy_s": 2e-05,
    "supply_step_v": 0.05,
    "supply_step_edge_s": 1e-08,
    "supply_step_frequencies": [1e5, 3e5, 1e6],
    "supply_step_periods": 3,
    "supply_step_band_v": 0.0001,
    # The vertices of the supply and temperature ranges at the skew corners.
    "pvt": {"corners": ["ff", "ss", "fs", "sf"], "supplies": [1.62, 1.98], "temperatures": [-40.0, 125.0],
            "load_current": 2e-05, "worst_case_loads": ["+i", "-i", "rl_to_ground"]},
    "mc": {"corner": "tt_mm", "runs": 30, "seed": 64000},
}
SPEC = {
    "avo_min_db": 70.0, "gbw_min_hz": 8e6, "pm_min_deg": 60.0, "gm_min_db": 10.0, "peaking_max_db": 1.0,
    "sr_min_v_per_s": 4e6, "settle_max_s": 1e-06, "icmr_nominal_v": [0.6, 1.6], "icmr_pvt_half_window_v": 0.05,
    "swing_min_v": 2.5, "cm_gain_max_db": -50.0, "psrr_cm_min_db": 30.0, "cm_error_max_v": 0.02,
    "en_max_v_rthz": 6e-08, "thd_max_db": -70.0, "fpbw_min_hz": 3e5, "offset_sigma_max_v": 0.005,
    "vos_max_v": 0.002, "icc_max_a": 0.00045, "vcm_current_max_a": 1e-06, "icc_tracking_min": 0.7,
    "supply_range_v": [1.62, 1.98], "temp_range_c": [-40.0, 125.0],
    "power_cm_settle_max_s": 1e-3,
}


# ======================================================================
# ngspice decks and raw files
# ======================================================================

TOKEN = re.compile(r"\{\{([A-Z0-9_]+)\}\}")
# ngspice reads ./.spiceinit in preference to ~/.spiceinit, so every work directory gets one.
SPICEINIT = "set ngbehavior=hsa\nset num_threads=1\n"


class SpiceError(RuntimeError):
    """Raised when ngspice fails or a deck leaves a token unresolved."""


def render(template: str, tokens: dict[str, object]) -> str:
    def replace(match: re.Match[str]) -> str:
        key = match.group(1)
        if key not in tokens:
            raise SpiceError(f"template token {{{{{key}}}}} has no value")
        value = tokens[key]
        return f"{value:.10g}" if isinstance(value, float) else str(value)

    return TOKEN.sub(replace, template)


@dataclass
class Plot:
    name: str
    vectors: dict[str, np.ndarray]

    def __getitem__(self, key: str) -> np.ndarray:
        return self.vectors[key.lower()]

    @property
    def scale(self) -> np.ndarray:
        return next(iter(self.vectors.values()))


def read_raw(path: Path) -> list[Plot]:
    """Read every plot of an ngspice ASCII raw file, in file order."""
    lines = path.read_text(errors="replace").splitlines()
    plots: list[Plot] = []
    index = 0
    while index < len(lines):
        if not lines[index].startswith("Title:"):
            index += 1
            continue
        header: dict[str, str] = {}
        index += 1
        while index < len(lines) and not lines[index].startswith("Variables:"):
            key, _, value = lines[index].partition(":")
            header[key.strip().lower()] = value.strip()
            index += 1
        count = int(header["no. variables"])
        points = int(header["no. points"])
        is_complex = "complex" in header.get("flags", "").lower()
        names = [lines[index + 1 + k].split()[1].lower() for k in range(count)]
        index += 1 + count
        while index < len(lines) and not lines[index].startswith("Values:"):
            index += 1
        index += 1
        tokens: list[str] = []
        needed = points * (count + 1)
        while index < len(lines) and len(tokens) < needed:
            if lines[index].startswith("Title:"):
                break
            tokens.extend(lines[index].split())
            index += 1
        if len(tokens) < needed:
            raise SpiceError(f"{path}: plot {header.get('plotname')} is truncated")
        table = np.array(tokens[:needed], dtype=object).reshape(points, count + 1)[:, 1:]
        if is_complex:
            pairs = np.array(
                [token.split(",") for token in table.ravel()], dtype=float
            ).reshape(points, count, 2)
            data = pairs[..., 0] + 1j * pairs[..., 1]
        else:
            data = table.astype(float)
        vectors = {name: data[:, column] for column, name in enumerate(names)}
        if is_complex:
            vectors[names[0]] = vectors[names[0]].real  # the scale stays real in a complex plot
        plots.append(Plot(header.get("plotname", ""), vectors))
    return plots


@dataclass
class Run:
    deck: Path
    log: str
    plots: list[Plot]

    def plot(self, prefix: str) -> Plot:
        for plot in self.plots:
            if plot.name.lower().startswith(prefix.lower()):
                return plot
        raise SpiceError(f"{self.deck.name}: no '{prefix}' plot #0\n{tail(self.log)}")


def tail(text: str, lines: int = 25) -> str:
    return "\n".join(text.splitlines()[-lines:])


def run_deck(text: str, work: Path, name: str) -> Run:
    """Write ``text`` to ``work/name.spi``, run ngspice, and read the ``name.raw``
    that the bench's ``write {{RAW}}`` produces."""
    work.mkdir(parents=True, exist_ok=True)
    (work / ".spiceinit").write_text(SPICEINIT)
    deck = work / f"{name}.spi"
    raw = work / f"{name}.raw"
    if raw.exists():
        raw.unlink()
    deck.write_text(text)
    result = subprocess.run(
        ["ngspice", "-b", deck.name],
        cwd=work,
        env=dict(os.environ, OMP_NUM_THREADS="1"),
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    log = result.stdout
    (work / f"{name}.log").write_text(log)
    if result.returncode != 0 or not raw.exists():
        raise SpiceError(f"ngspice failed on {deck}\n{tail(log)}")
    return Run(deck, log, read_raw(raw))


# ======================================================================
# waveform and frequency-response measurements
# ======================================================================

trapezoid = getattr(np, "trapezoid", None) or np.trapz
NO_PHASE_CROSSING_GM_DB = 999.0  # reported gain margin when the phase never reaches -180 deg


def db(value) -> np.ndarray:
    return 20.0 * np.log10(np.maximum(np.abs(value), 1e-300))


def unwrapped_phase_deg(response: np.ndarray) -> np.ndarray:
    """Phase in degrees, unwrapped and shifted so the first point lies in (-180, 180]."""
    phase = np.degrees(np.unwrap(np.angle(response)))
    return phase - 360.0 * round(phase[0] / 360.0)


def crossing(x: np.ndarray, y: np.ndarray, level: float, *, rising: bool | None = None,
             start: int = 0, log_x: bool = False) -> float | None:
    """First ``x`` after index ``start`` where ``y`` crosses ``level`` (linear interpolation)."""
    above = y[start:] >= level
    for k in range(1, len(above)):
        if above[k] == above[k - 1]:
            continue
        if rising is True and not above[k]:
            continue
        if rising is False and above[k]:
            continue
        i = start + k
        y0, y1 = y[i - 1], y[i]
        frac = 0.0 if y1 == y0 else (level - y0) / (y1 - y0)
        if log_x:
            return float(10 ** (math.log10(x[i - 1]) + frac * (math.log10(x[i]) - math.log10(x[i - 1]))))
        return float(x[i - 1] + frac * (x[i] - x[i - 1]))
    return None


def value_at(freq: np.ndarray, y: np.ndarray, f0: float) -> float:
    """``y`` at ``f0``, interpolated on a logarithmic frequency axis."""
    return float(np.interp(math.log10(f0), np.log10(freq), y))


def loop_metrics(freq: np.ndarray, response: np.ndarray) -> dict[str, float | None]:
    """Gain, unity-gain frequency, phase margin and gain margin of a response whose
    low-frequency phase is near 0 degrees (non-inverting open-loop gain)."""
    mag = db(response)
    phase = unwrapped_phase_deg(response)
    result: dict[str, float | None] = {
        "dc_gain_db": float(mag[0]),
        "ugf_hz": None,
        "phase_margin_deg": None,
        "f180_hz": None,
        "gain_margin_db": None,
        "unity_crossings": int(np.count_nonzero(np.diff(np.sign(mag)) != 0)),
    }
    ugf = crossing(freq, mag, 0.0, rising=False, log_x=True)
    if ugf is not None:
        result["ugf_hz"] = ugf
        result["phase_margin_deg"] = 180.0 + value_at(freq, phase, ugf)
    f180 = crossing(freq, phase, -180.0, rising=False, log_x=True)
    if f180 is not None:
        result["f180_hz"] = f180
        result["gain_margin_db"] = -value_at(freq, mag, f180)
    elif ugf is not None:
        result["gain_margin_db"] = NO_PHASE_CROSSING_GM_DB
    return result


def bandwidths(freq: np.ndarray, response: np.ndarray) -> dict[str, float | None]:
    """-3 dB and 0.1 dB-flatness bandwidths, peaking, and group delay of a closed loop."""
    mag = db(response)
    ref = float(mag[0])
    phase = np.unwrap(np.angle(response))
    group_delay = -np.gradient(phase, 2.0 * math.pi * freq)
    f3 = crossing(freq, mag, ref - 3.0103, rising=False, log_x=True)
    flat = None
    outside = np.nonzero(np.abs(mag - ref) > 0.1)[0]
    if len(outside):
        i = outside[0]
        if i > 0:
            level = ref + (0.1 if mag[i] > ref else -0.1)
            flat = crossing(freq, mag, level, start=i - 1, log_x=True)
    passband = freq <= (f3 if f3 is not None else freq[-1])
    return {
        "f_3db_hz": f3,
        "f_0p1db_hz": flat,
        "peaking_db": float(max(mag.max() - ref, 0.0)),
        "group_delay_lf_s": float(group_delay[1]),
        "group_delay_max_s": float(group_delay[passband].max()),
    }


def uniform(t: np.ndarray, v: np.ndarray, t0: float, t1: float, points: int) -> np.ndarray:
    return np.interp(np.linspace(t0, t1, points), t, v)


def settling_time(t: np.ndarray, v: np.ndarray, t_edge: float, final: float, band: float) -> float | None:
    """Time from ``t_edge`` after which ``v`` stays inside ``final +/- band``,
    or ``None`` when the record ends outside the band."""
    mask = t >= t_edge
    tt, vv = t[mask], v[mask]
    outside = np.abs(vv - final) > band
    if outside[-1]:
        return None
    hits = np.nonzero(outside)[0]
    if not len(hits):
        return 0.0
    i = hits[-1]
    # Interpolate the last exit into the band.
    e0, e1 = abs(vv[i] - final) - band, abs(vv[i + 1] - final) - band
    frac = 0.0 if e0 == e1 else e0 / (e0 - e1)
    return float(tt[i] + frac * (tt[i + 1] - tt[i]) - t_edge)


def step_metrics(t: np.ndarray, v: np.ndarray, t_edge: float, t_end: float, v_start: float,
                 v_command: float) -> dict[str, float | None]:
    """Maximum slew, overshoot and 0.1 % settling of one edge between ``t_edge`` and ``t_end``."""
    mask = (t >= t_edge) & (t <= t_end)
    tt, vv = t[mask], v[mask]
    step = float(vv[tt >= t_edge + 0.95 * (t_end - t_edge)].mean()) - v_start
    amp = abs(step)
    norm = (vv - v_start) * (1.0 if step >= 0 else -1.0)
    t10 = crossing(tt, norm, 0.1 * amp, rising=True)
    t90 = crossing(tt, norm, 0.9 * amp, rising=True)
    slew = None
    if t10 is not None and t90 is not None and t90 > t10:
        # Maximum slope inside the 10-90 % window on a uniform grid; a short
        # moving average keeps single-step numerical noise out of the maximum.
        grid = np.linspace(t10, t90, 2001)
        slope = np.gradient(np.interp(grid, tt, norm), grid)
        slew = float(np.convolve(slope, np.ones(9) / 9, mode="valid").max())
    return {
        "slew_max_v_per_s": slew,
        "overshoot_pct": float(max(norm.max() - amp, 0.0) / amp * 100.0) if amp > 0 else None,
        # Settling is judged against the commanded level, never the output's own tail.
        "settle_0p1pct_s": settling_time(tt, vv, t_edge, v_command, 0.001 * abs(v_command - v_start)),
    }


def hermite(x: np.ndarray, xp: np.ndarray, fp: np.ndarray) -> np.ndarray:
    """Cubic Hermite resampling of a (non-uniform) record, with slopes from
    second-order finite differences.  Its error falls as h^3 rather than the
    h^2 of linear interpolation, so the resampling stays far below the
    distortion levels being measured (-160 dB and lower)."""
    keep = np.concatenate([[True], np.diff(xp) > 0])
    xp, fp = xp[keep], fp[keep]
    slope = np.gradient(fp, xp)
    i = np.clip(np.searchsorted(xp, x) - 1, 0, len(xp) - 2)
    h = xp[i + 1] - xp[i]
    s = (x - xp[i]) / h
    return ((2 * s**3 - 3 * s**2 + 1) * fp[i] + (s**3 - 2 * s**2 + s) * h * slope[i]
            + (-2 * s**3 + 3 * s**2) * fp[i + 1] + (s**3 - s**2) * h * slope[i + 1])


def thd(t: np.ndarray, v: np.ndarray, f0: float, t_start: float, cycles: int,
        harmonics: int = 10, points: int = 4096) -> dict[str, float]:
    """THD (harmonics 2..10), HD2 and HD3 over ``cycles`` whole periods from ``t_start``."""
    grid = t_start + np.arange(points) * (cycles / f0 / points)
    samples = hermite(grid, t, v)
    spectrum = np.abs(np.fft.rfft(samples - samples.mean())) * 2.0 / points
    amps = spectrum[[cycles * k for k in range(1, harmonics + 1)]]
    distortion = float(np.sqrt(np.sum(amps[1:] ** 2)))
    ratio = distortion / amps[0] if amps[0] > 0 else float("inf")
    return {
        "thd_db": float(20.0 * np.log10(max(ratio, 1e-12))),
        "hd2_db": float(db(amps[1] / amps[0])),
        "hd3_db": float(db(amps[2] / amps[0])),
    }


def contiguous_range(x: np.ndarray, ok: np.ndarray, x_ref: float) -> tuple[float, float] | None:
    """Largest interval of ``x`` around ``x_ref`` where ``ok`` holds at every sample,
    with the end points interpolated half-way to the first failing sample."""
    i = int(np.argmin(np.abs(x - x_ref)))
    if not ok[i]:
        return None
    lo = i
    while lo > 0 and ok[lo - 1]:
        lo -= 1
    hi = i
    while hi < len(x) - 1 and ok[hi + 1]:
        hi += 1
    x_lo = x[lo] if lo == 0 else 0.5 * (x[lo] + x[lo - 1])
    x_hi = x[hi] if hi == len(x) - 1 else 0.5 * (x[hi] + x[hi + 1])
    return float(min(x_lo, x_hi)), float(max(x_lo, x_hi))


def smooth_derivative(x: np.ndarray, y: np.ndarray, width: int) -> np.ndarray:
    slope = np.gradient(y, x)
    padded = np.pad(slope, width // 2, mode="edge")
    return np.convolve(padded, np.ones(width) / width, mode="valid")


# ======================================================================
# tests
# ======================================================================
# Each test renders one bench from benches/, runs it and returns
# {"metrics": {...}}.  Metric names carry SI units in their suffix.

@dataclass(frozen=True)
class Point:
    corner: str
    vdd: float
    temp: float

    @property
    def tag(self) -> str:
        temp = f"m{abs(self.temp):g}" if self.temp < 0 else f"{self.temp:g}"
        return f"{self.corner}_{self.vdd:.2f}v_{temp}c".replace(".", "p")


@dataclass
class Context:
    point: Point
    work: Path
    seed: int | None = None
    # Results of earlier tests at the same point (GBW, phase margin, slew rate).
    cache: dict = field(default_factory=dict)
    overrides: dict = field(default_factory=dict)

    @property
    def vcm(self) -> float:
        return COND["vcm_fraction"] * self.point.vdd

    def run(self, bench: str, name: str, **extra: object) -> Run:
        tokens: dict[str, object] = {
            "SEED_OPTION": "* nominal models (no Monte Carlo seed)" if self.seed is None
                           else f".option seed={self.seed}",
            "MODEL_LIB": COND["model_lib"],
            "CORNER": self.point.corner,
            "DUT_FILE": DUT_FILE,
            "DUT": SUBCKT,
            "TEMP": self.point.temp,
            "VDD": self.point.vdd,
            "VCM": self.vcm,
            "VCM_FRACTION": COND["vcm_fraction"],
            "IBIAS": COND["ibias"],
            "RL": COND["rl"],
            "CL": COND["cl"],
            "R_NET": COND["r_net"],
            "R_GM": COND["r_gm"],
            "C_NET": COND["c_net"],
            "IOUT_DC": 0.0,
            "RL_NODE": "vcm",
            **self.overrides,
            **extra,
            "RAW": f"{name}.raw",
        }
        return run_deck(render((BENCHES / bench).read_text(), tokens), self.work, name)


def float_range(start: float, stop: float, step: float) -> list[float]:
    count = int(round((stop - start) / step))
    return [round(start + k * step, 6) for k in range(count + 1)]


def freq_label(freq: float) -> str:
    for unit, scale in (("mhz", 1e6), ("khz", 1e3)):
        if freq >= scale:
            return f"{freq / scale:g}{unit}"
    return f"{freq:g}hz"


def first_stable_tau(ctx: Context) -> float:
    """Closed-loop time constant estimate used to size transient windows."""
    ugf = ctx.cache.get("ugf_hz") or 1e6
    beta = ctx.cache.get("feedback_factor", 1.0)
    return 1.0 / (2.0 * math.pi * ugf * beta)


def diff_cm(plot: Plot, p: str = "v(outp)", n: str = "v(outn)") -> tuple[np.ndarray, np.ndarray]:
    return plot[p] - plot[n], 0.5 * (plot[p] + plot[n])


def offset_op(ctx: Context) -> dict:
    op = ctx.run("tb_offset_op.spi", "fd01").plot("Operating")
    vod, vocm = diff_cm(op)
    icc = -float(op["i(vdd)"][0])
    ctx.cache["feedback_factor"] = 0.5
    return {"metrics": {
        "icc_a": icc,
        "pd_w": icc * ctx.point.vdd,
        "vos_out_v": float(vod[0]),
        "vos_in_v": float(vod[0]) / 2.0,  # noise gain 1 + R2/R1 = 2
        "vocm_error_v": float(vocm[0] - ctx.vcm),
        "vbi_v": float(op["v(ibias)"][0]),
        "vcm_pin_current_a": abs(float(op["v(vcm)"][0]) - float(op["v(vcmpin)"][0])) / VCM_SOURCE_OHM,
    }}


def bias_tracking(ctx: Context) -> dict:
    """How the supply current follows the bias current: the exponent n of Icc ~ Ibias^n
    between the two ends of the sweep.  Currents mirrored from ibias give about 1, and
    bias generated any other way gives about 0 (only the bias current itself moves)."""
    fraction = COND["ibias_tracking_fraction"]
    low, high = COND["ibias"] * (1.0 - fraction), COND["ibias"] * (1.0 + fraction)
    icc = [-float(ctx.run("tb_offset_op.spi", f"fd30_{k}", IBIAS=current).plot("Operating")["i(vdd)"][0])
           for k, current in enumerate((low, high))]
    return {"metrics": {
        "icc_low_bias_a": icc[0],
        "icc_high_bias_a": icc[1],
        "icc_tracking": math.log(icc[1] / icc[0]) / math.log(high / low),
    }}


def vcm_tracking(ctx: Context) -> dict:
    """The output common mode follows vcm when vcm is not VDD/2 (zero input, gain -1 network)."""
    metrics = {}
    for level in COND["vcm_tracking_levels_v"]:
        tag = f"{level:g}".replace(".", "p")
        op = ctx.run("tb_offset_op.spi", f"fd31_{tag}", VCM=level).plot("Operating")
        _, vocm = diff_cm(op)
        metrics[f"vocm_error_at_{tag}v_v"] = float(vocm[0] - level)
        metrics[f"vbi_at_{tag}v_v"] = float(op["v(ibias)"][0])
    return {"metrics": metrics}


def icmr_sweep(run: Run, levels: list[float]) -> tuple[list[Plot | None], list[Plot | None]]:
    """The operating-point and AC plot of each common-mode level.  A level where ngspice
    finds no operating point has no plot of its own and reads None, which the range rules
    treat as out of range: such a level far outside the range says nothing about the design."""
    ops: list[Plot | None] = [None] * len(levels)
    acs: list[Plot | None] = [None] * len(levels)
    current = None
    for plot in run.plots:
        kind = plot.name.lower()
        if kind.startswith("operating") and "v(cms)" in plot.vectors:
            value = float(plot["v(cms)"][0])
            current = int(np.argmin(np.abs(np.array(levels) - value)))
            ops[current] = plot
        elif kind.startswith("ac") and current is not None and acs[current] is None:
            acs[current] = plot
    return ops, acs


def sweep_values(plots: list[Plot | None], read: Callable[[Plot], float]) -> np.ndarray:
    return np.array([np.nan if plot is None else read(plot) for plot in plots])


def input_common_mode_range(ctx: Context) -> dict:
    half = COND["icmr_window_half_v"]
    levels = float_range(0.0, ctx.point.vdd, COND["icmr_step"]) + [ctx.vcm - half, ctx.vcm + half]
    levels = sorted(set(round(v, 6) for v in levels))
    run = ctx.run("tb_icmr_gain_sweep.spi", "fd04",
                  VCM_LIST=" ".join(f"{v:.6g}" for v in levels), F_LOW=COND["f_low"])
    ops, acs = icmr_sweep(run, levels)
    # The common mode ngspice applied, or the requested one where it found no operating point.
    vcm = np.array([level if op is None else float(op["v(cms)"][0]) for op, level in zip(ops, levels)])
    vocm = sweep_values(ops, lambda op: 0.5 * float(op["v(outp)"][0] + op["v(outn)"][0]))
    vod = sweep_values(ops, lambda op: float(op["v(outp)"][0] - op["v(outn)"][0]))
    ad_db = db(sweep_values(acs, lambda ac: abs(ac["v(outp)"][0] - ac["v(outn)"][0])))
    ref = float(np.nanmedian(ad_db[np.abs(vcm - ctx.vcm) <= 0.05]))
    ok = (ad_db >= ref - 6.0) & (np.abs(vocm - ctx.vcm) <= 0.05) & (np.abs(vod) <= 0.01)
    span = contiguous_range(vcm, ok, ctx.vcm)
    return {"metrics": {"ad_servo_db": ref, "icmr_lo_v": span[0] if span else None,
                        "icmr_hi_v": span[1] if span else None}}


def output_swing(ctx: Context) -> dict:
    dc = ctx.run("tb_swing_dc.spi", "fd05", R_HALF=COND["r_net"] / 2.0).plot("DC")
    vi = dc.scale.real
    vod, _ = diff_cm(dc)
    gain = smooth_derivative(vi, vod, 5)
    g0 = float(np.interp(ctx.vcm, vi, gain))
    span = contiguous_range(vi, np.abs(gain) >= COND["swing_gain_fraction"] * abs(g0), ctx.vcm)
    metrics = {"closed_loop_gain_mid": g0, "vod_min_v": None, "vod_max_v": None, "vodpp_v": None,
               "vout_min_v": None, "vout_max_v": None, "vopp_v": None}
    if span:
        ends = np.interp(np.array(span), vi, vod)
        metrics["vod_min_v"], metrics["vod_max_v"] = float(ends.min()), float(ends.max())
        metrics["vodpp_v"] = metrics["vod_max_v"] - metrics["vod_min_v"]
        mask = (vi >= span[0]) & (vi <= span[1])
        single = np.concatenate([dc["v(outp)"][mask], dc["v(outn)"][mask]])
        metrics["vout_min_v"], metrics["vout_max_v"] = float(single.min()), float(single.max())
        metrics["vopp_v"] = float(single.max() - single.min())
    return {"metrics": metrics}


def open_loop(ctx: Context) -> dict:
    ac = ctx.run("tb_openloop_ac.spi", "fd07").plot("AC")
    freq = ac.scale.real
    loop = loop_metrics(freq, ac["v(outp)"] - ac["v(outn)"])
    admittance = -ac["i(vapa)"] / 0.5  # current per volt on each input when Vid = 1
    k = int(np.argmin(np.abs(freq - 1e3)))
    conductance = float(admittance[k].real)
    ctx.cache.update(ugf_hz=loop["ugf_hz"], pm_deg=loop["phase_margin_deg"],
                     gain_margin_db=loop["gain_margin_db"], feedback_factor=0.5)
    return {"metrics": {
        "avo_db": loop["dc_gain_db"],
        "gbw_hz": loop["ugf_hz"],
        "pm_deg": loop["phase_margin_deg"],
        "gain_margin_db": loop["gain_margin_db"],
        "f180_hz": loop["f180_hz"],
        "unity_crossings": loop["unity_crossings"],
        "ro_ohm": float(abs(ac["v(opb)"][0] - ac["v(onb)"][0])),
        "cin_f": float(abs(admittance[k].imag) / (2.0 * math.pi * freq[k])),
        "rid_ohm": float(2.0 / conductance) if conductance > 1e-18 else float("inf"),
    }}


def closed_loop(ctx: Context) -> dict:
    ac = ctx.run("tb_closedloop_ac.spi", "fd08").plot("AC")
    bw = bandwidths(ac.scale.real, ac["v(outp)"] - ac["v(outn)"])
    return {"metrics": {
        "cl_f3db_hz": bw["f_3db_hz"],
        "cl_f0p1db_hz": bw["f_0p1db_hz"],
        "cl_peaking_db": bw["peaking_db"],
        "group_delay_lf_s": bw["group_delay_lf_s"],
        "group_delay_max_s": bw["group_delay_max_s"],
    }}


def cm_rejection(ctx: Context) -> dict:
    ac = ctx.run("tb_cmrr.spi", "fd09").plot("AC")
    vod, vocm = diff_cm(ac)
    cm_cm = db(vocm)
    return {"metrics": {
        "acm_cm_db": float(cm_cm[0]),
        "acm_cm_1mhz_db": value_at(ac.scale.real, cm_cm, 1e6),
        "cmrr_dm_db": float(-db(vod)[0]),
    }}


def supply_rejection(ctx: Context) -> dict:
    ac = ctx.run("tb_psrr.spi", "fd10").plot("AC")
    freq = ac.scale.real
    vod_p, vocm_p = diff_cm(ac, "v(opp)", "v(onp)")
    _, vocm_n = diff_cm(ac, "v(opn)", "v(onn)")
    plus, minus = -db(vocm_p), -db(vocm_n)
    return {"metrics": {
        "psrr_plus_cm_db": float(plus[0]),
        "psrr_plus_cm_1mhz_db": value_at(freq, plus, 1e6),
        "psrr_minus_cm_db": float(minus[0]),
        "psrr_minus_cm_1mhz_db": value_at(freq, minus, 1e6),
        "psrr_plus_dm_db": float(-db(vod_p)[0]),
    }}


def noise(ctx: Context) -> dict:
    plot = ctx.run("tb_noise.spi", "fd12").plot("Noise Spectral")
    freq = plot.scale.real
    onoise = np.abs(plot["onoise_spectrum"])
    inoise = np.abs(plot["inoise_spectrum"])
    f_lo, f_hi = 1.0, 1e9
    band = (freq >= f_lo) & (freq <= f_hi)
    vn_out = float(np.sqrt(trapezoid(onoise[band] ** 2, freq[band])))
    ugf = ctx.cache.get("ugf_hz") or 1e6
    white_band = (freq >= 1e4) & (freq <= max(min(1e6, ugf / 10.0), 2e4))
    floor = float(np.min(inoise[white_band])) if np.any(white_band) else float(inoise[-1])
    above = np.nonzero((inoise > math.sqrt(2.0) * floor) & (freq < 1e6))[0]
    gain = value_at(freq, onoise, 1e3) / max(value_at(freq, inoise, 1e3), 1e-30)
    return {"metrics": {
        "en_1khz_v_rthz": value_at(freq, inoise, 1e3),
        "en_1mhz_v_rthz": value_at(freq, inoise, 1e6),
        "en_white_v_rthz": floor,
        "flicker_corner_hz": float(freq[above[-1]]) if len(above) else None,
        "vn_out_rms_v": vn_out,
        "vn_in_equivalent_rms_v": vn_out / gain if gain > 0 else None,
        "vn_out_avg_density_v_rthz": vn_out / math.sqrt(f_hi - f_lo),
    }}


def step_edges(ctx: Context, name: str, vod_step: float) -> tuple[dict, dict]:
    """One rising and one falling edge of Vod by ``vod_step``.  Each input moves by
    vod_step/2 in antiphase, and each half of the network inverts, so Vod = +Vid.
    The window grows until both edges settle to 0.1 %, at most three tries."""
    tau = first_stable_tau(ctx)
    half = min(max(1500.0 * tau, 200e-9), 400e-6)
    amp = vod_step / 2.0
    for attempt in range(3):
        edge = max(tau / 20.0, 1e-12)
        delay = half / 10.0
        tmax = min(tau / 10.0, half / 2000.0)
        tran = ctx.run("tb_step.spi", f"{name}_{attempt}", V_HI=amp, V_HI_NEG=-amp, T_DELAY=delay,
                       T_EDGE=edge, T_WIDTH=half, T_PERIOD=4.0 * (half + edge), TSTEP=tmax,
                       TSTOP=delay + 2.0 * (half + edge), TMAX=tmax).plot("Transient")
        t = tran.scale.real
        v = tran["v(outp)"] - tran["v(outn)"]
        before = float(v[(t > 0.5 * delay) & (t < delay)].mean())
        t_fall = delay + edge + half
        rise = step_metrics(t, v, delay, t_fall, before, before + vod_step)
        mid = float(v[(t > t_fall - 0.05 * half) & (t < t_fall)].mean())
        fall = step_metrics(t, v, t_fall, t[-1], mid, before)
        if rise["settle_0p1pct_s"] is not None and fall["settle_0p1pct_s"] is not None:
            break
        half *= 4.0
    return rise, fall


def large_step(ctx: Context) -> dict:
    rise, fall = step_edges(ctx, "fd14", 2.0 * COND["large_step"])
    # Slew is reported per output (Vod/2).
    sr_up, sr_down = rise["slew_max_v_per_s"], fall["slew_max_v_per_s"]
    ctx.cache["sr_v_per_s"] = 0.5 * min(sr_down or float("inf"), sr_up or float("inf"))
    return {"metrics": {
        "sr_rise_v_per_s": None if sr_up is None else 0.5 * sr_up,
        "sr_fall_v_per_s": None if sr_down is None else 0.5 * sr_down,
        "sr_diff_v_per_s": None if None in (sr_up, sr_down) else min(sr_up, sr_down),
        "large_settle_rise_0p1pct_s": rise["settle_0p1pct_s"],
        "large_settle_fall_0p1pct_s": fall["settle_0p1pct_s"],
        "large_overshoot_pct": max(rise["overshoot_pct"] or 0.0, fall["overshoot_pct"] or 0.0),
    }}


def sine_thd(ctx: Context, name: str, amp: float, freq: float) -> dict:
    """THD of Vod in the gain -1 network with each input moving ``amp`` peak in antiphase."""
    settle, measure = COND["thd_settle_cycles"], COND["thd_measure_cycles"]
    period = 1.0 / freq
    tmax = period / 4000
    tran = ctx.run("tb_inverting_sine.spi", name, AMP=amp, FREQ=freq, SETTLE_CYCLES=settle, TSTEP=tmax,
                   TSTOP=(settle + measure) * period, TMAX=tmax).plot("Transient")
    return thd(tran.scale.real, tran["v(outp)"] - tran["v(outn)"], freq, settle / freq, measure)


def distortion(ctx: Context) -> dict:
    freq = COND["thd_frequency"]
    res = sine_thd(ctx, "fd15_0", COND["thd_amplitude_pp"] / 2.0, freq)
    label = freq_label(freq)
    return {"metrics": {f"thd_{label}_db": res["thd_db"], f"hd2_{label}_db": res["hd2_db"],
                        f"hd3_{label}_db": res["hd3_db"]}}


def highest_passing_frequency(passes: Callable[[float], bool], start: float) -> float | None:
    """Highest frequency that ``passes``: decade steps from ``start``, down while it
    fails and up while it passes, then six log-bisection steps inside the failing
    decade (3.7 %).  None when 1 kHz already fails."""
    lo, hi = start, None  # hi: the lowest frequency known to fail
    while not passes(lo):
        if lo <= 1e3:
            return None
        hi, lo = lo, lo / 10.0
    if hi is None:
        hi = lo
        while hi < 1e9:
            hi *= 10.0
            if not passes(hi):
                break
            lo = hi
        else:
            return lo
    for _ in range(6):
        mid = math.sqrt(lo * hi)
        if passes(mid):
            lo = mid
        else:
            hi = mid
    return math.sqrt(lo * hi)


def full_power_bandwidth(ctx: Context) -> dict:
    """Highest frequency whose THD at the full-power amplitude stays within the limit.

    The search starts at the decade below a third of the slew-limited estimate
    SR / (2 pi A) when the slew rate is known, so the long low-frequency
    transients run only when they matter.
    """
    amp = COND["fpbw_amplitude_pp"] / 2.0
    sr = ctx.cache.get("sr_v_per_s")
    sr_hz = sr / (2.0 * math.pi * amp) if sr and math.isfinite(sr) else None
    runs = 0

    def passes(freq: float) -> bool:
        nonlocal runs
        runs += 1
        return sine_thd(ctx, f"fd16_{runs - 1}", amp, freq)["thd_db"] <= COND["fpbw_thd_limit_db"]

    start = 1e3 if sr_hz is None else max(1e3, 10.0 ** math.floor(math.log10(sr_hz / 3.0)))
    return {"metrics": {"fpbw_hz": highest_passing_frequency(passes, start), "fpbw_from_sr_hz": sr_hz}}


def gm_sweep(ctx: Context, name: str, start: float, stop: float, step: float):
    dc = ctx.run("tb_gm_dc.spi", name, VID_START=start, VID_STOP=stop, VID_STEP=step).plot("DC")
    return dc.scale.real, (dc["v(outp)"] - dc["v(outn)"]) / COND["r_gm"], -dc["i(vdd)"]


def transconductance(ctx: Context) -> dict:
    """gm at the null, its linear range (the sweep narrows until that range holds
    40 samples), the output current range, and phase reversal across the widest
    differential input with both inputs inside the rails."""
    vid, iout, icc = gm_sweep(ctx, "fd17_0", -0.3, 0.3, 1e-3)
    iopp = float(iout.max() - iout.min())
    i_source, i_sink = float(iout.max()), float(-iout.min())
    window = 0.3
    for attempt in range(1, 5):
        null = crossing(vid, iout, 0.0)
        if null is None:
            null = float(vid[int(np.argmin(np.abs(iout)))])
        gm = smooth_derivative(vid, iout, 3)
        gm0 = float(np.interp(null, vid, gm))
        span = contiguous_range(vid, gm >= COND["gm_fraction"] * gm0, null) if gm0 > 0 else None
        samples = 0 if span is None else int(np.count_nonzero((vid >= span[0]) & (vid <= span[1])))
        if samples >= 40:
            break
        half = 4.0 * max(i_source, i_sink) / max(abs(gm0), 1e-12)
        if span is not None:
            half = min(half, 4.0 * (span[1] - span[0]))
        window = min(max(half, 1e-7), window / 2.0)
        vid, iout, icc = gm_sweep(ctx, f"fd17_{attempt}", null - window, null + window, window / 400.0)
    icc_null = float(np.interp(null, vid, icc))
    lo_v, hi_v = -ctx.point.vdd, ctx.point.vdd
    wide_vid, wide_iout, _ = gm_sweep(ctx, "fd17_vidr", lo_v, hi_v, 0.01)
    far = np.abs(wide_vid - null) > 0.05
    return {"metrics": {
        "gm_s": gm0,
        "gm_over_icc_per_v": gm0 / icc_null if icc_null > 0 else None,
        "vid_lin_v": None if span is None else span[1] - span[0],
        "vid_lin_lo_v": None if span is None else span[0] - null,
        "vid_lin_hi_v": None if span is None else span[1] - null,
        "gm_null_v": null,
        "iopp_a": iopp,
        "i_source_max_a": i_source,
        "i_sink_max_a": i_sink,
        "vidr_lo_v": lo_v,
        "vidr_hi_v": hi_v,
        "phase_reversal": bool(np.any(np.sign(wide_iout[far]) != np.sign(wide_vid[far] - null))),
    }}


def power_cycle_metrics(tran: Plot, t_up: float, t_hold: float, t_down: float) -> dict:
    t = tran.scale.real
    vocm, vod = tran["ocm"], tran["v(outp)"] - tran["v(outn)"]
    dt = COND["oscillation_grid_s"]
    width = max(int(COND["oscillation_window_s"] / dt), 3)
    metrics: dict[str, object] = {}
    worst = 0.0
    for label, (a, b) in {"up": (1e-4, t_up + 1e-3), "hold": (t_up + 1e-3, t_hold),
                          "down": (t_hold, t_down)}.items():
        for v in (vocm, vod):
            vg = uniform(t, v, a, b, max(int((b - a) / dt), 16))
            trend = np.convolve(np.pad(vg, width // 2, mode="edge"), np.ones(width) / width, mode="valid")[: len(vg)]
            busy = float(np.count_nonzero(np.abs(vg - trend) > COND["oscillation_threshold_v"]) * dt)
            worst = max(worst, busy)
            metrics[f"hf_activity_{label}_s"] = max(metrics.get(f"hf_activity_{label}_s", 0.0), busy)
    held = (t > t_hold - 0.2 * COND["power_hold_s"]) & (t < t_hold)
    offset = float(np.mean((vocm - tran["v(vcm)"])[held]))
    error = np.abs(vocm - tran["v(vcm)"] - offset)
    outside = np.nonzero((t >= t_up) & (error > COND["power_settle_band_v"]) & (t < t_hold))[0]
    metrics["power_up_settle_s"] = float(t[outside[-1]] - t_up) if len(outside) else 0.0
    metrics["hold_offset_v"] = offset
    # Scored: the output common mode must reach VCM itself, not just settle somewhere.
    window = (t >= t_up) & (t < t_hold)
    away = np.nonzero(window & (np.abs(vocm - tran["v(vcm)"]) > COND["power_cm_tolerance_v"]))[0]
    metrics["cm_to_vcm_s"] = float(t[away[-1]] - t_up) if len(away) else 0.0
    metrics["oscillation"] = bool(worst > COND["oscillation_max_busy_s"])
    return metrics


def power_cycle(ctx: Context) -> dict:
    """VDD ramps up, holds and ramps down, once for each ramp time."""
    metrics: dict[str, object] = {}
    for k, ramp in enumerate(COND["power_ramps_s"]):
        t_start = 1e-4
        t_up = t_start + ramp
        t_hold = t_up + COND["power_hold_s"]
        t_down = t_hold + ramp
        tran = ctx.run("tb_power_cycle.spi", f"fd25_{k}", T_RAMP=ramp, T_START=t_start, T_UP=t_up,
                       T_HOLD=t_hold, T_DOWN=t_down, T_END=t_down + 5e-4, TSTEP=1e-7,
                       TMAX=COND["power_tmax_s"]).plot("Transient")
        for key, value in power_cycle_metrics(tran, t_up, t_hold, t_down).items():
            metrics[f"{key}_{ramp * 1e3:g}ms"] = value
    metrics["oscillation"] = any(v for key, v in metrics.items() if key.startswith("oscillation_"))
    return {"metrics": metrics}


def supply_step_metrics(t: np.ndarray, v: np.ndarray, freq: float) -> dict:
    """Peak deviation, settling and ringing after each supply edge from the third on."""
    period = 1.0 / freq
    edges = [period / 4.0 + k * period / 2.0 for k in range(2 * COND["supply_step_periods"])]
    band_min = COND["supply_step_band_v"]
    peak = settle_worst = 0.0
    ringing = False
    for e0, e1 in zip(edges[2:], edges[3:] + [t[-1]]):
        mask = (t >= e0) & (t <= e1)
        tt, vv = t[mask], v[mask]
        if len(tt) < 4:
            continue
        end = vv[tt >= e1 - 0.1 * (e1 - e0)]
        final = float(end.mean())
        dev = float(np.max(np.abs(vv - final)))
        peak = max(peak, dev)
        settle = settling_time(tt, vv, e0, final, max(band_min, 0.01 * dev))
        settle_worst = max(settle_worst, settle if settle is not None else (e1 - e0))
        if float(end.max() - end.min()) > max(0.2 * dev, 2.0 * band_min):
            ringing = True
    return {"peak_v": peak, "settle_s": settle_worst, "oscillation": ringing}


def supply_step(ctx: Context) -> dict:
    """A square wave on VDD at each frequency; the output CM and Vod must recover after every edge."""
    ripple = COND["supply_step_v"]
    edge = COND["supply_step_edge_s"]
    metrics: dict[str, object] = {}
    for k, freq in enumerate(COND["supply_step_frequencies"]):
        period = 1.0 / freq
        tmax = min(period / 4000.0, max(first_stable_tau(ctx) / 5.0, period / 20000.0))
        tran = ctx.run("tb_supply_step.spi", f"fd26_{k}", VDD_LO=ctx.point.vdd - ripple,
                       VDD_HI=ctx.point.vdd + ripple, RIPPLE=ripple, FREQ=freq, T_DELAY=period / 4.0,
                       T_EDGE=edge, T_WIDTH=period / 2.0 - edge, T_PERIOD=period, TSTEP=tmax,
                       TSTOP=period / 4.0 + COND["supply_step_periods"] * period, TMAX=tmax).plot("Transient")
        t = tran.scale.real
        for name, v in (("cm", tran["ocm"]), ("dm", tran["v(outp)"] - tran["v(outn)"])):
            for key, value in supply_step_metrics(t, v, freq).items():
                metrics[f"supply_step_{freq_label(freq)}_{name}_{key}"] = value
    return {"metrics": metrics}


# Test ID: (title, function).  FD-01 and FD-07 run first at every point: later
# tests read the phase margin, GBW and slew rate they leave in the cache.
TESTS: dict[str, tuple[str, Callable[[Context], dict]]] = {
    "FD-01": ("Offset, output CM, supply current, power, bias-pin voltage", offset_op),
    "FD-07": ("Open-loop gain, GBW, phase/gain margin, RID, RO", open_loop),
    "FD-04": ("Input common-mode range (gain -6 dB)", input_common_mode_range),
    "FD-05": ("Output swing (DC)", output_swing),
    "FD-08": ("Closed-loop response, 0.1 dB flatness, group delay", closed_loop),
    "FD-09": ("Common-mode rejection (CM->CM, CM->DM)", cm_rejection),
    "FD-10": ("Supply rejection (supply->CM, supply->DM)", supply_rejection),
    "FD-12": ("Noise", noise),
    "FD-14": ("Large-signal step: slew rate and settling", large_step),
    "FD-15": ("Total harmonic distortion", distortion),
    "FD-16": ("Full-power bandwidth", full_power_bandwidth),
    "FD-17": ("Transconductance, linear range, gm/ICC, IOPP, phase reversal", transconductance),
    "FD-25": ("Power-up / power-down stability (5 and 10 ms ramps)", power_cycle),
    "FD-26": ("Supply surge stability", supply_step),
    "FD-30": ("Supply current against the bias current", bias_tracking),
    "FD-31": ("Output common mode with vcm away from VDD/2", vcm_tracking),
}
TRANSIENT_TESTS = {"FD-14", "FD-15", "FD-16", "FD-25", "FD-26"}


def transient_gate(cache: dict) -> str | None:
    """Why the transient tests do not run at a point, or None when they do.

    They run where the open-loop phase margin and gain margin measured at the
    same point are positive.  An amplifier that oscillates in its closed loop
    makes a millisecond transient take tens of minutes and has no step,
    distortion or swing to report, so at such a point those tests count as
    not met without being simulated.  Every design that can meet the phase
    margin limit runs all of them.
    """
    pm, gm = cache.get("pm_deg"), cache.get("gain_margin_db")
    if (pm is not None and pm <= 0.0) or (gm is not None and gm <= 0.0):
        pm_text = "missing" if pm is None else f"{pm:.3g}"
        gm_text = "missing" if gm is None else f"{gm:.3g}"
        return f"open-loop phase margin {pm_text} deg, gain margin {gm_text} dB"
    return None


# ======================================================================
# signoff run
# ======================================================================

FIRST = ["FD-01", "FD-07"]
NOMINAL = Point("tt", COND["vdd"], COND["temperature"])
# The rest of the nominal tests, one job each; the full-power bandwidth search
# starts from the slew rate of the large step, so FD-16 shares its job.
NOMINAL_JOBS = [["FD-04"], ["FD-05"], ["FD-08"], ["FD-09"], ["FD-10"], ["FD-12"], ["FD-14", "FD-16"],
                ["FD-15"], ["FD-17"], ["FD-25"], ["FD-26"], ["FD-30"], ["FD-31"]]
PVT_TESTS = ["FD-01", "FD-07", "FD-04", "FD-05", "FD-09", "FD-10", "FD-12", "FD-14", "FD-15"]
EXTREME_TESTS = ["FD-01", "FD-07", "FD-25", "FD-26"]
LOAD_OVERRIDES = {"+i": {"IOUT_DC": COND["pvt"]["load_current"]}, "-i": {"IOUT_DC": -COND["pvt"]["load_current"]},
                  "rl_to_ground": {"RL_NODE": "0"}}


def run_tests(point: Point, test_ids: list[str], work: str, seed: int | None = None,
              cache: dict | None = None, overrides: dict | None = None, label: str | None = None) -> dict:
    """Run ``test_ids`` in order at one point, skipping the transient tests where
    ``transient_gate`` says the loop is unstable."""
    ctx = Context(point, Path(work), seed, dict(cache or {}), overrides or {})
    results = {}
    for test_id in test_ids:
        started = time.time()
        skipped = transient_gate(ctx.cache) if test_id in TRANSIENT_TESTS else None
        if skipped:
            res = {"skipped": skipped}
        else:
            try:
                res = TESTS[test_id][1](ctx)
            except Exception as exc:  # recorded per test so one failure never hides the rest
                res = {"error": f"{type(exc).__name__}: {exc}"[-3000:]}
        res["elapsed_s"] = round(time.time() - started, 3)
        results[test_id] = res
    shutil.rmtree(work, ignore_errors=True)
    return {"point": point, "label": label, "results": results, "cache": ctx.cache,
            "elapsed_s": round(sum(res["elapsed_s"] for res in results.values()), 3)}


def clean(value):
    """JSON-safe copy: numpy scalars to float, non-finite numbers to strings."""
    if isinstance(value, dict):
        return {k: clean(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [clean(v) for v in value]
    if isinstance(value, (np.floating, np.integer)):
        value = value.item()
    if isinstance(value, float):
        if math.isnan(value):
            return "nan"
        if math.isinf(value):
            return "inf" if value > 0 else "-inf"
        return float(f"{value:.7g}")
    return value


def is_number(value) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def summarize(rows: list[dict]) -> dict:
    """Per test and metric: the count, the extremes with where they occur, and
    where a flag is true or a value is missing."""
    table: dict[str, dict[str, list]] = {}
    for row in rows:
        tag = row["label"] or row["point"].tag
        for test_id, res in row["results"].items():
            for name, value in res.get("metrics", {}).items():
                table.setdefault(test_id, {}).setdefault(name, []).append((tag, value))
    summary: dict[str, dict] = {}
    for test_id, metrics in table.items():
        for name, values in metrics.items():
            numeric = [(tag, v) for tag, v in values if is_number(v)]
            flags = [(tag, v) for tag, v in values if isinstance(v, bool)]
            entry: dict[str, object] = {"count": len(values)}
            if numeric:
                lo = min(numeric, key=lambda item: item[1])
                hi = max(numeric, key=lambda item: item[1])
                entry.update({"min": lo[1], "min_at": lo[0], "max": hi[1], "max_at": hi[0]})
            if flags:
                entry["true_at"] = [tag for tag, v in flags if v]
            missing = [tag for tag, v in values if v is None]
            if missing:
                entry["missing_at"] = missing
            summary.setdefault(test_id, {})[name] = entry
    return summary


def errors_of(rows: list[dict]) -> dict:
    by_where = {row["label"] or row["point"].tag: {tid: res["error"] for tid, res in row["results"].items()
                                                   if "error" in res} for row in rows}
    return {where: errors for where, errors in by_where.items() if errors}


def operating_range(rows: list[dict], key: str, nominal: float) -> dict:
    """The contiguous span of sweep values around ``nominal`` where the open loop
    meets the operating-range limits."""
    def ok(row: dict) -> bool:
        m = row["metrics"]
        if not all(isinstance(m.get(k), (int, float)) for k in ("avo_db", "gbw_hz", "pm_deg")):
            return False
        return (m["avo_db"] >= COND["operating_avo_min_db"]
                and m["gbw_hz"] >= COND["operating_gbw_min_hz"]
                and m["pm_deg"] >= COND["operating_pm_min_deg"])

    rows = sorted(rows, key=lambda row: row[key])
    flags = [ok(row) for row in rows]
    values = [row[key] for row in rows]
    ref = min(range(len(values)), key=lambda i: abs(values[i] - nominal))
    if not flags[ref]:
        return {"low": None, "high": None, "rows": rows}
    lo = hi = ref
    while lo > 0 and flags[lo - 1]:
        lo -= 1
    while hi < len(rows) - 1 and flags[hi + 1]:
        hi += 1
    return {
        "low": values[lo], "high": values[hi],
        "low_is_sweep_limit": lo == 0, "high_is_sweep_limit": hi == len(rows) - 1,
        "rows": rows,
    }


def signoff(work: Path, jobs: int) -> dict:
    """Run every test of the signoff on the design; returns the metrics report."""
    started = time.time()
    pvt = COND["pvt"]
    mc = COND["mc"]
    points = [Point(c, v, t) for c in pvt["corners"] for v in pvt["supplies"] for t in pvt["temperatures"]]
    head = run_tests(NOMINAL, FIRST, str(work / "nominal"))

    with ProcessPoolExecutor(max_workers=jobs) as pool:
        def submit(point, test_ids, tag, **kwargs):
            return pool.submit(run_tests, point, test_ids, str(work / tag), **kwargs)

        # Long nominal chains first so they overlap the many short PVT jobs.
        nominal_futures = [submit(NOMINAL, ids, f"nominal/{ids[0]}", cache=head["cache"]) for ids in NOMINAL_JOBS]
        pvt_futures = [submit(point, PVT_TESTS, f"pvt/{point.tag}") for point in points]
        mc_point = Point(mc["corner"], COND["vdd"], COND["temperature"])
        mc_futures = [submit(mc_point, ["FD-01"], f"mc/{k:03d}", seed=mc["seed"] + k) for k in range(mc["runs"])]
        extreme_points = [Point("tt", COND["vdd"], temp) for temp in pvt["temperatures"]]
        extreme_futures = [submit(point, EXTREME_TESTS, f"temp_ext/{point.tag}") for point in extreme_points]
        load_futures = []
        for point in points:
            for case in pvt["worst_case_loads"]:
                tag = f"{point.tag}_{case.replace('+', 'p').replace('-', 'm')}"
                load_futures.append(submit(point, ["FD-07"], f"load/{tag}", overrides=LOAD_OVERRIDES[case],
                                           label=f"{point.tag} {case}"))
        range_futures = [("vdd", vdd, submit(Point("tt", vdd, COND["temperature"]), ["FD-07"], f"vdd/{vdd:.2f}"))
                         for vdd in float_range(*COND["supply_sweep"])]
        range_futures += [("temp", temp, submit(Point("tt", COND["vdd"], temp), ["FD-07"], f"temp/{temp:g}"))
                          for temp in float_range(*COND["temp_range_sweep"])]

        nominal = dict(head["results"])
        for future in nominal_futures:
            nominal.update(future.result()["results"])
        pvt_rows = [future.result() for future in pvt_futures]
        mc_rows = [future.result() for future in mc_futures]
        extreme_rows = [future.result() for future in extreme_futures]
        load_rows = [future.result() for future in load_futures]
        range_rows = [(kind, value, future.result()) for kind, value, future in range_futures]

    nominal = {test_id: nominal[test_id] for test_id in TESTS}
    offsets = np.array([row["results"]["FD-01"]["metrics"]["vos_in_v"] for row in mc_rows
                        if "metrics" in row["results"]["FD-01"]])
    nominal_ac = nominal["FD-07"].get("metrics", {})
    ranges = {}
    if all(isinstance(nominal_ac.get(k), (int, float)) for k in ("avo_db", "gbw_hz")):
        for kind, nominal_value in (("vdd", COND["vdd"]), ("temp", COND["temperature"])):
            rows = [{kind: value, "metrics": res["results"]["FD-07"].get("metrics", {})}
                    for k, value, res in range_rows if k == kind]
            ranges[kind] = operating_range(rows, kind, nominal_value)
    # Runs where the transient tests were skipped, with the reason.
    gate = {}
    for where, results in ([("nominal", nominal)]
                           + [(f"pvt/{row['point'].tag}", row["results"]) for row in pvt_rows]
                           + [(f"temperature_extremes/{row['point'].tag}", row["results"]) for row in extreme_rows]):
        reasons = {res["skipped"] for res in results.values() if "skipped" in res}
        if reasons:
            gate[where] = sorted(reasons)[0]

    return {
        "task_id": TASK_ID,
        "subckt": SUBCKT,
        "conditions": COND,
        "tests": {test_id: title for test_id, (title, _) in TESTS.items()},
        "planned": {"nominal_tests": list(TESTS), "pvt_tests": PVT_TESTS, "pvt_points": len(points),
                    "load_points": len(load_rows)},
        "nominal_point": NOMINAL.tag,
        "nominal": nominal,
        "pvt": {
            "points": [row["point"].tag for row in pvt_rows],
            "summary": summarize(pvt_rows),
            "errors": errors_of(pvt_rows),
            "values": {row["point"].tag: {tid: res.get("metrics", {}) for tid, res in row["results"].items()}
                       for row in pvt_rows},
        },
        "monte_carlo": {
            "corner": mc["corner"], "runs": int(len(offsets)), "requested_runs": mc["runs"],
            "seed_first": mc["seed"], "metric": "vos_in_v",
            "mean": float(offsets.mean()) if len(offsets) else None,
            "sigma": float(offsets.std(ddof=1)) if len(offsets) > 1 else None,
            "abs_max": float(np.abs(offsets).max()) if len(offsets) else None,
        },
        "temperature_extremes": {
            row["point"].tag: {tid: {"skipped": res["skipped"]} if "skipped" in res
                               else res.get("metrics", res.get("error"))
                               for tid, res in row["results"].items()}
            for row in extreme_rows},
        "worst_case_load": {
            "load_current_a": pvt["load_current"],
            "loads": pvt["worst_case_loads"],
            "points": len(load_rows),
            "summary": summarize(load_rows),
            "errors": errors_of(load_rows),
        },
        "operating_range": ranges,
        "transient_gate": gate,
        # Simulation cost per phase: the sum of job times, in CPU-seconds.
        "cost_s": {
            "nominal": round(sum(res.get("elapsed_s", 0.0) for res in nominal.values()), 1),
            "pvt": round(sum(row["elapsed_s"] for row in pvt_rows), 1),
            "load_cases": round(sum(row["elapsed_s"] for row in load_rows), 1),
            "temperature_extremes": round(sum(row["elapsed_s"] for row in extreme_rows), 1),
            "offset_mc": round(sum(row["elapsed_s"] for row in mc_rows), 1),
            "ranges": round(sum(res["elapsed_s"] for _, _, res in range_rows), 1),
        },
        "elapsed_s": round(time.time() - started, 1),
    }


# ======================================================================
# scoring
# ======================================================================

def number(value) -> float | None:
    return float(value) if is_number(value) else None


def fmt(value: float | None, scale: float = 1.0, digits: int = 4) -> str:
    return "missing" if value is None else f"{value * scale:.{digits}g}"


def vdd_of(tag: str) -> float:
    return float(tag.split("_")[1].rstrip("v").replace("p", "."))


class View:
    """Read-only accessors over the metrics report."""

    def __init__(self, result: dict):
        self.r = result
        self.points = result["pvt"]["points"]

    def nominal_raw(self, test: str, key: str):
        return self.r["nominal"][test].get("metrics", {}).get(key)

    def nominal(self, test: str, key: str) -> float | None:
        return number(self.nominal_raw(test, key))

    def pvt(self, test: str, key: str) -> list[float | None]:
        values = self.r["pvt"]["values"]
        return [number(values[tag].get(test, {}).get(key)) for tag in self.points]

    def everywhere(self, test: str, key: str) -> list[float | None]:
        """Values at the PVT points and the nominal point."""
        return self.pvt(test, key) + [self.nominal(test, key)]

    def worst(self, test: str, key: str, lowest: bool, with_load: bool = False) -> float | None:
        """Worst value over the PVT points and the nominal point (and the load points)."""
        values = self.everywhere(test, key)
        if any(v is None for v in values):
            return None
        pick = min if lowest else max
        worst = pick(values)
        if with_load:
            entry = self.r["worst_case_load"]["summary"].get(test, {}).get(key, {})
            extreme = number(entry.get("min" if lowest else "max"))
            if (extreme is None or entry.get("missing_at")
                    or entry.get("count", 0) != self.r["planned"]["load_points"]):
                return None
            worst = pick(worst, extreme)
        return worst


def completeness(result: dict) -> tuple[bool, str]:
    planned = result["planned"]
    problems = []
    errors = [tid for tid in planned["nominal_tests"] if "error" in result["nominal"][tid]]
    if errors:
        problems.append(f"nominal errors in {','.join(errors)}")
    pvt = result["pvt"]
    if pvt["errors"]:
        problems.append(f"PVT {len(pvt['points'])}/{planned['pvt_points']} points, {len(pvt['errors'])} with errors")
    load = result["worst_case_load"]
    if load["errors"]:
        problems.append(f"load cases {load['points']}/{planned['load_points']}, {len(load['errors'])} with errors")
    mc = result["monte_carlo"]
    if mc["runs"] != mc["requested_runs"]:
        problems.append(f"offset MC {mc['runs']}/{mc['requested_runs']} runs")
    extremes = result["temperature_extremes"]
    bad = [tag for tag, tests in extremes.items() if any(not isinstance(res, dict) for res in tests.values())]
    if bad:
        problems.append(f"temperature extremes {len(extremes)}/2, errors at {','.join(bad)}")
    if set(result["operating_range"]) != {"vdd", "temp"}:
        problems.append("operating-range sweeps missing")
    counted = len(planned["nominal_tests"])
    detail = (f"nominal {counted - len(errors)}/{counted} tests, PVT {len(pvt['points'])}, "
              f"load {load['points']}, offset MC {mc['runs']}, temperature extremes {len(extremes)}")
    return not problems, detail + ("; " + "; ".join(problems) if problems else "")


def oscillation_flags(result: dict) -> list[str]:
    """Oscillation in the power-cycle and supply-surge tests at the nominal point
    and the temperature extremes, and output phase reversal at the nominal point."""
    flags = []
    runs = [("nominal", result["nominal"])]
    runs += [(tag, {tid: res if "skipped" in res else {"metrics": res}
                    for tid, res in tests.items() if isinstance(res, dict)})
             for tag, tests in result["temperature_extremes"].items()]
    for where, tests in runs:
        for test_id in ("FD-25", "FD-26"):
            entry = tests.get(test_id, {})
            metrics = entry.get("metrics")
            if "skipped" in entry:
                flags.append(f"{test_id} not run at {where} ({entry['skipped']})")
                continue
            if metrics is None:
                flags.append(f"{test_id} missing at {where}")
                continue
            hits = [k for k, x in metrics.items() if k.endswith("oscillation") and x is not False]
            if hits:
                flags.append(f"{test_id} {','.join(hits)} at {where}")
    reversal = result["nominal"]["FD-17"].get("metrics", {}).get("phase_reversal")
    if reversal is not False:
        flags.append(f"phase reversal {reversal}")
    return flags


def power_up_cm(result: dict) -> tuple[float | None, list[str]]:
    """Worst time after the end of each supply ramp until the output common mode stays within
    the tolerance of VCM, over the power-cycle runs at the nominal point and the temperature extremes."""
    runs = [("nominal", result["nominal"].get("FD-25", {}).get("metrics"))]
    runs += [(tag, tests.get("FD-25")) for tag, tests in result["temperature_extremes"].items()]
    worst, missing = 0.0, []
    for where, metrics in runs:
        if not isinstance(metrics, dict) or "skipped" in metrics:
            missing.append(where)
            continue
        values = [x for k, x in metrics.items() if k.startswith("cm_to_vcm_s_")]
        if len(values) != len(COND["power_ramps_s"]):
            missing.append(where)
            continue
        worst = max(worst, *values)
    return (None if missing else worst), missing


def score(result: dict) -> tuple[list[tuple[str, bool, str]], dict]:
    """Checks (name, passed, message) and the scored measurements."""
    v = View(result)
    s = SPEC
    checks: list[tuple[str, bool, str]] = []
    meas: dict[str, float | None] = {}

    def check(name: str, ok: bool, message: str) -> None:
        checks.append((name, bool(ok), message))

    def le(value, limit):
        return value is not None and value <= limit

    def ge(value, limit):
        return value is not None and value >= limit

    gate = result["transient_gate"]
    gate_note = (f"; transient tests not run at {len(gate)} of the runs because the open-loop phase or "
                 f"gain margin there is not positive ({', '.join(sorted(gate)[:4])}"
                 f"{', ...' if len(gate) > 4 else ''})" if gate else "")

    ok, message = completeness(result)
    check("complete_signoff", ok, message)

    avo = meas["avo_min_db"] = v.worst("FD-07", "avo_db", True, with_load=True)
    check("pvt_open_loop_gain", ge(avo, s["avo_min_db"]),
          f"worst AVO {fmt(avo)} dB over PVT + load cases (limit {s['avo_min_db']:g} dB)")

    gbw = meas["gbw_min_hz"] = v.worst("FD-07", "gbw_hz", True, with_load=True)
    check("pvt_gain_bandwidth", ge(gbw, s["gbw_min_hz"]),
          f"worst GBW {fmt(gbw, 1e-6)} MHz (limit {s['gbw_min_hz'] / 1e6:g} MHz)")

    pm = meas["pm_min_deg"] = v.worst("FD-07", "pm_deg", True, with_load=True)
    gm = meas["gain_margin_min_db"] = v.worst("FD-07", "gain_margin_db", True, with_load=True)
    crossings = v.worst("FD-07", "unity_crossings", False, with_load=True)
    check("pvt_loop_stability", ge(pm, s["pm_min_deg"]) and ge(gm, s["gm_min_db"]) and crossings == 1,
          f"worst PM {fmt(pm)} deg, worst gain margin {fmt(gm)} dB, unity crossings max {fmt(crossings)} "
          f"(limits {s['pm_min_deg']:g} deg, {s['gm_min_db']:g} dB, 1)")

    peaking = meas["closed_loop_peaking_db"] = v.nominal("FD-08", "cl_peaking_db")
    check("closed_loop_flatness", le(peaking, s["peaking_max_db"]),
          f"closed-loop peaking {fmt(peaking)} dB (limit {s['peaking_max_db']:g} dB)")

    rise, fall = v.worst("FD-14", "sr_rise_v_per_s", True), v.worst("FD-14", "sr_fall_v_per_s", True)
    sr = meas["slew_rate_min_v_per_s"] = None if rise is None or fall is None else min(rise, fall)
    check("pvt_slew_rate", ge(sr, s["sr_min_v_per_s"]),
          f"worst SR {fmt(sr, 1e-6)} V/us (limit {s['sr_min_v_per_s'] / 1e6:g} V/us)" + gate_note)

    t_rise = v.worst("FD-14", "large_settle_rise_0p1pct_s", False)
    t_fall = v.worst("FD-14", "large_settle_fall_0p1pct_s", False)
    ts = meas["settling_max_s"] = None if t_rise is None or t_fall is None else max(t_rise, t_fall)
    check("pvt_settling", le(ts, s["settle_max_s"]),
          f"worst 0.1 % large-step settling {fmt(ts, 1e9)} ns (limit {s['settle_max_s'] * 1e9:g} ns)"
          + gate_note)

    lo, hi = v.nominal("FD-04", "icmr_lo_v"), v.nominal("FD-04", "icmr_hi_v")
    want_lo, want_hi = s["icmr_nominal_v"]
    half = s["icmr_pvt_half_window_v"]
    # Every PVT point must cover its own VCM (VDD/2) +/- half.
    margins = []
    for tag, plo, phi in zip(v.points, v.pvt("FD-04", "icmr_lo_v"), v.pvt("FD-04", "icmr_hi_v")):
        vcm_pt = COND["vcm_fraction"] * vdd_of(tag)
        margins.append(None if plo is None or phi is None else min(vcm_pt - half - plo, phi - vcm_pt - half))
    pvt_margin = None if None in margins else min(margins)
    meas.update(icmr_nominal_lo_v=lo, icmr_nominal_hi_v=hi, icmr_pvt_margin_v=pvt_margin)
    check("input_common_mode_range",
          le(lo, want_lo) and ge(hi, want_hi) and ge(pvt_margin, 0.0),
          f"nominal {fmt(lo)}..{fmt(hi)} V (must cover {want_lo:g}..{want_hi:g} V); every PVT point covers "
          f"VCM +/- {half * 1e3:g} mV with {fmt(pvt_margin, 1e3)} mV to spare at worst")

    swing = meas["swing_min_v"] = v.worst("FD-05", "vodpp_v", True)
    check("pvt_output_swing", ge(swing, s["swing_min_v"]),
          f"worst VODpp {fmt(swing)} V (limit {s['swing_min_v']:g} V)")

    cm_gain = meas["cm_gain_max_db"] = v.worst("FD-09", "acm_cm_db", False)
    check("pvt_cm_rejection", le(cm_gain, s["cm_gain_max_db"]),
          f"worst input-CM to output-CM gain {fmt(cm_gain)} dB (limit {s['cm_gain_max_db']:g} dB)")

    plus, minus = v.worst("FD-10", "psrr_plus_cm_db", True), v.worst("FD-10", "psrr_minus_cm_db", True)
    psrr = meas["psrr_cm_min_db"] = None if plus is None or minus is None else min(plus, minus)
    check("pvt_cm_supply_rejection", ge(psrr, s["psrr_cm_min_db"]),
          f"worst VDD->CM {fmt(plus)} dB, VSS->CM {fmt(minus)} dB (limit {s['psrr_cm_min_db']:g} dB)")

    cm_errors = v.everywhere("FD-01", "vocm_error_v")
    cm_err = meas["cm_error_max_v"] = None if None in cm_errors else max(abs(x) for x in cm_errors)
    check("pvt_output_common_mode", le(cm_err, s["cm_error_max_v"]),
          f"worst |Vocm - VCM| {fmt(cm_err, 1e3)} mV (limit {s['cm_error_max_v'] * 1e3:g} mV)")

    levels = COND["vcm_tracking_levels_v"]
    tracks = [v.nominal("FD-31", f"vocm_error_at_{lv:g}".replace(".", "p") + "v_v") for lv in levels]
    track_err = meas["vcm_tracking_error_max_v"] = None if None in tracks else max(abs(x) for x in tracks)
    check("vcm_tracking", le(track_err, s["cm_error_max_v"]),
          f"with vcm at {', '.join(f'{lv:g}' for lv in levels)} V (VDD 1.8 V) worst |Vocm - VCM| "
          f"{fmt(track_err, 1e3)} mV (limit {s['cm_error_max_v'] * 1e3:g} mV)")

    en = meas["noise_white_max_v_rthz"] = v.worst("FD-12", "en_white_v_rthz", False)
    check("pvt_noise", le(en, s["en_max_v_rthz"]),
          f"worst white input noise {fmt(en, 1e9)} nV/rtHz (limit {s['en_max_v_rthz'] * 1e9:g} nV/rtHz)")

    thd_db = meas["thd_max_db"] = v.worst("FD-15", "thd_10khz_db", False)
    check("pvt_distortion", le(thd_db, s["thd_max_db"]),
          f"worst THD at 10 kHz {fmt(thd_db)} dB (limit {s['thd_max_db']:g} dB)" + gate_note)

    fpbw = meas["fpbw_hz"] = v.nominal("FD-16", "fpbw_hz")
    check("full_power_bandwidth", ge(fpbw, s["fpbw_min_hz"]),
          f"full-power bandwidth {fmt(fpbw, 1e-6)} MHz (limit {s['fpbw_min_hz'] / 1e6:g} MHz)" + gate_note)

    sigma = meas["offset_sigma_v"] = number(result["monte_carlo"]["sigma"])
    offsets = v.everywhere("FD-01", "vos_in_v")
    vos = meas["vos_max_v"] = None if None in offsets else max(abs(x) for x in offsets)
    check("offset", le(sigma, s["offset_sigma_max_v"]) and le(vos, s["vos_max_v"]),
          f"mismatch sigma {fmt(sigma, 1e3)} mV (limit {s['offset_sigma_max_v'] * 1e3:g} mV), "
          f"worst systematic |VOS| {fmt(vos, 1e3)} mV (limit {s['vos_max_v'] * 1e3:g} mV)")

    icc = meas["icc_max_a"] = v.worst("FD-01", "icc_a", False)
    check("pvt_supply_current", le(icc, s["icc_max_a"]),
          f"worst supply current {fmt(icc, 1e6)} uA (limit {s['icc_max_a'] * 1e6:g} uA)")

    vbi = v.everywhere("FD-01", "vbi_v")
    vdds = [vdd_of(tag) for tag in v.points] + [COND["vdd"]]
    if None not in vbi:
        excess = max(max(-x, x - vd) for x, vd in zip(vbi, vdds))
        lowest, highest = min(vbi), max(x - vd for x, vd in zip(vbi, vdds))
    else:
        excess = lowest = highest = None
    meas["ibias_pin_excess_v"] = excess
    check("pvt_bias_pin_compliance", excess is not None and excess <= 0.0,
          f"lowest V(ibias) {fmt(lowest)} V (must be >= 0 V), highest V(ibias) - V(vdd) {fmt(highest)} V "
          f"(must be <= 0 V) over every PVT point"
          if excess is not None else "V(ibias) missing at some operating point")

    ivcm = meas["vcm_pin_current_max_a"] = v.worst("FD-01", "vcm_pin_current_a", False)
    check("pvt_vcm_pin_current", le(ivcm, s["vcm_current_max_a"]),
          f"worst current drawn from the vcm reference {fmt(ivcm, 1e9)} nA "
          f"(limit {s['vcm_current_max_a'] * 1e9:g} nA)")

    tracking = meas["icc_tracking"] = v.nominal("FD-30", "icc_tracking")
    check("bias_current_tracking", ge(tracking, s["icc_tracking_min"]),
          f"supply current tracks the bias current with exponent {fmt(tracking)} (limit {s['icc_tracking_min']:g})")

    ranges = result["operating_range"]
    vdd_lo, vdd_hi = (number(ranges.get("vdd", {}).get(k)) for k in ("low", "high"))
    t_lo, t_hi = (number(ranges.get("temp", {}).get(k)) for k in ("low", "high"))
    meas.update(supply_range_low_v=vdd_lo, supply_range_high_v=vdd_hi, temp_range_low_c=t_lo, temp_range_high_c=t_hi)
    want_v, want_t = s["supply_range_v"], s["temp_range_c"]
    check("operating_range",
          le(vdd_lo, want_v[0]) and ge(vdd_hi, want_v[1]) and le(t_lo, want_t[0]) and ge(t_hi, want_t[1]),
          f"supply {fmt(vdd_lo)}..{fmt(vdd_hi)} V (must cover {want_v[0]:g}..{want_v[1]:g} V), "
          f"temperature {fmt(t_lo)}..{fmt(t_hi)} C (must cover {want_t[0]:g}..{want_t[1]:g} C)")

    cm_up, cm_missing = power_up_cm(result)
    meas["power_up_cm_settle_s"] = cm_up
    check("power_up_common_mode", le(cm_up, s["power_cm_settle_max_s"]),
          f"after each supply ramp the output common mode is within {COND['power_cm_tolerance_v'] * 1e3:g} mV of VCM "
          f"after {fmt(cm_up, 1e6)} us at worst (limit {s['power_cm_settle_max_s'] * 1e6:g} us, nominal and -40/125 C)"
          if cm_up is not None else f"power-cycle runs missing or skipped at {', '.join(cm_missing)}")

    flags = oscillation_flags(result)
    check("transient_stability", not flags,
          "power-up/down and supply surge free of oscillation at -40/27/125 C, no phase reversal"
          if not flags else "; ".join(flags))
    return checks, meas


def available_cpus() -> int:
    """CPUs this container may use: the affinity mask capped by the cgroup quota."""
    count = len(os.sched_getaffinity(0)) if hasattr(os, "sched_getaffinity") else (os.cpu_count() or 1)
    try:  # cgroup v2
        quota, period = Path("/sys/fs/cgroup/cpu.max").read_text().split()[:2]
        if quota != "max":
            count = min(count, max(1, int(quota) // int(period)))
    except (OSError, ValueError):
        pass
    try:  # cgroup v1
        quota = int(Path("/sys/fs/cgroup/cpu/cpu.cfs_quota_us").read_text())
        period = int(Path("/sys/fs/cgroup/cpu/cpu.cfs_period_us").read_text())
        if quota > 0 and period > 0:
            count = min(count, max(1, quota // period))
    except (OSError, ValueError):
        pass
    return max(1, int(os.environ.get("VERIFIER_JOBS", count)))


def main() -> int:
    started = time.time()
    jobs = available_cpus()
    work = Path(tempfile.mkdtemp(prefix="opamp-signoff-"))
    result = signoff(work, jobs)
    shutil.rmtree(work, ignore_errors=True)
    checks, measurements = score(result)
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    (REPORT_DIR / "metrics.json").write_text(json.dumps(clean(result), indent=1) + "\n")
    (REPORT_DIR / "summary.json").write_text(json.dumps(clean({
        "task_id": TASK_ID,
        "tests_total": len(checks),
        "tests_passed": sum(ok for _, ok, _ in checks),
        "checks": [{"name": n, "passed": ok, "message": m} for n, ok, m in checks],
        "measurements": measurements,
        "jobs": jobs,
        "elapsed_s": round(time.time() - started, 1),
    }), indent=2) + "\n")
    write_results(checks)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
