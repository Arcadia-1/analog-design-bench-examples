#!/usr/bin/env python3
"""Fully differential op amp with common-mode feedback: signoff of /app/circuit.spi.

Runs the op-amp benches in benches/ at the nominal point, the PVT and load
points, the mismatch Monte Carlo runs and the supply and temperature sweeps
listed in the specification, and scores the design against its limits.
"""

from __future__ import annotations

import json
import math
import numpy as np
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

M = S = sys.modules[__name__]  # the inlined modules refer to each other as M. and S.


# ======================================================================
# ngspice decks and raw files
# ======================================================================

"""Render bench templates, run ngspice, and read its ASCII raw files."""





TOKEN = re.compile(r"\{\{([A-Z0-9_]+)\}\}")


class SpiceError(RuntimeError):
    """Raised when ngspice fails or a deck leaves a token unresolved."""


def spice_value(value: object) -> str:
    if isinstance(value, bool):
        return "1" if value else "0"
    if isinstance(value, float):
        return f"{value:.10g}"
    return str(value)


def render(template: str, tokens: dict[str, object]) -> str:
    def replace(match: re.Match[str]) -> str:
        key = match.group(1)
        if key not in tokens:
            raise SpiceError(f"template token {{{{{key}}}}} has no value")
        return spice_value(tokens[key])

    return TOKEN.sub(replace, template)


@dataclass
class Plot:
    name: str
    vectors: dict[str, np.ndarray]

    def __getitem__(self, key: str) -> np.ndarray:
        return self.vectors[key.lower()]

    def __contains__(self, key: str) -> bool:
        return key.lower() in self.vectors

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
        vectors = {}
        for column, name in enumerate(names):
            vector = data[:, column]
            # Scales and real-valued vectors stay real even in complex plots.
            if is_complex and column == 0:
                vector = vector.real
            vectors[name] = vector
        plots.append(Plot(header.get("plotname", ""), vectors))
    return plots


@dataclass
class Run:
    deck: Path
    log: str
    plots: list[Plot]

    def plot(self, prefix: str, occurrence: int = 0) -> Plot:
        matches = [plot for plot in self.plots if plot.name.lower().startswith(prefix.lower())]
        if len(matches) <= occurrence:
            raise SpiceError(f"{self.deck.name}: no '{prefix}' plot #{occurrence}\n{tail(self.log)}")
        return matches[occurrence]

    def all(self, prefix: str) -> list[Plot]:
        return [plot for plot in self.plots if plot.name.lower().startswith(prefix.lower())]


def tail(text: str, lines: int = 25) -> str:
    return "\n".join(text.splitlines()[-lines:])


def run_deck(text: str, work: Path, name: str) -> Run:
    """Write ``text`` to ``work/name.spi``, run ngspice, and read ``name.raw``.

    Every bench writes its vectors with ``write {{RAW}}`` from a ``.control``
    block, so the raw file holds exactly the plots the bench asked for.
    """
    work.mkdir(parents=True, exist_ok=True)
    deck = work / f"{name}.spi"
    raw = work / f"{name}.raw"
    if raw.exists():
        raw.unlink()
    deck.write_text(text)
    env = dict(os.environ, OMP_NUM_THREADS="1")
    result = subprocess.run(
        ["ngspice", "-b", deck.name],
        cwd=work,
        env=env,
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

"""Waveform and frequency-response measurements shared by every bench."""





trapezoid = getattr(np, "trapezoid", None) or np.trapz


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


def value_at(x: np.ndarray, y: np.ndarray, x0: float, *, log_x: bool = True) -> float:
    if log_x:
        return float(np.interp(math.log10(x0), np.log10(x), y))
    return float(np.interp(x0, x, y))


NO_PHASE_CROSSING_GM_DB = 999.0  # reported gain margin when the phase never reaches -180 deg


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
        # The phase never reaches -180 deg in the analysed band: infinite gain margin.
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
        "dc_gain_db": ref,
        "f_3db_hz": f3,
        "f_0p1db_hz": flat,
        "peaking_db": float(max(mag.max() - ref, 0.0)),
        "group_delay_lf_s": float(group_delay[1]),
        "group_delay_max_s": float(group_delay[passband].max()),
    }


def uniform(t: np.ndarray, v: np.ndarray, t0: float, t1: float, points: int) -> tuple[np.ndarray, np.ndarray]:
    grid = np.linspace(t0, t1, points)
    return grid, np.interp(grid, t, v)


def settling_time(t: np.ndarray, v: np.ndarray, t_edge: float, final: float, band: float) -> float | None:
    """Time from ``t_edge`` after which ``v`` stays inside ``final +/- band``.

    Returns ``None`` when the record ends outside the band."""
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
    """Slew, rise time, overshoot and settling of one edge between ``t_edge`` and ``t_end``."""
    mask = (t >= t_edge) & (t <= t_end)
    tt, vv = t[mask], v[mask]
    tail = vv[tt >= t_edge + 0.95 * (t_end - t_edge)]
    final = float(tail.mean())
    step = final - v_start
    sign = 1.0 if step >= 0 else -1.0
    norm = (vv - v_start) * sign
    amp = abs(step)
    t10 = crossing(tt, norm, 0.1 * amp, rising=True)
    t90 = crossing(tt, norm, 0.9 * amp, rising=True)
    result: dict[str, float | None] = {
        "final_v": final,
        "static_error_fraction": abs(final - v_command) / max(abs(v_command - v_start), 1e-30),
        "rise_10_90_s": None if t10 is None or t90 is None else t90 - t10,
        "slew_10_90_v_per_s": None,
        "slew_max_v_per_s": None,
        "overshoot_pct": float(max(norm.max() - amp, 0.0) / amp * 100.0) if amp > 0 else None,
        # Settling is judged against the commanded level, never the output's own tail.
        "settle_1pct_s": settling_time(tt, vv, t_edge, v_command, 0.01 * abs(v_command - v_start)),
        "settle_0p1pct_s": settling_time(tt, vv, t_edge, v_command, 0.001 * abs(v_command - v_start)),
    }
    if t10 is not None and t90 is not None and t90 > t10:
        result["slew_10_90_v_per_s"] = 0.8 * amp / (t90 - t10)
        # Maximum slope inside the 10-90 % window on a uniform grid; a short
        # moving average keeps single-step numerical noise out of the maximum.
        grid, vg = uniform(tt, norm, t10, t90, 2001)
        slope = np.gradient(vg, grid)
        width = 9
        slope = np.convolve(slope, np.ones(width) / width, mode="valid")
        result["slew_max_v_per_s"] = float(slope.max())
    return result


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


def harmonics(t: np.ndarray, v: np.ndarray, f0: float, t_start: float, cycles: int,
              count: int = 10, points: int = 4096) -> np.ndarray:
    """Amplitudes of harmonics 1..count over ``cycles`` whole periods from ``t_start``."""
    period = cycles / f0
    grid = t_start + np.arange(points) * (period / points)
    samples = hermite(grid, t, v)
    spectrum = np.abs(np.fft.rfft(samples - samples.mean())) * 2.0 / points
    bins = [cycles * k for k in range(1, count + 1) if cycles * k < len(spectrum)]
    return np.array([spectrum[b] for b in bins])


def thd(t: np.ndarray, v: np.ndarray, f0: float, t_start: float, cycles: int,
        count: int = 10, points: int = 4096) -> dict[str, float]:
    amps = harmonics(t, v, f0, t_start, cycles, count, points)
    distortion = float(np.sqrt(np.sum(amps[1:] ** 2)))
    ratio = distortion / amps[0] if amps[0] > 0 else float("inf")
    return {
        "fundamental_v": float(amps[0]),
        "thd_db": float(20.0 * np.log10(max(ratio, 1e-12))),
        "thd_pct": float(100.0 * ratio),
        "hd2_db": float(db(amps[1] / amps[0])) if len(amps) > 1 else float("nan"),
        "hd3_db": float(db(amps[2] / amps[0])) if len(amps) > 2 else float("nan"),
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


def smooth_derivative(x: np.ndarray, y: np.ndarray, width: int = 5) -> np.ndarray:
    slope = np.gradient(y, x)
    if width > 1:
        kernel = np.ones(width) / width
        padded = np.pad(slope, width // 2, mode="edge")
        slope = np.convolve(padded, kernel, mode="valid")
    return slope


def high_pass_ripple(t: np.ndarray, v: np.ndarray, t0: float, t1: float, dt: float,
                     window: float) -> float:
    """Peak-to-peak of ``v`` minus its moving average (``window`` long) on [t0, t1]."""
    points = max(int((t1 - t0) / dt), 16)
    grid, vg = uniform(t, v, t0, t1, points)
    width = max(int(window / dt), 3)
    kernel = np.ones(width) / width
    trend = np.convolve(np.pad(vg, width // 2, mode="edge"), kernel, mode="valid")[: len(vg)]
    residual = (vg - trend)[width:-width] if len(vg) > 2 * width else vg - trend
    return float(residual.max() - residual.min()) if len(residual) else 0.0


# ======================================================================
# benches
# ======================================================================

"""The mandatory test list of the v4 OTA bench and the measurement of every test.

Each test renders one bench template from ``testbench/<class>/``, runs it with
ngspice, and returns ``{"metrics": {...}, "curves": {...}}``.  Metric names
carry SI units in their suffix (``_v``, ``_a``, ``_hz``, ``_s``, ``_db`` ...).
"""






BENCH_ROOT = Path(__file__).resolve().parents[1]
# Bench templates live in <TEMPLATE_ROOT>/<class>/; the Harbor verifier points
# this at its own copy of the decks.
TEMPLATE_ROOT = BENCH_ROOT / "testbench"


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
    klass: str
    dut_file: str
    dut: str
    cond: dict
    point: Point
    work: Path
    detail: bool = True
    seed: int | None = None
    cache: dict = field(default_factory=dict)
    overrides: dict = field(default_factory=dict)
    nominal: bool = False   # the nominal point of the run (some checks run only there)

    @property
    def vcm(self) -> float:
        return self.cond["vcm_fraction"] * self.point.vdd

    def tokens(self, **extra: object) -> dict[str, object]:
        corner = self.point.corner
        seed = "* nominal models (no Monte Carlo seed)"
        if self.seed is not None:
            seed = f".option seed={self.seed}"
        tokens: dict[str, object] = {
            "SEED_OPTION": seed,
            "MODEL_LIB": self.cond["model_lib"],
            "CORNER": corner,
            "DUT_FILE": self.dut_file,
            "DUT": self.dut,
            "TEMP": self.point.temp,
            "VDD": self.point.vdd,
            "VCM": self.vcm,
            "VCM_FRACTION": self.cond["vcm_fraction"],
            "IBIAS": self.cond["ibias"],
            "RL": self.cond["rl"],
            "CL": self.cond["cl"],
            "R_NET": self.cond["r_net"],
            "R_GM": self.cond["r_gm"],
            "C_NET": self.cond["c_net"],
            "MC_PARAM": "* no Monte Carlo switch override",
            "IOUT_DC": 0.0,
            "RL_NODE": "vcm",
            "RS": 1e-3,
        }
        tokens.update(self.overrides)
        tokens.update(extra)
        return tokens

    def run(self, template: str, name: str, **extra: object) -> Run:
        source = (TEMPLATE_ROOT / self.klass / template).read_text()
        text = render(source, self.tokens(RAW=f"{name}.raw", **extra))
        return run_deck(text, self.work, name)


def curve(x, y) -> dict[str, list[float]]:
    return {"x": [float(v) for v in np.asarray(x)], "y": [float(v) for v in np.asarray(y)]}


def edge_curve(t, v, t_edge: float, span: float) -> dict[str, list[float]]:
    """The record around one edge at full resolution, time measured from the edge
    (the whole-record curves are thinned and lose ns-scale detail)."""
    t, v = np.asarray(t), np.asarray(v)
    mask = (t >= t_edge - 0.1 * span) & (t <= t_edge + span)
    return curve(t[mask] - t_edge, v[mask])


def edge_span(settle: float | None, fallback: float, ceiling: float) -> float:
    """Window after an edge: four settling times, or a fallback when unknown."""
    span = 4.0 * settle if settle else fallback
    return min(max(span, fallback / 4.0), ceiling)


def at(freq: np.ndarray, values: np.ndarray, f0: float) -> float:
    return M.value_at(freq, values, f0)


def float_range(start: float, stop: float, step: float) -> list[float]:
    count = int(round((stop - start) / step))
    return [round(start + k * step, 6) for k in range(count + 1)]


def sine_timing(freq: float, settle: int, measure: int, per_period: int = 4000) -> dict[str, float]:
    period = 1.0 / freq
    tmax = period / per_period
    return {
        "FREQ": freq,
        "SETTLE_CYCLES": settle,
        "TSTEP": tmax,
        "TSTOP": (settle + measure) * period,
        "TMAX": tmax,
    }


def first_stable_tau(ctx: Context) -> float:
    """Closed-loop time constant estimate used to size transient windows."""
    ugf = ctx.cache.get("ugf_hz") or 1e6
    beta = ctx.cache.get("feedback_factor", 1.0)
    return 1.0 / (2.0 * math.pi * ugf * beta)


def dut_netlist(path: str, subckt: str) -> tuple[list[str], list[dict]]:
    """Pins and transistor instances of the design's subcircuit."""
    lines: list[str] = []
    for raw in Path(path).read_text().splitlines():
        text = raw.split("$", 1)[0].rstrip()
        if text.startswith("+") and lines:
            lines[-1] += " " + text[1:].strip()
        else:
            lines.append(text)
    pins: list[str] = []
    devices: list[dict] = []
    inside = False
    for text in lines:
        words = text.split()
        if not words or words[0].startswith("*"):
            continue
        head = words[0].lower()
        if head == ".subckt" and len(words) > 1 and words[1].lower() == subckt.lower():
            inside = True
            pins = [w.lower() for w in words[2:] if "=" not in w]
            continue
        if inside and head.startswith(".ends"):
            break
        if inside and head.startswith("x") and len(words) >= 6:
            d, g, src, b = (w.lower() for w in words[1:5])
            devices.append({"name": head, "d": d, "g": g, "s": src, "b": b,
                            "model": words[5].lower(), "line": text})
    return pins, devices


INPUT_PINS = {"se": (3, 4), "fd": (4, 5)}  # positions of vinn/vinp (SE) and vinp/vinn (FD)
TB_NODES = {"se": ["vss", "ibias", "vdd", "out", "vcm", "out"],
            "fd": ["vss", "ibias", "vdd", "vcm", "inp", "inn", "outp", "outn"]}


def input_pair(ctx: Context) -> tuple[list[str], list[dict], dict | None]:
    pins, devices = dut_netlist(ctx.dut_file, ctx.dut)
    gates = {pins[i] for i in INPUT_PINS[ctx.klass]}
    inputs = [dev for dev in devices if dev["g"] in gates]
    sources = {dev["s"] for dev in inputs}
    tail = next((dev for dev in devices if dev not in inputs and dev["d"] in sources), None)
    return pins, inputs, tail


def node_probe(ctx: Context, pins: list[str], node: str) -> str:
    if node in pins:
        return f"v({TB_NODES[ctx.klass][pins.index(node)]})"
    return f"v(xdut.{node})"


def device_path(dev: dict) -> str:
    return f"m.xdut.{dev['name']}.m{dev['model']}"


def op_icmr(ctx: Context, name: str) -> dict:
    """Input common-mode range estimated from the operating point (spec
    3.2.1.3: compute it from the static operating point)."""
    pins, inputs, tail = input_pair(ctx)
    if not inputs or tail is None:
        return {"metrics": {"icmr_op_lo_v": None, "icmr_op_hi_v": None}}
    prints = []
    for dev in inputs + [tail]:
        path = device_path(dev)
        prints.append(f"print @{path}[vgs] @{path}[vth] @{path}[vdsat] @{path}[vds]")
    for dev in inputs:
        prints.append(f"print {node_probe(ctx, pins, dev['d'])} {node_probe(ctx, pins, dev['s'])}")
    run = ctx.run("tb_op_devices.spi", name, PRINTS="\n".join(prints))
    values = {}
    for line in run.log.splitlines():
        match = re.match(r"^\s*(\S+)\s*=\s*([-+0-9.eE]+)\s*$", line)
        if match:
            values[match.group(1).lower()] = float(match.group(2))

    def get(dev: dict, key: str) -> float:
        return values[f"@{device_path(dev)}[{key}]"]

    pmos = "pfet" in inputs[0]["model"]
    vgs = max(abs(get(dev, "vgs")) for dev in inputs)
    vth = [abs(get(dev, "vth")) for dev in inputs]
    vdsat_tail = abs(get(tail, "vdsat"))
    drains = [values[node_probe(ctx, pins, dev["d"]).lower()] for dev in inputs]
    if pmos:
        hi = ctx.point.vdd - vgs - vdsat_tail
        lo = max(d - t for d, t in zip(drains, vth))
    else:
        lo = vgs + vdsat_tail
        hi = min(d + t for d, t in zip(drains, vth))
    # The first-order estimate can land outside the rails (e.g. drain + Vth > VDD
    # for an NMOS pair under PMOS folding sources); the inputs cannot.
    return {"metrics": {
        "icmr_op_lo_v": max(lo, 0.0),
        "icmr_op_hi_v": min(hi, ctx.point.vdd),
        "input_vgs_v": vgs,
        "input_vth_v": max(vth),
        "tail_vdsat_v": vdsat_tail,
        "tail_vds_v": abs(get(tail, "vds")),
        "input_drain_v": min(drains) if not pmos else max(drains),
        "input_devices": " ".join(dev["name"] for dev in inputs),
        "tail_device": tail["name"],
    }}


def swing_metrics(vi: np.ndarray, vo: np.ndarray, vcm: float, fraction: float) -> dict:
    gain = M.smooth_derivative(vi, vo, 5)
    g0 = float(np.interp(vcm, vi, gain))
    span = M.contiguous_range(vi, np.abs(gain) >= fraction * abs(g0), vcm)
    out = {"gain": gain, "g0": g0, "span": span, "lo": None, "hi": None}
    if span:
        ends = np.interp(np.array(span), vi, vo)
        out["lo"], out["hi"] = float(ends.min()), float(ends.max())
    return out


def rl_label(value: float) -> str:
    for unit, scale in (("meg", 1e6), ("k", 1e3)):
        if value >= scale:
            return f"{value / scale:g}{unit}"
    return f"{value:g}"


def temp_dc_range(temp: np.ndarray, err: np.ndarray, ref_temp: float, tol: float):
    ref = float(np.interp(ref_temp, temp, err))
    return M.contiguous_range(temp, np.abs(err - ref) <= tol, ref_temp)


# --------------------------------------------------------------------------
# Single-ended class (spec 3.2 and OTA 3.2.x, plus 3.3)
# --------------------------------------------------------------------------

def se_offset_op(ctx: Context) -> dict:
    op = ctx.run("tb_offset_op.spi", "se01").plot("Operating")
    icc = -float(op["i(vdd)"][0])
    vos = float(op["v(out)"][0] - op["v(vi)"][0])
    ctx.cache["vos_v"] = vos
    ctx.cache["icc_a"] = icc
    return {"metrics": {
        "icc_a": icc,
        "pd_w": icc * ctx.point.vdd,
        "vos_v": vos,
        "vbi_v": float(op["v(ibias)"][0]),
    }}


def bias_tracking(ctx: Context, name: str) -> dict:
    """How the supply current follows the bias current: the exponent n of Icc ~ Ibias^n
    between the two ends of the sweep.  Currents mirrored from ibias give about 1, and
    bias generated any other way gives about 0 (only the bias current itself moves)."""
    fraction = ctx.cond["ibias_tracking_fraction"]
    low, high = ctx.cond["ibias"] * (1.0 - fraction), ctx.cond["ibias"] * (1.0 + fraction)
    icc = [-float(ctx.run("tb_offset_op.spi", f"{name}_{k}", IBIAS=current).plot("Operating")["i(vdd)"][0])
           for k, current in enumerate((low, high))]
    return {"metrics": {
        "icc_low_bias_a": icc[0],
        "icc_high_bias_a": icc[1],
        "icc_tracking": math.log(icc[1] / icc[0]) / math.log(high / low),
    }}


def se_bias_tracking(ctx: Context) -> dict:
    return bias_tracking(ctx, "se30")


def se_offset_tempco(ctx: Context) -> dict:
    t0, t1 = ctx.cond["temp_sweep"]
    dc = ctx.run("tb_offset_tempco.spi", "se02", TEMP_START=t0, TEMP_STOP=t1).plot("DC")
    temp = dc.scale.real
    vos = dc["v(out)"] - dc["v(vi)"]
    slope = M.smooth_derivative(temp, vos, 5)
    metrics = {
        "tcvos_avg_v_per_c": float((vos[-1] - vos[0]) / (temp[-1] - temp[0])),
        "tcvos_max_v_per_c": float(np.max(np.abs(slope))),
        "vos_span_v": float(vos.max() - vos.min()),
    }
    result = {"metrics": metrics}
    if ctx.detail:
        # Spec 3.3.2.2 as written: DC sweep of temperature on the application.
        lo, hi = ctx.cond["temp_range_sweep"][:2]
        wide = ctx.run("tb_offset_tempco.spi", "se02w", TEMP_START=lo, TEMP_STOP=hi).plot("DC")
        tw = wide.scale.real
        ew = wide["v(out)"] - wide["v(vi)"]
        span = temp_dc_range(tw, ew, ctx.point.temp, ctx.cond["follower_tolerance"])
        metrics["temp_dc_lo_c"], metrics["temp_dc_hi_c"] = span if span else (None, None)
        result["curves"] = {"vos_vs_temp": curve(temp, vos), "vos_vs_temp_wide": curve(tw, ew)}
    return result


def se_icmr_closed_loop(ctx: Context) -> dict:
    dc = ctx.run("tb_icmr_closed_loop.spi", "se03").plot("DC")
    vi = dc.scale.real
    vo = dc["v(out)"]
    slope = M.smooth_derivative(vi, vo, 7)
    cmrr = -M.db(slope)
    near = np.abs(vi - ctx.vcm) <= 0.05
    ref = float(np.median(cmrr[near]))
    span = M.contiguous_range(vi, cmrr >= min(ref, ctx.cond["icmr_cmrr_cap_db"]) - 6.0, ctx.vcm)
    metrics = {"cmrr_closed_loop_db": ref, "cl_cm_lo_v": None, "cl_cm_hi_v": None}
    if span:
        metrics["cl_cm_lo_v"], metrics["cl_cm_hi_v"] = span
    result = {"metrics": metrics}
    if ctx.detail:
        result["curves"] = {"vo_vs_vi": curve(vi, vo), "cmrr_db_vs_vi": curve(vi, cmrr)}
    return result


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


def sweep_levels(ops: list[Plot | None], levels: list[float]) -> np.ndarray:
    """The common mode ngspice applied at each level, or the requested one where it found no operating point."""
    return np.array([level if op is None else float(op["v(cms)"][0]) for op, level in zip(ops, levels)])


def se_icmr_cmrr_sweep(ctx: Context) -> dict:
    vdd = ctx.point.vdd
    levels = float_range(0.0, vdd, ctx.cond["icmr_step"])
    run = ctx.run(
        "tb_icmr_cmrr_sweep.spi", "se04",
        VCM_LIST=" ".join(f"{v:.6g}" for v in levels), F_LOW=ctx.cond["f_low"],
    )
    ops, acs = icmr_sweep(run, levels)
    vcm = sweep_levels(ops, levels)
    held = np.abs(sweep_values(ops, lambda op: float(op["v(outd)"][0])) - ctx.vcm) < 0.05
    ad = sweep_values(acs, lambda ac: abs(ac["v(outd)"][0]))
    acm = sweep_values(acs, lambda ac: abs(ac["v(outc)"][0]))
    ad_db = M.db(ad)
    cmrr_db = M.db(ad / np.maximum(acm, 1e-300))
    near = np.abs(vcm - ctx.vcm) <= 0.05
    ref_cmrr = float(np.nanmedian(cmrr_db[near]))
    ref_ad = float(np.nanmedian(ad_db[near]))
    ok = held & (cmrr_db >= min(ref_cmrr, ctx.cond["icmr_cmrr_cap_db"]) - 6.0) & (ad_db >= ref_ad - 6.0)
    span = M.contiguous_range(vcm, ok, ctx.vcm)
    metrics = {"cmrr_servo_db": ref_cmrr, "icmr_lo_v": None, "icmr_hi_v": None}
    if span:
        metrics["icmr_lo_v"], metrics["icmr_hi_v"] = span
    result = {"metrics": metrics}
    if ctx.detail:
        result["curves"] = {"cmrr_db_vs_vcm": curve(vcm, cmrr_db), "ad_db_vs_vcm": curve(vcm, ad_db)}
    return result


def se_icmr_follower(ctx: Context) -> dict:
    dc = ctx.run("tb_icmr_follower.spi", "se05").plot("DC")
    vi = dc.scale.real
    err = dc["v(out)"] - vi
    vos = float(np.interp(ctx.vcm, vi, err))
    span = M.contiguous_range(vi, np.abs(err - vos) <= ctx.cond["follower_tolerance"], ctx.vcm)
    metrics = {"follower_lo_v": None, "follower_hi_v": None}
    if span:
        metrics["follower_lo_v"], metrics["follower_hi_v"] = span
    result = {"metrics": metrics}
    if ctx.detail:
        result["curves"] = {"vo_vs_vi": curve(vi, dc["v(out)"])}
    return result


def se_swing_dc(ctx: Context) -> dict:
    dc = ctx.run("tb_swing_dc.spi", "se06").plot("DC")
    vi = dc.scale.real
    vo = dc["v(out)"]
    sw = swing_metrics(vi, vo, ctx.vcm, ctx.cond["swing_gain_fraction"])
    metrics = {"closed_loop_gain_mid": sw["g0"], "vout_min_v": sw["lo"], "vout_max_v": sw["hi"],
               "vopp_v": None if sw["lo"] is None else sw["hi"] - sw["lo"]}
    ctx.cache["vopp_v"] = metrics["vopp_v"]
    result = {"metrics": metrics}
    if ctx.detail:
        # Fig. 6: output swing against the load resistance.
        loads, swings = [], []
        for k, rl in enumerate(ctx.cond["swing_rl_sweep"]):
            d = ctx.run("tb_swing_dc.spi", f"se06rl_{k}", RL=rl).plot("DC")
            w = swing_metrics(d.scale.real, d["v(out)"], ctx.vcm, ctx.cond["swing_gain_fraction"])
            value = None if w["lo"] is None else w["hi"] - w["lo"]
            metrics[f"vopp_rl_{rl_label(rl)}_v"] = value
            loads.append(rl)
            swings.append(float("nan") if value is None else value)
        result["curves"] = {"vo_vs_vi": curve(vi, vo), "gain_vs_vi": curve(vi, sw["gain"]),
                            "vopp_vs_rl": curve(loads, swings)}
    return result


def sine_thd(ctx: Context, template: str, name: str, amp: float, freq: float,
             probe: str = "v(out)", **extra: object) -> dict:
    settle, measure = ctx.cond["thd_settle_cycles"], ctx.cond["thd_measure_cycles"]
    timing = sine_timing(freq, settle, measure)
    tran = ctx.run(template, name, AMP=amp, **timing, **extra).plot("Transient")
    t = tran.scale.real
    if probe == "diff":
        v = tran["v(outp)"] - tran["v(outn)"]
    else:
        v = tran[probe]
    return M.thd(t, v, freq, settle / freq, measure)


def amplitude_search(ctx: Context, template: str, prefix: str, freq: float, amps: list[float],
                     probe: str, **extra: object) -> tuple[float | None, list[tuple[float, float]]]:
    """Largest amplitude (same units as ``amps``) whose THD stays within the limit."""
    limit = ctx.cond["thd_limit_db"]
    curve_points = []
    best = None
    failures = 0
    for k, amp in enumerate(amps):
        res = sine_thd(ctx, template, f"{prefix}_{k}", amp, freq, probe, **extra)
        curve_points.append((amp, res["thd_db"]))
        if res["thd_db"] <= limit:
            best = amp
            failures = 0
        else:
            failures += 1
            if failures >= 2:
                break
    if best is not None:
        # Interpolate between the last passing and the first failing amplitude.
        for (a0, d0), (a1, d1) in zip(curve_points, curve_points[1:]):
            if a0 == best and d1 > limit:
                best = a0 + (limit - d0) / (d1 - d0) * (a1 - a0)
                break
    return best, curve_points


def swing_amplitudes(ctx: Context) -> list[float]:
    """Peak amplitudes for the TRAN swing check: 50 mV steps, then 12.5 mV steps
    over the last 100 mV, ending with the output touching both rails."""
    top = 0.5 * ctx.point.vdd
    coarse = [a for a in float_range(0.05, top - 0.1, 0.05) if a < top - 0.1 + 1e-9]
    return coarse + [top - 0.1 + 0.0125 * k for k in range(1, 9)]


def se_swing_tran(ctx: Context) -> dict:
    amps = swing_amplitudes(ctx)
    best, points = amplitude_search(ctx, "tb_inverting_sine.spi", "se07", ctx.cond["swing_tran_freq"],
                                    amps, "v(out)")
    result = {"metrics": {"vopp_tran_v": None if best is None else 2.0 * best,
                          "vopp_tran_at_rail": best is not None and best >= amps[-1] - 1e-12}}
    if ctx.detail:
        result["curves"] = {"thd_db_vs_amp_peak": curve([p[0] for p in points], [p[1] for p in points])}
    return result


def se_openloop_ac(ctx: Context) -> dict:
    ac = ctx.run("tb_openloop_ac.spi", "se08").plot("AC")
    freq = ac.scale.real
    loop = M.loop_metrics(freq, ac["v(outa)"])
    admittance = -ac["i(via)"]
    k = int(np.argmin(np.abs(freq - 1e3)))
    conductance = float(admittance[k].real)
    ctx.cache["ugf_hz"] = loop["ugf_hz"]
    ctx.cache["avo_db"] = loop["dc_gain_db"]
    ctx.cache["pm_deg"] = loop["phase_margin_deg"]
    ctx.cache["gain_margin_db"] = loop["gain_margin_db"]
    metrics = {
        "avo_db": loop["dc_gain_db"],
        "gbw_hz": loop["ugf_hz"],
        "pm_deg": loop["phase_margin_deg"],
        "gain_margin_db": loop["gain_margin_db"],
        "f180_hz": loop["f180_hz"],
        "unity_crossings": loop["unity_crossings"],
        "ro_ohm": float(abs(ac["v(outb)"][0])),
        "cin_f": float(abs(admittance[k].imag) / (2.0 * math.pi * freq[k])),
        "rid_ohm": float(1.0 / conductance) if conductance > 1e-18 else float("inf"),
    }
    result = {"metrics": metrics}
    if ctx.detail:
        h = ac["v(outa)"]
        result["curves"] = {
            "gain_db": curve(freq, M.db(h)),
            "phase_deg": curve(freq, M.unwrapped_phase_deg(h)),
        }
    return result


def se_closedloop_ac(ctx: Context) -> dict:
    ac = ctx.run("tb_closedloop_ac.spi", "se09").plot("AC")
    freq = ac.scale.real
    h = ac["v(out)"]
    bw = M.bandwidths(freq, h)
    result = {"metrics": {
        "cl_f3db_hz": bw["f_3db_hz"],
        "cl_f0p1db_hz": bw["f_0p1db_hz"],
        "cl_peaking_db": bw["peaking_db"],
        "group_delay_lf_s": bw["group_delay_lf_s"],
        "group_delay_max_s": bw["group_delay_max_s"],
    }}
    if ctx.detail:
        phase = np.unwrap(np.angle(h))
        result["curves"] = {
            "gain_db": curve(freq, M.db(h)),
            "group_delay_s": curve(freq, -np.gradient(phase, 2.0 * math.pi * freq)),
        }
    return result


def se_cmrr(ctx: Context) -> dict:
    ac = ctx.run("tb_cmrr.spi", "se10").plot("AC")
    freq = ac.scale.real
    cmrr = M.db(ac["v(outd)"]) - M.db(ac["v(outc)"])
    network = -M.db(ac["v(outf)"])
    result = {"metrics": {
        "cmrr_db": float(cmrr[0]),
        "cmrr_1khz_db": at(freq, cmrr, 1e3),
        "cmrr_1mhz_db": at(freq, cmrr, 1e6),
        "cmrr_fig10_db": float(network[0]),
    }}
    if ctx.detail:
        result["curves"] = {"cmrr_db": curve(freq, cmrr), "cmrr_fig10_db": curve(freq, network)}
    return result


def se_psrr(ctx: Context) -> dict:
    ac = ctx.run("tb_psrr.spi", "se11").plot("AC")
    freq = ac.scale.real
    plus, minus = -M.db(ac["v(outp)"]), -M.db(ac["v(outn)"])
    metrics = {
        "psrr_plus_db": float(plus[0]),
        "psrr_plus_1khz_db": at(freq, plus, 1e3),
        "psrr_plus_1mhz_db": at(freq, plus, 1e6),
        "psrr_minus_db": float(minus[0]),
        "psrr_minus_1mhz_db": at(freq, minus, 1e6),
    }
    result = {"metrics": metrics}
    if ctx.detail:
        # Fig. 14: PSRR against temperature.
        temps, values = [], []
        for k, temp in enumerate(ctx.cond["psrr_temps"]):
            a = ctx.run("tb_psrr.spi", f"se11t_{k}", TEMP=temp).plot("AC")
            temps.append(temp)
            values.append(float(-M.db(a["v(outp)"])[0]))
        metrics["psrr_plus_min_over_temp_db"] = min(values)
        result["curves"] = {"psrr_plus_db": curve(freq, plus), "psrr_minus_db": curve(freq, minus),
                            "psrr_plus_db_vs_temp": curve(temps, values)}
    return result


def se_zout(ctx: Context) -> dict:
    ac = ctx.run("tb_zout.spi", "se12").plot("AC")
    freq = ac.scale.real
    z = np.abs(ac["v(out)"])
    k = int(np.argmax(z))
    result = {"metrics": {
        "zout_1khz_ohm": at(freq, z, 1e3),
        "zout_1mhz_ohm": at(freq, z, 1e6),
        "zout_peak_ohm": float(z[k]),
        "zout_peak_hz": float(freq[k]),
    }}
    if ctx.detail:
        result["curves"] = {"zout_ohm": curve(freq, z)}
    return result


def noise_metrics(ctx: Context, plot) -> dict:
    freq = plot.scale.real
    onoise = np.abs(plot["onoise_spectrum"])
    inoise = np.abs(plot["inoise_spectrum"])
    f_lo, f_hi = 1.0, 1e9
    band = (freq >= f_lo) & (freq <= f_hi)
    vn_out = float(np.sqrt(M.trapezoid(onoise[band] ** 2, freq[band])))
    ugf = ctx.cache.get("ugf_hz") or 1e6
    white_band = (freq >= 1e4) & (freq <= max(min(1e6, ugf / 10.0), 2e4))
    floor = float(np.min(inoise[white_band])) if np.any(white_band) else float(inoise[-1])
    above = np.nonzero((inoise > math.sqrt(2.0) * floor) & (freq < 1e6))[0]
    corner = float(freq[above[-1]]) if len(above) else None
    # Spec 3.2.2.6 asks for the band's total output noise, the equivalent
    # input noise (output noise over the in-band gain), the average noise
    # density and the spectrum.
    gain = at(freq, onoise, 1e3) / max(at(freq, inoise, 1e3), 1e-30)
    return {
        "en_1khz_v_rthz": at(freq, inoise, 1e3),
        "en_1mhz_v_rthz": at(freq, inoise, 1e6),
        "en_white_v_rthz": floor,
        "flicker_corner_hz": corner,
        "vn_out_rms_v": vn_out,
        "vn_in_equivalent_rms_v": vn_out / gain if gain > 0 else None,
        "vn_out_avg_density_v_rthz": vn_out / math.sqrt(f_hi - f_lo),
    }, freq, inoise


def se_noise(ctx: Context) -> dict:
    plot = ctx.run("tb_noise.spi", "se13").plot("Noise Spectral")
    metrics, freq, inoise = noise_metrics(ctx, plot)
    result = {"metrics": metrics}
    if ctx.detail:
        result["curves"] = {"en_v_rthz": curve(freq, inoise)}
    return result


def step_run(ctx: Context, template: str, name: str, low: float, high: float,
             probe: str = "v(out)", rise_delta: float | None = None,
             **extra: object) -> tuple[dict, dict]:
    """One rising and one falling input edge; ``rise_delta`` is the commanded
    output change on the rising edge (``high - low`` for a follower)."""
    tau = first_stable_tau(ctx)
    half = min(max(1500.0 * tau, 200e-9), 400e-6)
    delta = (high - low) if rise_delta is None else rise_delta
    for attempt in range(3):
        edge = max(tau / 20.0, 1e-12)
        delay = half / 10.0
        tmax = min(tau / 10.0, half / 2000.0)
        tokens = {
            "V_LO": low, "V_HI": high, "V_HI_NEG": -high, "T_DELAY": delay, "T_EDGE": edge,
            "T_WIDTH": half, "T_PERIOD": 4.0 * (half + edge),
            "TSTEP": tmax, "TSTOP": delay + 2.0 * (half + edge), "TMAX": tmax,
        }
        tokens.update(extra)
        tran = ctx.run(template, f"{name}_{attempt}", **tokens).plot("Transient")
        t = tran.scale.real
        v = tran["v(outp)"] - tran["v(outn)"] if probe == "diff" else tran[probe]
        before = float(v[(t > 0.5 * delay) & (t < delay)].mean())
        t_fall = delay + edge + half
        rise = M.step_metrics(t, v, delay, t_fall, before, before + delta)
        mid = float(v[(t > t_fall - 0.05 * half) & (t < t_fall)].mean())
        fall = M.step_metrics(t, v, t_fall, t[-1], mid, before)
        if rise["settle_0p1pct_s"] is not None and fall["settle_0p1pct_s"] is not None:
            break
        half *= 4.0
    waves = {}
    if ctx.detail:
        span = edge_span(rise["settle_0p1pct_s"], 40.0 * tau, half)
        waves = {"t": t, "v": v, "edge": edge_curve(t, v, delay, span)}
    return {"rise": rise, "fall": fall}, waves


def se_step_small(ctx: Context) -> dict:
    step = ctx.cond["small_step"]
    edges, waves = step_run(ctx, "tb_step.spi", "se15", ctx.vcm - step / 2, ctx.vcm + step / 2)
    result = {"metrics": small_step_metrics(edges)}
    if ctx.detail:
        # Spec section 2: a non-zero source resistance with the input
        # capacitance adds overshoot.
        rs_edges, rs_waves = step_run(ctx, "tb_step.spi", "se15rs", ctx.vcm - step / 2, ctx.vcm + step / 2,
                                      RS=ctx.cond["source_resistance"])
        rs = small_step_metrics(rs_edges)
        result["metrics"]["small_rs_overshoot_pct"] = rs["small_overshoot_pct"]
        result["metrics"]["small_rs_settle_0p1pct_s"] = rs["small_settle_0p1pct_s"]
        result["curves"] = {"vout": curve(waves["t"], waves["v"]),
                            "vout_rs": curve(rs_waves["t"], rs_waves["v"]),
                            "vout_edge": waves["edge"], "vout_rs_edge": rs_waves["edge"]}
    return result


def small_step_metrics(edges: dict) -> dict:
    rise, fall = edges["rise"], edges["fall"]
    return {
        "small_rise_10_90_s": rise["rise_10_90_s"],
        "small_overshoot_pct": max(rise["overshoot_pct"] or 0.0, fall["overshoot_pct"] or 0.0),
        "small_settle_0p1pct_s": None if None in (rise["settle_0p1pct_s"], fall["settle_0p1pct_s"])
        else max(rise["settle_0p1pct_s"], fall["settle_0p1pct_s"]),
        "small_settle_1pct_s": None if None in (rise["settle_1pct_s"], fall["settle_1pct_s"])
        else max(rise["settle_1pct_s"], fall["settle_1pct_s"]),
        "small_static_error_pct": 100.0 * max(rise["static_error_fraction"], fall["static_error_fraction"]),
    }


def se_step_large(ctx: Context) -> dict:
    step = ctx.cond["large_step"]
    edges, waves = step_run(ctx, "tb_step.spi", "se16", ctx.vcm - step / 2, ctx.vcm + step / 2)
    rise, fall = edges["rise"], edges["fall"]
    ctx.cache["sr_v_per_s"] = min(
        rise["slew_max_v_per_s"] or float("inf"), fall["slew_max_v_per_s"] or float("inf"))
    result = {"metrics": {
        "sr_rise_v_per_s": rise["slew_max_v_per_s"],
        "sr_fall_v_per_s": fall["slew_max_v_per_s"],
        "sr_rise_10_90_v_per_s": rise["slew_10_90_v_per_s"],
        "sr_fall_10_90_v_per_s": fall["slew_10_90_v_per_s"],
        "large_settle_rise_0p1pct_s": rise["settle_0p1pct_s"],
        "large_settle_fall_0p1pct_s": fall["settle_0p1pct_s"],
        "large_overshoot_pct": max(rise["overshoot_pct"] or 0.0, fall["overshoot_pct"] or 0.0),
    }}
    if ctx.detail:
        result["curves"] = {"vout": curve(waves["t"], waves["v"]), "vout_edge": waves["edge"]}
    return result


def thd_label(freq: float) -> str:
    for unit, scale in (("mhz", 1e6), ("khz", 1e3)):
        if freq >= scale:
            return f"{freq / scale:g}{unit}"
    return f"{freq:g}hz"


def thd_suite(ctx: Context, template: str, prefix: str, probe: str) -> dict:
    amp = ctx.cond["thd_amplitude_pp"] / 2.0
    freqs = ctx.cond["thd_frequencies"] if ctx.detail else [ctx.cond["thd_pvt_frequency"]]
    metrics, curves = {}, {}
    for k, freq in enumerate(freqs):
        res = sine_thd(ctx, template, f"{prefix}_{k}", amp, freq, probe)
        label = thd_label(freq)
        metrics[f"thd_{label}_db"] = res["thd_db"]
        metrics[f"hd2_{label}_db"] = res["hd2_db"]
        metrics[f"hd3_{label}_db"] = res["hd3_db"]
    if ctx.detail:
        curves["thd_db_vs_freq"] = curve(freqs, [metrics[f"thd_{thd_label(f)}_db"] for f in freqs])
        curves["hd2_db_vs_freq"] = curve(freqs, [metrics[f"hd2_{thd_label(f)}_db"] for f in freqs])
        curves["hd3_db_vs_freq"] = curve(freqs, [metrics[f"hd3_{thd_label(f)}_db"] for f in freqs])
        # Fig. 25: THD against output amplitude, at a low and a high frequency.
        amps = ctx.cond["thd_sweep_pp"]
        for j, freq in enumerate(ctx.cond["thd_sweep_frequencies"]):
            values = [sine_thd(ctx, template, f"{prefix}a{j}_{k}", a / 2.0, freq, probe)["thd_db"]
                      for k, a in enumerate(amps)]
            curves[f"thd_db_vs_amp_pp_{thd_label(freq)}"] = curve(amps, values)
    result = {"metrics": metrics}
    if curves:
        result["curves"] = curves
    return result


def se_thd(ctx: Context) -> dict:
    return thd_suite(ctx, "tb_follower_sine.spi", "se17", "v(out)")


def frequency_search(ctx: Context, template: str, prefix: str, amp: float, probe: str,
                     **extra: object) -> float | None:
    """Highest frequency whose THD stays within the limit: decade steps from
    1 kHz, then six log-bisection steps inside the failing decade (3.7 %).

    The decade steps start at the decade below a third of the slew-limited
    estimate SR / (2 pi A) when the slew rate is known, and step down from
    there if that frequency already fails, so the long low-frequency
    transients run only when they matter.
    """
    limit = ctx.cond["thd_limit_db"]
    k = 0

    def passes(freq: float) -> bool:
        nonlocal k
        k += 1
        return sine_thd(ctx, template, f"{prefix}_{k - 1}", amp, freq, probe, **extra)["thd_db"] <= limit

    sr = ctx.cache.get("sr_v_per_s")
    lo = 1e3
    if sr and math.isfinite(sr):
        lo = max(1e3, 10.0 ** math.floor(math.log10(sr / (2.0 * math.pi * amp) / 3.0)))
    hi = None   # lowest frequency known to fail
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


def se_fpbw(ctx: Context) -> dict:
    amp = ctx.cond["fpbw_amplitude_pp"] / 2.0
    fpbw = frequency_search(ctx, "tb_inverting_sine.spi", "se18", amp, "v(out)")
    sr = ctx.cache.get("sr_v_per_s")
    return {"metrics": {
        "fpbw_hz": fpbw,
        "fpbw_from_sr_hz": None if not sr or not math.isfinite(sr) else sr / (2.0 * math.pi * amp),
    }}


def gm_sweep(ctx: Context, template: str, name: str, start: float, stop: float, step: float,
             differential: bool) -> tuple[np.ndarray, np.ndarray, float]:
    dc = ctx.run(template, name, VID_START=start, VID_STOP=stop, VID_STEP=step).plot("DC")
    vid = dc.scale.real
    rg = ctx.cond["r_gm"]
    if differential:
        iout = (dc["v(outp)"] - dc["v(outn)"]) / rg
    else:
        iout = (dc["v(out)"] - ctx.vcm) / rg
    icc = -dc["i(vdd)"]
    return vid, iout, icc


def gm_analysis(ctx: Context, template: str, prefix: str, differential: bool) -> dict:
    vid, iout, icc = gm_sweep(ctx, template, f"{prefix}_0", -0.3, 0.3, 1e-3, differential)
    iopp = float(iout.max() - iout.min())
    i_source, i_sink = float(iout.max()), float(-iout.min())
    window = 0.3
    for attempt in range(1, 5):
        null = M.crossing(vid, iout, 0.0)
        if null is None:
            null = float(vid[int(np.argmin(np.abs(iout)))])
        gm = M.smooth_derivative(vid, iout, 3)
        gm0 = float(np.interp(null, vid, gm))
        span = M.contiguous_range(vid, gm >= ctx.cond["gm_fraction"] * gm0, null) if gm0 > 0 else None
        samples = 0 if span is None else int(np.count_nonzero((vid >= span[0]) & (vid <= span[1])))
        if samples >= 40:
            break
        half = 4.0 * max(i_source, i_sink) / max(abs(gm0), 1e-12)
        if span is not None:
            half = min(half, 4.0 * (span[1] - span[0]))
        window = min(max(half, 1e-7), window / 2.0)
        vid, iout, icc = gm_sweep(ctx, template, f"{prefix}_{attempt}", null - window, null + window,
                                  window / 400.0, differential)
    icc_null = float(np.interp(null, vid, icc))
    metrics = {
        "gm_s": gm0,
        "gm_over_icc_per_v": gm0 / icc_null if icc_null > 0 else None,
        "vid_lin_v": None if span is None else span[1] - span[0],
        "vid_lin_lo_v": None if span is None else span[0] - null,
        "vid_lin_hi_v": None if span is None else span[1] - null,
        "gm_null_v": null,
        "iopp_a": iopp,
        "i_source_max_a": i_source,
        "i_sink_max_a": i_sink,
    }
    result = {"metrics": metrics}
    if ctx.detail or ctx.nominal:
        # Table 1 VIDR: the widest differential input with both inputs inside
        # the rails (SE: the inverting input sits at vcom), checked for phase
        # reversal of the output current.
        if differential:
            lo_v, hi_v = -ctx.point.vdd, ctx.point.vdd
        else:
            lo_v, hi_v = -ctx.vcm, ctx.point.vdd - ctx.vcm
        wv, wi, _ = gm_sweep(ctx, template, f"{prefix}_vidr", lo_v, hi_v, 0.01, differential)
        far = np.abs(wv - null) > 0.05
        reversal = bool(np.any(np.sign(wi[far]) != np.sign(wv[far] - null)))
        metrics["vidr_lo_v"], metrics["vidr_hi_v"] = lo_v, hi_v
        metrics["phase_reversal"] = reversal
        if ctx.detail:
            result["curves"] = {"iout_vs_vid": curve(vid, iout), "gm_vs_vid": curve(vid, gm),
                                "iout_vs_vid_wide": curve(wv, wi)}
    return result


def se_gm_dc(ctx: Context) -> dict:
    return gm_analysis(ctx, "tb_gm_dc.spi", "se19", differential=False)


def gm_ac_metrics(ctx: Context, freq: np.ndarray, gm: np.ndarray) -> dict:
    ref = float(gm[0])
    f3 = M.crossing(freq, M.db(gm), float(M.db(ref)) - 3.0103, rising=False, log_x=True)
    return {"gm_lf_s": ref, "gm_f3db_hz": f3}


def se_gm_ac(ctx: Context) -> dict:
    ac = ctx.run("tb_gm_ac.spi", "se20").plot("AC")
    freq = ac.scale.real
    gm = np.abs(ac["v(out)"]) / ctx.cond["r_gm"]
    literal = np.abs(ac["v(outl)"]) / ctx.cond["rl"]
    metrics = gm_ac_metrics(ctx, freq, gm)
    lit = gm_ac_metrics(ctx, freq, literal)
    metrics["gm_fig35_lf_s"], metrics["gm_fig35_f3db_hz"] = lit["gm_lf_s"], lit["gm_f3db_hz"]
    result = {"metrics": metrics}
    if ctx.detail:
        result["curves"] = {"gm_s": curve(freq, gm), "gm_fig35_s": curve(freq, literal)}
    return result


def power_cycle_analysis(ctx: Context, tran, probes: list[str], reference: str, ramp: float) -> dict:
    t = tran.scale.real
    t_start, t_up = 1e-4, 1e-4 + ramp
    t_hold = t_up + ctx.cond["power_hold_s"]
    t_down = t_hold + ramp
    dt = ctx.cond["oscillation_grid_s"]
    window = ctx.cond["oscillation_window_s"]
    threshold = ctx.cond["oscillation_threshold_v"]
    metrics: dict[str, object] = {}
    worst = 0.0
    for label, (a, b) in {"up": (t_start, t_up + 1e-3), "hold": (t_up + 1e-3, t_hold),
                          "down": (t_hold, t_down)}.items():
        for probe in probes:
            v = tran[probe] if probe != "diff" else tran["v(outp)"] - tran["v(outn)"]
            grid, vg = M.uniform(t, v, a, b, max(int((b - a) / dt), 16))
            width = max(int(window / dt), 3)
            trend = np.convolve(np.pad(vg, width // 2, mode="edge"), np.ones(width) / width, mode="valid")[: len(vg)]
            busy = float(np.count_nonzero(np.abs(vg - trend) > threshold) * dt)
            worst = max(worst, busy)
            metrics[f"hf_activity_{label}_s"] = max(metrics.get(f"hf_activity_{label}_s", 0.0), busy)
    ref = tran[reference]
    probe = probes[0]
    v = tran[probe] if probe != "diff" else tran["v(outp)"] - tran["v(outn)"]
    target = ref if probe != "diff" else np.zeros_like(ref)
    hold = (t > t_hold - 0.2 * ctx.cond["power_hold_s"]) & (t < t_hold)
    offset = float(np.mean((v - target)[hold]))
    after = t >= t_up
    error = np.abs(v - target - offset)
    outside = np.nonzero(after & (error > ctx.cond["power_settle_band_v"]) & (t < t_hold))[0]
    metrics["power_up_settle_s"] = float(t[outside[-1]] - t_up) if len(outside) else 0.0
    metrics["hold_offset_v"] = offset
    metrics["oscillation"] = bool(worst > ctx.cond["oscillation_max_busy_s"])
    return metrics


def power_cycle_tokens(ctx: Context, ramp: float) -> dict:
    t_start = 1e-4
    t_up = t_start + ramp
    t_hold = t_up + ctx.cond["power_hold_s"]
    t_down = t_hold + ramp
    return {"T_RAMP": ramp, "T_START": t_start, "T_UP": t_up, "T_HOLD": t_hold,
            "T_DOWN": t_down, "T_END": t_down + 5e-4, "TSTEP": 1e-7, "TMAX": ctx.cond["power_tmax_s"]}


def power_cycles(ctx: Context, name: str, probes: list[str], keep: dict[str, str]) -> dict:
    """Spec 3.3.4.1 with both ends of its 5..10 ms edge range."""
    metrics: dict[str, object] = {}
    curves = {}
    for k, ramp in enumerate(ctx.cond["power_ramps_s"]):
        tran = ctx.run("tb_power_cycle.spi", f"{name}_{k}", **power_cycle_tokens(ctx, ramp)).plot("Transient")
        label = f"{ramp * 1e3:g}ms"
        for key, value in power_cycle_analysis(ctx, tran, probes, "v(vcm)", ramp).items():
            metrics[f"{key}_{label}"] = value
        if ctx.detail and k == 0:
            t = tran.scale.real
            idx = np.linspace(0, len(t) - 1, min(len(t), 4000)).astype(int)
            for vec, label_name in keep.items():
                curves[label_name] = curve(t[idx], tran[vec][idx])
    metrics["oscillation"] = any(v for key, v in metrics.items() if key.startswith("oscillation_"))
    result = {"metrics": metrics}
    if curves:
        result["curves"] = curves
    return result


def se_power_cycle(ctx: Context) -> dict:
    return power_cycles(ctx, "se25", ["v(out)"], {"v(out)": "vout", "v(vdd)": "vdd"})


def supply_step_analysis(ctx: Context, tran, freq: float, probe: str) -> dict:
    t = tran.scale.real
    v = tran[probe] if probe != "diff" else tran["v(outp)"] - tran["v(outn)"]
    period = 1.0 / freq
    delay = period / 4.0
    edges = [delay + k * period / 2.0 for k in range(2 * ctx.cond["supply_step_periods"])]
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
        band = max(ctx.cond["supply_step_band_v"], 0.01 * dev)
        settle = M.settling_time(tt, vv, e0, final, band)
        settle_worst = max(settle_worst, settle if settle is not None else (e1 - e0))
        if float(end.max() - end.min()) > max(0.2 * dev, 2.0 * ctx.cond["supply_step_band_v"]):
            ringing = True
    return {"peak_v": peak, "settle_s": settle_worst, "oscillation": ringing}


def se_supply_step(ctx: Context) -> dict:
    ripple = ctx.cond["supply_step_v"]
    metrics: dict[str, object] = {}
    curves = {}
    for k, freq in enumerate(ctx.cond["supply_step_frequencies"]):
        period = 1.0 / freq
        edge = ctx.cond["supply_step_edge_s"]
        tmax = min(period / 4000.0, max(first_stable_tau(ctx) / 5.0, period / 20000.0))
        tokens = {
            "VDD_LO": ctx.point.vdd - ripple, "VDD_HI": ctx.point.vdd + ripple, "RIPPLE": ripple,
            "FREQ": freq, "T_DELAY": period / 4.0, "T_EDGE": edge, "T_WIDTH": period / 2.0 - edge,
            "T_PERIOD": period, "TSTEP": tmax,
            "TSTOP": period / 4.0 + ctx.cond["supply_step_periods"] * period, "TMAX": tmax,
        }
        tran = ctx.run("tb_supply_step.spi", f"se26_{k}", **tokens).plot("Transient")
        res = supply_step_analysis(ctx, tran, freq, "v(out)")
        label = thd_label(freq)
        metrics[f"supply_step_{label}_peak_v"] = res["peak_v"]
        metrics[f"supply_step_{label}_settle_s"] = res["settle_s"]
        metrics[f"supply_step_{label}_oscillation"] = res["oscillation"]
        if ctx.detail:
            t = tran.scale.real
            curves[f"vout_{label}"] = curve(t, tran["v(out)"])
            span = edge_span(res["settle_s"], 40.0 * first_stable_tau(ctx), period / 2.0)
            curves[f"vout_{label}_edge"] = edge_curve(t, tran["v(out)"], period / 4.0 + period, span)
    result = {"metrics": metrics}
    if ctx.detail:
        result["curves"] = curves
    return result


def cm_transient(ctx: Context, name: str, probe: str) -> dict:
    """Input common-mode step through the closed-loop network; output (SE) or
    output common mode (FD) deviation and settling."""
    tau = first_stable_tau(ctx)
    half = min(max(1500.0 * tau, 200e-9), 400e-6)
    edge = max(tau / 20.0, 1e-12)
    delay = half / 10.0
    tmax = min(tau / 10.0, half / 2000.0)
    tran = ctx.run("tb_cm_step.spi", name, CM_STEP=ctx.cond["cm_step"], T_DELAY=delay, T_EDGE=edge,
                   T_WIDTH=half, T_PERIOD=4.0 * (half + edge), TSTEP=tmax,
                   TSTOP=delay + 2.0 * (half + edge), TMAX=tmax).plot("Transient")
    t = tran.scale.real
    v = 0.5 * (tran["v(outp)"] + tran["v(outn)"]) if probe == "ocm" else tran[probe]
    base = float(v[(t > 0.5 * delay) & (t < delay)].mean())
    band = ctx.cond["power_settle_band_v"]
    metrics = {"cm_peak_dev_v": float(np.max(np.abs(v - base)))}
    worst, shift = 0.0, 0.0
    for t0, t1 in ((delay, delay + edge + half), (delay + edge + half, t[-1])):
        mask = (t >= t0) & (t <= t1)
        final = float(v[mask][-max(int(0.05 * mask.sum()), 2):].mean())
        shift = max(shift, abs(final - base))
        settle = M.settling_time(t[mask], v[mask], t0, final, band)
        worst = max(worst, settle if settle is not None else t1 - t0)
    metrics["cm_static_shift_v"] = shift
    metrics["cm_settle_1mv_s"] = worst
    result = {"metrics": metrics}
    if ctx.detail:
        span = edge_span(worst, 40.0 * tau, half)
        result["curves"] = {"out": curve(t, v), "out_edge": edge_curve(t, v, delay, span)}
    return result


def se_cm_transient(ctx: Context) -> dict:
    return cm_transient(ctx, "se27", "v(out)")


def se_op_icmr(ctx: Context) -> dict:
    return op_icmr(ctx, "se28")


def supply_dc(ctx: Context, name: str, differential: bool) -> dict:
    """Spec 3.3.2.1 as written: DC sweep of the supply of the application."""
    vmax = ctx.cond["supply_sweep"][1]
    probe = ctx.cond["supply_dc_probe_v"]
    dc = ctx.run("tb_supply_dc.spi", name, VDD_MAX=vmax, V_PROBE=probe, V_PROBE_HALF=probe / 2.0,
                 V_PROBE_HALF_NEG=-probe / 2.0).plot("DC")
    vdd = dc.scale.real
    tol = ctx.cond["follower_tolerance"]
    if differential:
        err = 0.5 * (dc["v(outp)"] + dc["v(outn)"]) - dc["v(vcm)"]
        dm = dc["v(outp)"] - dc["v(outn)"] - probe
        dm_ref = float(np.interp(ctx.point.vdd, vdd, dm))
        dm_ok = np.abs(dm - dm_ref) <= tol
    else:
        err = dc["v(out)"] - dc["v(vi)"]
        dm_ok = np.ones_like(err, dtype=bool)
    ref = float(np.interp(ctx.point.vdd, vdd, err))
    ok = (np.abs(err - ref) <= tol) & dm_ok
    span = M.contiguous_range(vdd, ok, ctx.point.vdd)
    result = {"metrics": {"supply_dc_min_v": None if span is None else span[0],
                          "supply_dc_max_v": None if span is None else span[1]}}
    if ctx.detail:
        result["curves"] = {"error_vs_vdd": curve(vdd, err)}
    return result


def se_supply_dc(ctx: Context) -> dict:
    return supply_dc(ctx, "se29", differential=False)


@dataclass(frozen=True)
class Test:
    id: str
    title: str
    spec: str
    func: Callable[[Context], dict]
    pvt: bool = True
    transient: bool = False


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


SE_TESTS: tuple[Test, ...] = (
    Test("SE-01", "Offset, supply current, power, bias-pin voltage", "3.2.1.1 Fig.1; Table 1 ICC/PD/VBI", se_offset_op),
    Test("SE-08", "Open-loop gain, GBW, phase/gain margin, RID, RO", "3.2.2.1 Fig.7; Table 1 AVO/GBW/PM/RID/RO", se_openloop_ac),
    Test("SE-02", "Offset temperature coefficient", "3.2.1.2", se_offset_tempco, pvt=False),
    Test("SE-03", "Closed-loop common-mode range (Fig. 3)", "3.2.1.3 Fig.3", se_icmr_closed_loop),
    Test("SE-04", "Input common-mode range (CMRR -6 dB)", "3.2.1.3; Table 1 ICMR", se_icmr_cmrr_sweep),
    Test("SE-05", "Follower input range", "3.2.1.3 (Fig.1 sweep)", se_icmr_follower),
    Test("SE-06", "Output swing (DC)", "3.2.1.4 Fig.5; Table 1 VOPP", se_swing_dc),
    Test("SE-07", "Output swing (TRAN, THD limit)", "3.2.1.4 TRAN check", se_swing_tran, pvt=False, transient=True),
    Test("SE-09", "Closed-loop response, 0.1 dB flatness, group delay", "3.2.2.2, 3.2.2.7", se_closedloop_ac),
    Test("SE-10", "CMRR", "3.2.2.3 Fig.10; Table 1 CMRR", se_cmrr),
    Test("SE-11", "PSRR+ / PSRR-", "3.2.2.4 Fig.12; Table 1 PSRR", se_psrr),
    Test("SE-12", "Closed-loop output impedance", "3.2.2.5 Fig.15", se_zout),
    Test("SE-13", "Noise", "3.2.2.6 Fig.17", se_noise),
    Test("SE-15", "Small-signal step response", "3.2.3.1 Figs.19-21", se_step_small, transient=True),
    Test("SE-16", "Large-signal step: slew rate and settling", "3.2.3.1 Figs.19-23; Table 1 SR/TS", se_step_large, transient=True),
    Test("SE-17", "Total harmonic distortion", "3.2.3.2; Table 1 THD", se_thd, transient=True),
    Test("SE-18", "Full-power bandwidth", "3.2.3.2; Table 1 BWfull", se_fpbw, pvt=False, transient=True),
    Test("SE-19", "Transconductance, linear range, gm/ICC, IOPP", "OTA 3.2.1.1 Fig.33; Table 1 gm/VID/IOPP", se_gm_dc),
    Test("SE-20", "Transconductance frequency response", "OTA 3.2.2.1 Fig.35", se_gm_ac),
    Test("SE-25", "Power-up / power-down stability (5 and 10 ms ramps)", "3.3.4.1", se_power_cycle, pvt=False, transient=True),
    Test("SE-26", "Supply surge stability", "3.3.4.2", se_supply_step, pvt=False, transient=True),
    Test("SE-27", "Common-mode transient of the Fig. 3 network", "3.2.1.3 TRAN check", se_cm_transient, transient=True),
    Test("SE-28", "Input common-mode range from the operating point", "3.2.1.3 (OP calculation)", se_op_icmr),
    Test("SE-29", "Operating supply range, DC sweep", "3.3.2.1 (DC sweep)", se_supply_dc, pvt=False),
    Test("SE-30", "Supply current against the bias current", "Bias pin contract", se_bias_tracking, pvt=False),
)


# --------------------------------------------------------------------------
# Fully-differential class (spec 3.1, OTA Fig. 34, plus 3.2 / 3.3 adapted)
# --------------------------------------------------------------------------

def diff_cm(plot, p: str = "v(outp)", n: str = "v(outn)") -> tuple[np.ndarray, np.ndarray]:
    return plot[p] - plot[n], 0.5 * (plot[p] + plot[n])


def fd_offset_op(ctx: Context) -> dict:
    op = ctx.run("tb_offset_op.spi", "fd01").plot("Operating")
    vod, vocm = diff_cm(op)
    icc = -float(op["i(vdd)"][0])
    vos_in = float(vod[0]) / 2.0  # noise gain 1 + R2/R1 = 2
    ctx.cache["icc_a"] = icc
    ctx.cache["feedback_factor"] = 0.5
    return {"metrics": {
        "icc_a": icc,
        "pd_w": icc * ctx.point.vdd,
        "vos_out_v": float(vod[0]),
        "vos_in_v": vos_in,
        "vocm_error_v": float(vocm[0] - ctx.vcm),
        "vbi_v": float(op["v(ibias)"][0]),
        "vcm_pin_current_a": abs(float(op["v(vcm)"][0]) - float(op["v(vcmpin)"][0])) / VCM_SOURCE_OHM,
    }}


def fd_bias_tracking(ctx: Context) -> dict:
    return bias_tracking(ctx, "fd30")


def fd_offset_tempco(ctx: Context) -> dict:
    t0, t1 = ctx.cond["temp_sweep"]
    dc = ctx.run("tb_offset_tempco.spi", "fd02", TEMP_START=t0, TEMP_STOP=t1).plot("DC")
    temp = dc.scale.real
    vod, vocm = diff_cm(dc)
    vos_in = vod / 2.0
    metrics = {
        "tcvos_avg_v_per_c": float((vos_in[-1] - vos_in[0]) / (temp[-1] - temp[0])),
        "tcvos_max_v_per_c": float(np.max(np.abs(M.smooth_derivative(temp, vos_in, 5)))),
        "vocm_drift_v": float(vocm.max() - vocm.min()),
        "tcvocm_max_v_per_c": float(np.max(np.abs(M.smooth_derivative(temp, vocm, 5)))),
    }
    # The symmetric circuit's offset is 0 at every temperature, so its drift is
    # 0 too; the drift that means something is that of the 1 % skewed pair (FD-27).
    skewed = skewed_dut(ctx)
    if skewed is not None:
        sk = ctx.run("tb_offset_tempco.spi", "fd02s", TEMP_START=t0, TEMP_STOP=t1,
                     DUT_FILE=str(skewed[0])).plot("DC")
        sod, _ = diff_cm(sk)
        svos = sod / 2.0
        ts = sk.scale.real
        metrics["tcvos_skew_avg_v_per_c"] = float((svos[-1] - svos[0]) / (ts[-1] - ts[0]))
        metrics["tcvos_skew_max_v_per_c"] = float(np.max(np.abs(M.smooth_derivative(ts, svos, 5))))
    result = {"metrics": metrics}
    if ctx.detail:
        lo, hi = ctx.cond["temp_range_sweep"][:2]
        wide = ctx.run("tb_offset_tempco.spi", "fd02w", TEMP_START=lo, TEMP_STOP=hi).plot("DC")
        tw = wide.scale.real
        wod, wcm = diff_cm(wide)
        tol = ctx.cond["follower_tolerance"]
        ok_cm = temp_dc_range(tw, wcm, ctx.point.temp, tol)
        ok_dm = temp_dc_range(tw, wod / 2.0, ctx.point.temp, tol)
        if ok_cm and ok_dm:
            metrics["temp_dc_lo_c"] = max(ok_cm[0], ok_dm[0])
            metrics["temp_dc_hi_c"] = min(ok_cm[1], ok_dm[1])
        else:
            metrics["temp_dc_lo_c"] = metrics["temp_dc_hi_c"] = None
        result["curves"] = {"vos_in_vs_temp": curve(temp, vos_in), "vocm_vs_temp": curve(temp, vocm),
                            "vocm_vs_temp_wide": curve(tw, wcm)}
    return result


def fd_icmr_closed_loop(ctx: Context) -> dict:
    dc = ctx.run("tb_icmr_closed_loop.spi", "fd03").plot("DC")
    vi = dc.scale.real
    vod, vocm = diff_cm(dc)
    slope = M.smooth_derivative(vi, dc["v(outp)"], 7)
    cmrr = -M.db(slope)
    near = np.abs(vi - ctx.vcm) <= 0.05
    ref = float(np.median(cmrr[near]))
    span = M.contiguous_range(vi, cmrr >= min(ref, ctx.cond["icmr_cmrr_cap_db"]) - 6.0, ctx.vcm)
    vocm_ref = float(np.interp(ctx.vcm, vi, vocm))
    hold = M.contiguous_range(vi, np.abs(vocm - vocm_ref) <= ctx.cond["follower_tolerance"], ctx.vcm)
    metrics = {"cm_to_out_closed_loop_db": -ref, "cl_cm_lo_v": None, "cl_cm_hi_v": None,
               "ocm_hold_lo_v": None, "ocm_hold_hi_v": None}
    if span:
        metrics["cl_cm_lo_v"], metrics["cl_cm_hi_v"] = span
    if hold:
        metrics["ocm_hold_lo_v"], metrics["ocm_hold_hi_v"] = hold
    result = {"metrics": metrics}
    if ctx.detail:
        result["curves"] = {"vocm_vs_vi": curve(vi, vocm), "vop_vs_vi": curve(vi, dc["v(outp)"])}
    return result


def fd_icmr_gain_sweep(ctx: Context) -> dict:
    levels = float_range(0.0, ctx.point.vdd, ctx.cond["icmr_step"])
    run = ctx.run("tb_icmr_gain_sweep.spi", "fd04",
                  VCM_LIST=" ".join(f"{v:.6g}" for v in levels), F_LOW=ctx.cond["f_low"])
    ops, acs = icmr_sweep(run, levels)
    vcm = sweep_levels(ops, levels)
    vocm = sweep_values(ops, lambda op: 0.5 * float(op["v(outp)"][0] + op["v(outn)"][0]))
    vod = sweep_values(ops, lambda op: float(op["v(outp)"][0] - op["v(outn)"][0]))
    ad_db = M.db(sweep_values(acs, lambda ac: abs(ac["v(outp)"][0] - ac["v(outn)"][0])))
    near = np.abs(vcm - ctx.vcm) <= 0.05
    ref = float(np.nanmedian(ad_db[near]))
    ok = (ad_db >= ref - 6.0) & (np.abs(vocm - ctx.vcm) <= 0.05) & (np.abs(vod) <= 0.01)
    span = M.contiguous_range(vcm, ok, ctx.vcm)
    metrics = {"ad_servo_db": ref, "icmr_lo_v": None, "icmr_hi_v": None}
    if span:
        metrics["icmr_lo_v"], metrics["icmr_hi_v"] = span
    result = {"metrics": metrics}
    if ctx.detail:
        result["curves"] = {"ad_db_vs_vcm": curve(vcm, ad_db), "vocm_vs_vcm": curve(vcm, vocm)}
    return result


def fd_swing_dc(ctx: Context) -> dict:
    dc = ctx.run("tb_swing_dc.spi", "fd05", R_HALF=ctx.cond["r_net"] / 2.0).plot("DC")
    vi = dc.scale.real
    vod, _ = diff_cm(dc)
    sw = swing_metrics(vi, vod, ctx.vcm, ctx.cond["swing_gain_fraction"])
    metrics = {"closed_loop_gain_mid": sw["g0"], "vod_min_v": sw["lo"], "vod_max_v": sw["hi"],
               "vodpp_v": None if sw["lo"] is None else sw["hi"] - sw["lo"],
               "vout_min_v": None, "vout_max_v": None, "vopp_v": None}
    if sw["span"]:
        mask = (vi >= sw["span"][0]) & (vi <= sw["span"][1])
        single = np.concatenate([dc["v(outp)"][mask], dc["v(outn)"][mask]])
        metrics["vout_min_v"], metrics["vout_max_v"] = float(single.min()), float(single.max())
        metrics["vopp_v"] = float(single.max() - single.min())
    result = {"metrics": metrics}
    if ctx.detail:
        loads, swings = [], []
        for k, rl in enumerate(ctx.cond["swing_rl_sweep"]):
            d = ctx.run("tb_swing_dc.spi", f"fd05rl_{k}", R_HALF=ctx.cond["r_net"] / 2.0, RL=rl).plot("DC")
            dv, _ = diff_cm(d)
            w = swing_metrics(d.scale.real, dv, ctx.vcm, ctx.cond["swing_gain_fraction"])
            value = None if w["lo"] is None else w["hi"] - w["lo"]
            metrics[f"vodpp_rl_{rl_label(rl)}_v"] = value
            loads.append(rl)
            swings.append(float("nan") if value is None else value)
        result["curves"] = {"vod_vs_vi": curve(vi, vod), "vop_vs_vi": curve(vi, dc["v(outp)"]),
                            "von_vs_vi": curve(vi, dc["v(outn)"]), "vodpp_vs_rl": curve(loads, swings)}
    return result


def fd_swing_tran(ctx: Context) -> dict:
    amps = swing_amplitudes(ctx)
    best, points = amplitude_search(ctx, "tb_inverting_sine.spi", "fd06", ctx.cond["swing_tran_freq"],
                                    amps, "diff")
    result = {"metrics": {
        "vodpp_tran_v": None if best is None else 4.0 * best,
        "vopp_tran_v": None if best is None else 2.0 * best,
        "vopp_tran_at_rail": best is not None and best >= amps[-1] - 1e-12,
    }}
    if ctx.detail:
        result["curves"] = {"thd_db_vs_amp_peak_per_side": curve([p[0] for p in points], [p[1] for p in points])}
    return result


def fd_openloop_ac(ctx: Context) -> dict:
    ac = ctx.run("tb_openloop_ac.spi", "fd07").plot("AC")
    freq = ac.scale.real
    h = ac["v(outp)"] - ac["v(outn)"]
    loop = M.loop_metrics(freq, h)
    admittance = -ac["i(vapa)"] / 0.5  # current per volt on each input when Vid = 1
    k = int(np.argmin(np.abs(freq - 1e3)))
    conductance = float(admittance[k].real)
    ctx.cache["ugf_hz"] = loop["ugf_hz"]
    ctx.cache["avo_db"] = loop["dc_gain_db"]
    ctx.cache["pm_deg"] = loop["phase_margin_deg"]
    ctx.cache["gain_margin_db"] = loop["gain_margin_db"]
    ctx.cache["feedback_factor"] = 0.5
    metrics = {
        "avo_db": loop["dc_gain_db"],
        "gbw_hz": loop["ugf_hz"],
        "pm_deg": loop["phase_margin_deg"],
        "gain_margin_db": loop["gain_margin_db"],
        "f180_hz": loop["f180_hz"],
        "unity_crossings": loop["unity_crossings"],
        "ro_ohm": float(abs(ac["v(opb)"][0] - ac["v(onb)"][0])),
        "cin_f": float(abs(admittance[k].imag) / (2.0 * math.pi * freq[k])),
        "rid_ohm": float(2.0 / conductance) if conductance > 1e-18 else float("inf"),
    }
    result = {"metrics": metrics}
    if ctx.detail:
        result["curves"] = {"gain_db": curve(freq, M.db(h)), "phase_deg": curve(freq, M.unwrapped_phase_deg(h))}
    return result


def fd_closedloop_ac(ctx: Context) -> dict:
    ac = ctx.run("tb_closedloop_ac.spi", "fd08").plot("AC")
    freq = ac.scale.real
    h = ac["v(outp)"] - ac["v(outn)"]
    bw = M.bandwidths(freq, h)
    result = {"metrics": {
        "cl_f3db_hz": bw["f_3db_hz"],
        "cl_f0p1db_hz": bw["f_0p1db_hz"],
        "cl_peaking_db": bw["peaking_db"],
        "group_delay_lf_s": bw["group_delay_lf_s"],
        "group_delay_max_s": bw["group_delay_max_s"],
    }}
    if ctx.detail:
        phase = np.unwrap(np.angle(h))
        result["curves"] = {"gain_db": curve(freq, M.db(h)),
                            "group_delay_s": curve(freq, -np.gradient(phase, 2.0 * math.pi * freq))}
    return result


def fd_cmrr(ctx: Context) -> dict:
    ac = ctx.run("tb_cmrr.spi", "fd09").plot("AC")
    freq = ac.scale.real
    vod, vocm = diff_cm(ac)
    cm_cm = M.db(vocm)
    cm_dm = M.db(vod)
    result = {"metrics": {
        "acm_cm_db": float(cm_cm[0]),
        "acm_cm_1mhz_db": at(freq, cm_cm, 1e6),
        "cmrr_dm_db": float(-cm_dm[0]),
    }}
    if ctx.detail:
        result["curves"] = {"acm_cm_db": curve(freq, cm_cm)}
    return result


def fd_psrr(ctx: Context) -> dict:
    ac = ctx.run("tb_psrr.spi", "fd10").plot("AC")
    freq = ac.scale.real
    vod_p, vocm_p = diff_cm(ac, "v(opp)", "v(onp)")
    vod_n, vocm_n = diff_cm(ac, "v(opn)", "v(onn)")
    plus, minus = -M.db(vocm_p), -M.db(vocm_n)
    metrics = {
        "psrr_plus_cm_db": float(plus[0]),
        "psrr_plus_cm_1mhz_db": at(freq, plus, 1e6),
        "psrr_minus_cm_db": float(minus[0]),
        "psrr_minus_cm_1mhz_db": at(freq, minus, 1e6),
        "psrr_plus_dm_db": float(-M.db(vod_p)[0]),
    }
    result = {"metrics": metrics}
    if ctx.detail:
        temps, values = [], []
        for k, temp in enumerate(ctx.cond["psrr_temps"]):
            a = ctx.run("tb_psrr.spi", f"fd10t_{k}", TEMP=temp).plot("AC")
            _, cm = diff_cm(a, "v(opp)", "v(onp)")
            temps.append(temp)
            values.append(float(-M.db(cm)[0]))
        metrics["psrr_plus_cm_min_over_temp_db"] = min(values)
        result["curves"] = {"psrr_plus_cm_db": curve(freq, plus), "psrr_minus_cm_db": curve(freq, minus),
                            "psrr_plus_cm_db_vs_temp": curve(temps, values)}
    return result


def fd_zout(ctx: Context) -> dict:
    ac = ctx.run("tb_zout.spi", "fd11").plot("AC")
    freq = ac.scale.real
    zd = np.abs(ac["v(outp)"] - ac["v(outn)"])
    zc = np.abs(0.5 * (ac["v(opc)"] + ac["v(onc)"]))
    k = int(np.argmax(zd))
    result = {"metrics": {
        "zout_1khz_ohm": at(freq, zd, 1e3),
        "zout_1mhz_ohm": at(freq, zd, 1e6),
        "zout_peak_ohm": float(zd[k]),
        "zout_peak_hz": float(freq[k]),
        "zout_cm_1khz_ohm": at(freq, zc, 1e3),
        "zout_cm_1mhz_ohm": at(freq, zc, 1e6),
    }}
    if ctx.detail:
        result["curves"] = {"zout_ohm": curve(freq, zd), "zout_cm_ohm": curve(freq, zc)}
    return result


def fd_noise(ctx: Context) -> dict:
    plot = ctx.run("tb_noise.spi", "fd12").plot("Noise Spectral")
    metrics, freq, inoise = noise_metrics(ctx, plot)
    result = {"metrics": metrics}
    if ctx.detail:
        result["curves"] = {"en_v_rthz": curve(freq, inoise)}
    return result


def fd_step(ctx: Context, name: str, vod_step: float) -> tuple[dict, dict]:
    # Each input moves by vod_step/2 in antiphase; with feedback from Von to
    # VP and from Vop to VN the network gives Vod = +Vid (each half inverts).
    half = vod_step / 2.0
    return step_run(ctx, "tb_step.spi", name, 0.0, half, probe="diff", rise_delta=vod_step)


def fd_step_small(ctx: Context) -> dict:
    edges, waves = fd_step(ctx, "fd13", ctx.cond["small_step"])
    result = {"metrics": small_step_metrics(edges)}
    if ctx.detail:
        half = ctx.cond["small_step"] / 2.0
        rs_edges, rs_waves = step_run(ctx, "tb_step.spi", "fd13rs", 0.0, half, probe="diff",
                                      rise_delta=ctx.cond["small_step"], RS=ctx.cond["source_resistance"])
        rs = small_step_metrics(rs_edges)
        result["metrics"]["small_rs_overshoot_pct"] = rs["small_overshoot_pct"]
        result["metrics"]["small_rs_settle_0p1pct_s"] = rs["small_settle_0p1pct_s"]
        result["curves"] = {"vod": curve(waves["t"], waves["v"]), "vod_rs": curve(rs_waves["t"], rs_waves["v"]),
                            "vod_edge": waves["edge"], "vod_rs_edge": rs_waves["edge"]}
    return result


def fd_step_large(ctx: Context) -> dict:
    edges, waves = fd_step(ctx, "fd14", 2.0 * ctx.cond["large_step"])
    rise, fall = edges["rise"], edges["fall"]
    # Slew is reported per output (Vod/2) so it compares with a single-ended output.
    sr_up = rise["slew_max_v_per_s"]
    sr_down = fall["slew_max_v_per_s"]
    ctx.cache["sr_v_per_s"] = 0.5 * min(sr_down or float("inf"), sr_up or float("inf"))
    result = {"metrics": {
        "sr_rise_v_per_s": None if sr_up is None else 0.5 * sr_up,
        "sr_fall_v_per_s": None if sr_down is None else 0.5 * sr_down,
        "sr_diff_v_per_s": None if None in (sr_up, sr_down) else min(sr_up, sr_down),
        "large_settle_rise_0p1pct_s": rise["settle_0p1pct_s"],
        "large_settle_fall_0p1pct_s": fall["settle_0p1pct_s"],
        "large_overshoot_pct": max(rise["overshoot_pct"] or 0.0, fall["overshoot_pct"] or 0.0),
    }}
    if ctx.detail:
        result["curves"] = {"vod": curve(waves["t"], waves["v"]), "vod_edge": waves["edge"]}
    return result


def fd_thd(ctx: Context) -> dict:
    return thd_suite(ctx, "tb_inverting_sine.spi", "fd15", "diff")


def fd_fpbw(ctx: Context) -> dict:
    amp = ctx.cond["fpbw_amplitude_pp"] / 2.0
    fpbw = frequency_search(ctx, "tb_inverting_sine.spi", "fd16", amp, "diff")
    sr = ctx.cache.get("sr_v_per_s")
    return {"metrics": {
        "fpbw_hz": fpbw,
        "fpbw_from_sr_hz": None if not sr or not math.isfinite(sr) else sr / (2.0 * math.pi * amp),
    }}


def fd_gm_dc(ctx: Context) -> dict:
    return gm_analysis(ctx, "tb_gm_dc.spi", "fd17", differential=True)


def fd_gm_ac(ctx: Context) -> dict:
    ac = ctx.run("tb_gm_ac.spi", "fd18").plot("AC")
    freq = ac.scale.real
    gm = np.abs(ac["v(outp)"] - ac["v(outn)"]) / ctx.cond["r_gm"]
    literal = np.abs(ac["v(opl)"] - ac["v(onl)"]) / ctx.cond["rl"]
    metrics = gm_ac_metrics(ctx, freq, gm)
    lit = gm_ac_metrics(ctx, freq, literal)
    metrics["gm_fig35_lf_s"], metrics["gm_fig35_f3db_hz"] = lit["gm_lf_s"], lit["gm_f3db_hz"]
    result = {"metrics": metrics}
    if ctx.detail:
        result["curves"] = {"gm_s": curve(freq, gm), "gm_fig35_s": curve(freq, literal)}
    return result


def fd_cm_step(ctx: Context) -> dict:
    result = cm_transient(ctx, "fd19", "ocm")
    m = result["metrics"]
    result["metrics"] = {"ocm_peak_dev_v": m["cm_peak_dev_v"], "ocm_static_shift_v": m["cm_static_shift_v"],
                         "ocm_settle_1mv_s": m["cm_settle_1mv_s"]}
    if "curves" in result:
        result["curves"] = {"vocm": result["curves"]["out"], "vocm_edge": result["curves"]["out_edge"]}
    return result


def fd_power_cycle(ctx: Context) -> dict:
    return power_cycles(ctx, "fd25", ["ocm", "diff"], {"ocm": "vocm", "v(vdd)": "vdd"})


def fd_supply_step(ctx: Context) -> dict:
    ripple = ctx.cond["supply_step_v"]
    metrics: dict[str, object] = {}
    curves = {}
    for k, freq in enumerate(ctx.cond["supply_step_frequencies"]):
        period = 1.0 / freq
        edge = ctx.cond["supply_step_edge_s"]
        tmax = min(period / 4000.0, max(first_stable_tau(ctx) / 5.0, period / 20000.0))
        tokens = {
            "VDD_LO": ctx.point.vdd - ripple, "VDD_HI": ctx.point.vdd + ripple, "RIPPLE": ripple,
            "FREQ": freq, "T_DELAY": period / 4.0, "T_EDGE": edge, "T_WIDTH": period / 2.0 - edge,
            "T_PERIOD": period, "TSTEP": tmax,
            "TSTOP": period / 4.0 + ctx.cond["supply_step_periods"] * period, "TMAX": tmax,
        }
        tran = ctx.run("tb_supply_step.spi", f"fd26_{k}", **tokens).plot("Transient")
        label = thd_label(freq)
        for probe, name in (("ocm", "cm"), ("diff", "dm")):
            res = supply_step_analysis(ctx, tran, freq, probe)
            metrics[f"supply_step_{label}_{name}_peak_v"] = res["peak_v"]
            metrics[f"supply_step_{label}_{name}_settle_s"] = res["settle_s"]
            metrics[f"supply_step_{label}_{name}_oscillation"] = res["oscillation"]
        if ctx.detail:
            curves[f"vocm_{label}"] = curve(tran.scale.real, tran["ocm"])
            settle = metrics[f"supply_step_{label}_cm_settle_s"]
            span = edge_span(settle, 40.0 * first_stable_tau(ctx), period / 2.0)
            curves[f"vocm_{label}_edge"] = edge_curve(tran.scale.real, tran["ocm"], period / 4.0 + period, span)
    result = {"metrics": metrics}
    if ctx.detail:
        result["curves"] = curves
    return result


def skewed_dut(ctx: Context) -> tuple[Path, str] | None:
    """The DUT netlist with the vinp-side input transistor's W raised by
    ``fd_input_skew`` (spec 3.1.1.1), and that device's name."""
    pins, inputs, _ = input_pair(ctx)
    vinp = pins[INPUT_PINS["fd"][0]]
    target = next((dev for dev in inputs if dev["g"] == vinp), None)
    if target is None:
        return None
    skew = ctx.cond["fd_input_skew"]
    text = Path(ctx.dut_file).read_text()
    line = target["line"]
    width = float(re.search(r"\bw=([0-9.eE+-]+)", line, re.I).group(1))
    skewed_line = re.sub(r"\bw=[0-9.eE+-]+", f"w={width * (1.0 + skew):.6g}", line, count=1, flags=re.I)
    ctx.work.mkdir(parents=True, exist_ok=True)
    path = ctx.work / "skewed_input_pair.spi"
    path.write_text(text.replace(line, skewed_line, 1))
    return path, target["name"]


def fd_skew_offset(ctx: Context) -> dict:
    """Spec 3.1.1.1: a symmetric FD amplifier has no systematic offset; to see a
    random one, deliberately skew the input pair's W/L and rerun Fig. 26."""
    skewed = skewed_dut(ctx)
    if skewed is None:
        return {"metrics": {"vos_in_skew_v": None}}
    path, name = skewed
    skew = ctx.cond["fd_input_skew"]
    op = ctx.run("tb_offset_op.spi", "fd27", DUT_FILE=str(path)).plot("Operating")
    vod, vocm = diff_cm(op)
    return {"metrics": {
        "input_w_skew_pct": 100.0 * skew,
        "vos_out_skew_v": float(vod[0]),
        "vos_in_skew_v": float(vod[0]) / 2.0,
        "skewed_device": name,
    }}


def fd_op_icmr(ctx: Context) -> dict:
    return op_icmr(ctx, "fd28")


def fd_supply_dc(ctx: Context) -> dict:
    return supply_dc(ctx, "fd29", differential=True)


FD_TESTS: tuple[Test, ...] = (
    Test("FD-01", "Offset, output CM, supply current, power, bias-pin voltage", "3.1.1.1 Fig.26; Table 1 ICC/VOS/PD/VBI", fd_offset_op),
    Test("FD-07", "Open-loop gain, GBW, phase/gain margin, RID, RO", "3.1.3.1 Fig.29; Table 1 AVO/GBW/PM/RID/RO", fd_openloop_ac),
    Test("FD-02", "Offset and output-CM temperature drift", "3.2.1.2 (FD network)", fd_offset_tempco, pvt=False),
    Test("FD-03", "Closed-loop common-mode range (Fig. 27)", "3.1.2 Fig.27", fd_icmr_closed_loop),
    Test("FD-04", "Input common-mode range (gain -6 dB)", "3.1.2; Table 1 ICMR", fd_icmr_gain_sweep),
    Test("FD-05", "Output swing (DC)", "3.1.2.1 Fig.28; Table 1 VOPP", fd_swing_dc),
    Test("FD-06", "Output swing (TRAN, THD limit)", "3.1.2.1 TRAN check", fd_swing_tran, pvt=False, transient=True),
    Test("FD-08", "Closed-loop response, 0.1 dB flatness, group delay", "3.2.2.2, 3.2.2.7 (FD network)", fd_closedloop_ac),
    Test("FD-09", "Common-mode rejection (CM->CM, CM->DM)", "3.1.3.2 Fig.30; Table 1 CMRR", fd_cmrr),
    Test("FD-10", "Supply rejection (supply->CM, supply->DM)", "3.1.3.3 Fig.31; Table 1 PSRR", fd_psrr),
    Test("FD-11", "Closed-loop output impedance (DM and CM)", "3.2.2.5 (FD network)", fd_zout),
    Test("FD-12", "Noise", "3.2.2.6 (FD, capacitive gain -1)", fd_noise),
    Test("FD-13", "Small-signal step response", "3.1.4.1 Fig.32", fd_step_small, transient=True),
    Test("FD-14", "Large-signal step: slew rate and settling", "3.1.4.1 Fig.32; Table 1 SR/TS", fd_step_large, transient=True),
    Test("FD-15", "Total harmonic distortion", "3.2.3.2 (FD network); Table 1 THD", fd_thd, transient=True),
    Test("FD-16", "Full-power bandwidth", "3.2.3.2; Table 1 BWfull", fd_fpbw, pvt=False, transient=True),
    Test("FD-17", "Transconductance, linear range, gm/ICC, IOPP", "OTA 3.2.1.1 Fig.34; Table 1 gm/VID/IOPP", fd_gm_dc),
    Test("FD-18", "Transconductance frequency response", "OTA 3.2.2.1 (Fig.35, FD)", fd_gm_ac),
    Test("FD-19", "CMFB transient (input common-mode step)", "3.1.2 TRAN check", fd_cm_step, transient=True),
    Test("FD-25", "Power-up / power-down stability (5 and 10 ms ramps)", "3.3.4.1", fd_power_cycle, pvt=False, transient=True),
    Test("FD-26", "Supply surge stability", "3.3.4.2", fd_supply_step, pvt=False, transient=True),
    Test("FD-27", "Offset with a deliberately skewed input pair", "3.1.1.1 (skewed W/L)", fd_skew_offset),
    Test("FD-28", "Input common-mode range from the operating point", "3.2.1.3 (OP calculation)", fd_op_icmr),
    Test("FD-29", "Operating supply range, DC sweep", "3.3.2.1 (DC sweep)", fd_supply_dc, pvt=False),
    Test("FD-30", "Supply current against the bias current", "Bias pin contract", fd_bias_tracking, pvt=False),
)


# ======================================================================
# signoff run
# ======================================================================

"""Run the benches on one design and collect the metrics."""








def tests_for(klass: str) -> tuple[S.Test, ...]:
    return S.SE_TESTS if klass == "se" else S.FD_TESTS


def run_tests(job: dict) -> dict:
    """Run ``job['tests']`` in order at one point; the context cache carries
    results (GBW, phase margin, slew rate) from earlier tests to later ones.
    Transient tests are skipped where ``suite.transient_gate`` says the loop
    oscillates."""
    point = Point(*job["point"])
    ctx = Context(job["klass"], job["dut_file"], job["dut"], job["cond"], point,
                  Path(job["work"]), job["detail"], job.get("seed"), dict(job.get("cache", {})),
                  dict(job.get("overrides", {})), job.get("nominal", False))
    by_id = {test.id: test for test in tests_for(job["klass"])}
    results = {}
    for test_id in job["tests"]:
        started = time.time()
        skipped = S.transient_gate(ctx.cache) if by_id[test_id].transient else None
        if skipped:
            res = {"skipped": skipped}
        else:
            try:
                res = by_id[test_id].func(ctx)
            except Exception as exc:  # recorded per test so one failure never hides the rest
                res = {"error": f"{type(exc).__name__}: {exc}"[-3000:]}
        res["elapsed_s"] = round(time.time() - started, 3)
        results[test_id] = res
    if not job.get("keep_work"):
        shutil.rmtree(job["work"], ignore_errors=True)
    return {"point": job["point"], "seed": job.get("seed"), "label": job.get("label"),
            "results": results, "cache": ctx.cache,
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


def thin(curves: dict, limit: int) -> dict:
    """Keep at most ``limit`` points per curve (index-uniform, so the dense
    regions the simulator chose, such as edges, stay dense)."""
    for test in curves.values():
        for data in test.values():
            count = len(data["x"])
            if count > limit:
                keep = np.unique(np.linspace(0, count - 1, limit).round().astype(int))
                data["x"] = [data["x"][i] for i in keep]
                data["y"] = [data["y"][i] for i in keep]
    return curves


def summarize_pvt(points: list[dict]) -> dict:
    table: dict[str, dict[str, list]] = {}
    for entry in points:
        tag = entry.get("label") or Point(*entry["point"]).tag
        for test_id, res in entry["results"].items():
            for name, value in res.get("metrics", {}).items():
                table.setdefault(test_id, {}).setdefault(name, []).append((tag, value))
    summary: dict[str, dict] = {}
    for test_id, metrics in table.items():
        for name, values in metrics.items():
            numeric = [(tag, v) for tag, v in values
                       if isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)]
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


def summarize_mc(rows: list[dict]) -> dict:
    """Mean, sigma, min and max of every numeric metric over Monte Carlo runs."""
    table: dict[str, dict[str, list[float]]] = {}
    for row in rows:
        for test_id, res in row["results"].items():
            for name, value in res.get("metrics", {}).items():
                if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value):
                    table.setdefault(test_id, {}).setdefault(name, []).append(float(value))
    out: dict[str, dict] = {}
    for test_id, metrics in table.items():
        for name, values in metrics.items():
            arr = np.array(values)
            out.setdefault(test_id, {})[name] = {
                "count": int(len(arr)), "mean": float(arr.mean()),
                "sigma": float(arr.std(ddof=1)) if len(arr) > 1 else 0.0,
                "min": float(arr.min()), "max": float(arr.max()),
            }
    return out


def operating_range(rows: list[dict], key: str, nominal: dict, cond: dict) -> dict:
    def ok(row: dict) -> bool:
        m = row["metrics"]
        if not all(isinstance(m.get(k), (int, float)) for k in ("avo_db", "gbw_hz", "pm_deg")):
            return False
        return (m["avo_db"] >= cond["operating_avo_min_db"]
                and m["gbw_hz"] >= cond["operating_gbw_min_hz"]
                and m["pm_deg"] >= cond["operating_pm_min_deg"])

    rows = sorted(rows, key=lambda row: row[key])
    flags = [ok(row) for row in rows]
    values = [row[key] for row in rows]
    ref = min(range(len(values)), key=lambda i: abs(values[i] - nominal[key]))
    lo = hi = ref
    if not flags[ref]:
        return {"low": None, "high": None, "rows": rows}
    while lo > 0 and flags[lo - 1]:
        lo -= 1
    while hi < len(rows) - 1 and flags[hi + 1]:
        hi += 1
    return {
        "low": values[lo], "high": values[hi],
        "low_is_sweep_limit": lo == 0, "high_is_sweep_limit": hi == len(rows) - 1,
        "rows": rows,
    }


# Phases of a characterization run.  The CLI runs the full plan; the Harbor
# verifier runs SIGNOFF_PLAN.
FULL_PLAN = {
    "nominal_detail": True,          # every nominal test with its detail sweeps
    "nominal_tests": None,           # None: every test at the nominal point; else these IDs
    "pvt": True,                     # PVT-flagged tests at the PVT points
    "pvt_tests": None,               # None: every PVT-flagged test; else these IDs
    "pvt_set": "full",               # "full": the whole matrix; "vertices": see pvt_points
    "temperature_extremes": "all",   # "all" non-PVT tests, "stability" ones, or None
    "load_cases": True,              # PVT matrix with the critical loads (OP + open-loop AC)
    "offset_mc_runs": None,          # None: conditions [mc] runs
    "process_mc_runs": None,         # None: conditions [mc] process_runs
    "process_mc_tests": "pvt",       # "pvt": every PVT test; "core": OP + open-loop AC
    "load_tests": "op_ac",           # load cases run "op_ac" (OP + open-loop AC) or "ac" only
    "range_tests": "op_ac",          # operating-range sweeps, same choice
    "supply_sweep": None,            # None: conditions supply_sweep
    "temp_range_sweep": None,        # None: conditions temp_range_sweep
}
STABILITY_TESTS = ("SE-25", "SE-26", "FD-25", "FD-26")


def pvt_points(cond: dict, plan: dict) -> list[tuple[str, float, float]]:
    """The PVT points of a run.  "vertices" keeps the skew corners (every corner
    but the typical one) at the lowest and highest supply and temperature: on
    the six references these points hold every scored worst case of the full
    matrix to within 0.2 dB and 0.4 %."""
    pvt = cond["pvt"]
    corners, supplies, temps = pvt["corners"], pvt["supplies"], pvt["temperatures"]
    if plan["pvt_set"] == "vertices":
        corners = [c for c in corners if c != "tt"]
        supplies = [min(supplies), max(supplies)]
        temps = [min(temps), max(temps)]
    return [(corner, vdd, temp) for corner in corners for vdd in supplies for temp in temps]


def characterize(klass: str, dut: str, dut_file: str, cond: dict, work: Path, jobs: int,
                 plan: dict | None = None, only: list[str] | None = None, keep_work: bool = False,
                 task_id: str = "") -> tuple[dict, dict]:
    """Run the mandatory test list on one design; returns (metrics, curves)."""
    plan = {**FULL_PLAN, **(plan or {})}
    tests = tests_for(klass)
    nominal_point = ("tt", cond["vdd"], cond["temperature"])
    started = time.time()
    mc = cond["mc"]
    offset_runs = mc["runs"] if plan["offset_mc_runs"] is None else plan["offset_mc_runs"]
    process_runs = mc["process_runs"] if plan["process_mc_runs"] is None else plan["process_mc_runs"]

    def job(point, test_ids, detail, tag, seed=None, cache=None, overrides=None, label=None):
        return {"klass": klass, "dut_file": dut_file, "dut": dut, "cond": cond, "point": point,
                "work": str(work / tag), "detail": detail, "tests": test_ids, "seed": seed,
                "cache": cache or {}, "overrides": overrides or {}, "label": label, "keep_work": keep_work,
                "nominal": point == nominal_point and seed is None and not overrides}

    first = [t.id for t in tests[:2]]  # offset/OP and open-loop AC feed later tests
    rest = [t.id for t in tests[2:]]
    if plan["nominal_tests"] is not None:
        rest = [t for t in rest if t in plan["nominal_tests"]]
    if only:
        rest = [t for t in rest if t in only]
    head = run_tests(job(nominal_point, first, plan["nominal_detail"], "nominal"))
    cache = head["cache"]
    # The full-power bandwidth needs the slew rate, so it runs after the large step.
    chained = {"SE-18": "SE-16", "FD-16": "FD-14"}
    groups: list[list[str]] = []
    for test_id in rest:
        if test_id in chained and chained[test_id] in rest:
            continue
        group = [test_id]
        group += [t for t, parent in chained.items() if parent == test_id and t in rest]
        groups.append(group)
    points = pvt_points(cond, plan)

    with ProcessPoolExecutor(max_workers=jobs) as pool:
        # Long nominal chains first so they overlap the many short PVT jobs.
        nominal_futures = [pool.submit(run_tests, job(nominal_point, g, plan["nominal_detail"],
                                                      f"nominal/{g[0]}", cache=cache))
                           for g in groups]
        pvt_futures, pvt_ids = [], []
        if plan["pvt"]:
            pvt_ids = [t.id for t in tests if t.pvt and (plan["pvt_tests"] is None or t.id in plan["pvt_tests"])]
            for point in points:
                pvt_futures.append(pool.submit(run_tests, job(point, pvt_ids, False, f"pvt/{Point(*point).tag}")))
        mc_futures = []
        for k in range(offset_runs):
            point = (mc["corner"], cond["vdd"], cond["temperature"])
            mc_futures.append(pool.submit(
                run_tests, job(point, [tests[0].id], False, f"mc/{k:03d}", seed=mc["seed"] + k)))
        extra_futures = {"temperature": [], "load": [], "process": []}
        if plan["temperature_extremes"]:
            # Spec 3.3.1.2: the remaining tests at the lowest and highest temperature.
            if plan["temperature_extremes"] == "stability":
                extra_ids = [t.id for t in tests if t.id in STABILITY_TESTS]
            else:
                nominal_only = [t.id for t in tests
                                if not t.pvt and t.id not in ("SE-02", "FD-02", "SE-29", "FD-29")]
                needs = [tid for tid in ("SE-16", "FD-14") if tid in {t.id for t in tests}]
                extra_ids = needs + nominal_only
            for temp in (min(cond["pvt"]["temperatures"]), max(cond["pvt"]["temperatures"])):
                point = ("tt", cond["vdd"], temp)
                extra_futures["temperature"].append(pool.submit(
                    run_tests, job(point, first + extra_ids, False, f"temp_ext/{Point(*point).tag}")))
        if plan["load_cases"]:
            # Spec 3.3.3: the PVT matrix again under the critical load conditions.
            load_ids = first if plan["load_tests"] == "op_ac" else first[1:]
            current = cond["pvt"]["load_current"]
            variants = {"+i": {"IOUT_DC": current}, "-i": {"IOUT_DC": -current},
                        "rl_to_ground": {"RL_NODE": "0"}}
            for point in points:
                for case in cond["pvt"]["worst_case_loads"]:
                    tag = f"{Point(*point).tag}_{case.replace('+', 'p').replace('-', 'm')}"
                    extra_futures["load"].append(pool.submit(
                        run_tests, job(point, load_ids, False, f"load/{tag}",
                                       overrides=variants[case], label=f"{Point(*point).tag} {case}")))
        # Spec 3.3.1.1: process tolerance on the PVT-matrix tests.
        if plan["process_mc_tests"] == "core":
            process_ids = list(first)
        else:
            process_ids = [t.id for t in tests if t.pvt]
        for k in range(process_runs):
            point = (mc["process_corner"], cond["vdd"], cond["temperature"])
            extra_futures["process"].append(pool.submit(
                run_tests, job(point, process_ids, False, f"process_mc/{k:03d}", seed=mc["process_seed"] + k,
                               overrides={"MC_PARAM": mc["process_param"]}, label=f"seed {mc['process_seed'] + k}")))
        range_futures = []
        sweeps = {"vdd": plan["supply_sweep"] or cond["supply_sweep"],
                  "temp": plan["temp_range_sweep"] or cond["temp_range_sweep"]}
        if sweeps["vdd"] and sweeps["temp"]:
            range_ids = first if plan["range_tests"] == "op_ac" else first[1:]
            for vdd in S.float_range(*sweeps["vdd"]):
                range_futures.append(("vdd", vdd, pool.submit(
                    run_tests, job(("tt", vdd, cond["temperature"]), range_ids, False, f"vdd/{vdd:.2f}"))))
            for temp in S.float_range(*sweeps["temp"]):
                range_futures.append(("temp", temp, pool.submit(
                    run_tests, job(("tt", cond["vdd"], temp), range_ids, False, f"temp/{temp:g}"))))

        nominal = dict(head["results"])
        for future in nominal_futures:
            nominal.update(future.result()["results"])
        pvt = [future.result() for future in pvt_futures]
        mc_rows = [future.result() for future in mc_futures]
        range_rows = [(kind, value, future.result()) for kind, value, future in range_futures]
        temp_rows = [future.result() for future in extra_futures["temperature"]]
        load_rows = [future.result() for future in extra_futures["load"]]
        process_rows = [future.result() for future in extra_futures["process"]]

    order = [t.id for t in tests]
    nominal = {test_id: nominal[test_id] for test_id in order if test_id in nominal}
    curves = {test_id: res.pop("curves") for test_id, res in nominal.items() if "curves" in res}

    result: dict[str, object] = {
        "task_id": task_id,
        "class": klass,
        "subckt": dut,
        "conditions": cond,
        "tests": [{"id": t.id, "title": t.title, "spec": t.spec, "pvt": t.pvt} for t in tests],
        "planned": {
            "nominal_tests": first + rest,
            "pvt_tests": pvt_ids if plan["pvt"] else [],
            "pvt_points": len(points) if plan["pvt"] else 0,
            "load_points": len(extra_futures["load"]),
        },
        "nominal_point": Point(*nominal_point).tag,
        "nominal": nominal,
    }
    if pvt:
        result["pvt"] = {
            "points": [Point(*entry["point"]).tag for entry in pvt],
            "summary": summarize_pvt(pvt),
            "errors": {Point(*entry["point"]).tag: {tid: res["error"] for tid, res in entry["results"].items()
                                                    if "error" in res}
                       for entry in pvt if any("error" in res for res in entry["results"].values())},
            "values": {Point(*entry["point"]).tag: {tid: res.get("metrics", {})
                                                    for tid, res in entry["results"].items()}
                       for entry in pvt},
        }
    if mc_rows:
        first_id = tests[0].id
        key = "vos_v" if klass == "se" else "vos_in_v"
        values = np.array([row["results"][first_id]["metrics"][key] for row in mc_rows
                           if "metrics" in row["results"][first_id]])
        result["monte_carlo"] = {
            "corner": mc["corner"], "runs": int(len(values)), "requested_runs": offset_runs,
            "seed_first": mc["seed"], "metric": key,
            "mean": float(values.mean()) if len(values) else None,
            "sigma": float(values.std(ddof=1)) if len(values) > 1 else None,
            "abs_max": float(np.abs(values).max()) if len(values) else None,
        }
    if temp_rows:
        result["temperature_extremes"] = {
            Point(*row["point"]).tag: {tid: {"skipped": res["skipped"]} if "skipped" in res
                                       else res.get("metrics", res.get("error"))
                                       for tid, res in row["results"].items()}
            for row in temp_rows}
    if load_rows:
        result["worst_case_load"] = {
            "load_current_a": cond["pvt"]["load_current"],
            "loads": cond["pvt"]["worst_case_loads"],
            "points": len(load_rows),
            "summary": summarize_pvt(load_rows),
            "errors": {row["label"]: {tid: res["error"] for tid, res in row["results"].items() if "error" in res}
                       for row in load_rows if any("error" in res for res in row["results"].values())},
        }
    if process_rows:
        result["process_mc"] = {
            "corner": mc["process_corner"], "param": mc["process_param"],
            "runs": len(process_rows), "seed_first": mc["process_seed"], "tests": process_ids,
            "summary": summarize_mc(process_rows),
            "errors": {row["label"]: {tid: res["error"][:200] for tid, res in row["results"].items() if "error" in res}
                       for row in process_rows if any("error" in res for res in row["results"].values())},
        }
    if range_rows:
        nominal_ac = nominal[first[1]].get("metrics", {})
        ref = {"avo_db": nominal_ac.get("avo_db"), "gbw_hz": nominal_ac.get("gbw_hz"),
               "vdd": cond["vdd"], "temp": cond["temperature"]}
        ranges = {}
        if isinstance(ref["avo_db"], (int, float)) and isinstance(ref["gbw_hz"], (int, float)):
            for kind in ("vdd", "temp"):
                rows = [{kind: value, "metrics": res["results"][first[1]].get("metrics", {})}
                        for k, value, res in range_rows if k == kind]
                ranges[kind] = operating_range(rows, kind, ref, cond)
        result["operating_range"] = ranges
    # Points where the transient tests were skipped, with the reason.
    gate = {}
    for where, results in ([("nominal", nominal)]
                           + [(f"pvt/{Point(*row['point']).tag}", row["results"]) for row in pvt]
                           + [(f"temperature_extremes/{Point(*row['point']).tag}", row["results"])
                              for row in temp_rows]):
        reasons = {res["skipped"] for res in results.values() if "skipped" in res}
        if reasons:
            gate[where] = sorted(reasons)[0]
    if gate:
        result["transient_gate"] = gate
    # Simulation cost per phase (sum of job times, i.e. CPU-seconds), and the
    # PVT cost per test, so runtime budgets can be argued from data.
    pvt_by_test: dict[str, float] = {}
    for entry in pvt:
        for tid, res in entry["results"].items():
            pvt_by_test[tid] = pvt_by_test.get(tid, 0.0) + res.get("elapsed_s", 0.0)
    nominal_by_test = {tid: res.get("elapsed_s", 0.0) for tid, res in nominal.items()}
    result["cost_s"] = {
        "nominal": round(sum(nominal_by_test.values()), 1),
        "pvt": round(sum(entry["elapsed_s"] for entry in pvt), 1),
        "load_cases": round(sum(row["elapsed_s"] for row in load_rows), 1),
        "temperature_extremes": round(sum(row["elapsed_s"] for row in temp_rows), 1),
        "offset_mc": round(sum(row["elapsed_s"] for row in mc_rows), 1),
        "process_mc": round(sum(row["elapsed_s"] for row in process_rows), 1),
        "ranges": round(sum(res["elapsed_s"] for _, _, res in range_rows), 1),
        "nominal_by_test": {k: round(v, 1) for k, v in sorted(nominal_by_test.items(), key=lambda kv: -kv[1])},
        "pvt_by_test": {k: round(v, 1) for k, v in sorted(pvt_by_test.items(), key=lambda kv: -kv[1])},
    }
    result["elapsed_s"] = round(time.time() - started, 1)
    return result, curves


# ======================================================================
# scoring
# ======================================================================

"""Acceptance checks of the v4 OTA bench Harbor tasks.

``score(result, spec)`` turns one characterization result (the dictionary that
``run_suite.characterize`` returns, also stored as results/metrics.json) into
named pass/fail checks.  The Harbor verifier embeds this module; the
characterization flow uses it to re-score stored results.
"""



# Test IDs of the same measurement in the two classes.
IDS = {
    "se": {"op": "SE-01", "ol": "SE-08", "icmr": "SE-04", "swing": "SE-06", "cl": "SE-09", "cmrr": "SE-10",
           "psrr": "SE-11", "noise": "SE-13", "step": "SE-16", "thd": "SE-17", "fpbw": "SE-18", "gm": "SE-19",
           "power": "SE-25", "surge": "SE-26", "track": "SE-30"},
    "fd": {"op": "FD-01", "ol": "FD-07", "icmr": "FD-04", "swing": "FD-05", "cl": "FD-08", "cmrr": "FD-09",
           "psrr": "FD-10", "noise": "FD-12", "step": "FD-14", "thd": "FD-15", "fpbw": "FD-16", "gm": "FD-17",
           "power": "FD-25", "surge": "FD-26", "track": "FD-30"},
}
# Tests the signoff runs at the nominal point and at the PVT points: the ones
# the checks below read (the open-loop and offset tests always run first).
SIGNOFF_TESTS = {
    "se": {"nominal": ("SE-04", "SE-06", "SE-09", "SE-10", "SE-11", "SE-13", "SE-16", "SE-17", "SE-18",
                       "SE-19", "SE-25", "SE-26", "SE-30"),
           "pvt": ("SE-01", "SE-08", "SE-04", "SE-06", "SE-10", "SE-11", "SE-13", "SE-16", "SE-17")},
    "fd": {"nominal": ("FD-04", "FD-05", "FD-08", "FD-09", "FD-10", "FD-12", "FD-14", "FD-15", "FD-16",
                       "FD-17", "FD-25", "FD-26", "FD-30"),
           "pvt": ("FD-01", "FD-07", "FD-04", "FD-05", "FD-09", "FD-10", "FD-12", "FD-14", "FD-15")},
}
# Tests that locate the input pair by parsing the submitted netlist (the
# operating-point ICMR estimate and the deliberate 1 % W skew).  A legal
# netlist they cannot parse (for example one built from nested subcircuits)
# leaves them unreported; that does not make the signoff incomplete.
STRUCTURAL = ("SE-28", "FD-28", "FD-27")


def signoff_tests(klass: str, spec: dict) -> dict[str, list[str]]:
    """Nominal and PVT test IDs of the signoff; the transconductance test joins
    the PVT set when its gm is scored (the OTAs)."""
    tests = {key: list(ids) for key, ids in SIGNOFF_TESTS[klass].items()}
    if "gm_min_s" in spec:
        tests["pvt"].append(IDS[klass]["gm"])
    return tests


def number(value) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value) if math.isfinite(value) else None


def fmt(value: float | None, scale: float = 1.0, digits: int = 4) -> str:
    return "missing" if value is None else f"{value * scale:.{digits}g}"


class View:
    """Read-only accessors over one characterization result."""

    def __init__(self, result: dict):
        self.r = result
        self.klass = result["class"]
        self.ids = IDS[self.klass]
        self.planned = result.get("planned", {})

    def nominal(self, test: str, key: str):
        return number(self.r.get("nominal", {}).get(self.ids.get(test, test), {}).get("metrics", {}).get(key))

    def nominal_raw(self, test: str, key: str):
        return self.r.get("nominal", {}).get(self.ids.get(test, test), {}).get("metrics", {}).get(key)

    def pvt(self, test: str, key: str) -> list[float | None]:
        values = self.r.get("pvt", {}).get("values", {})
        test_id = self.ids.get(test, test)
        return [number(values[tag].get(test_id, {}).get(key)) for tag in self.r.get("pvt", {}).get("points", [])]

    def pvt_points(self) -> list[str]:
        return list(self.r.get("pvt", {}).get("points", []))

    def load_summary(self, test: str, key: str) -> dict:
        return self.r.get("worst_case_load", {}).get("summary", {}).get(self.ids.get(test, test), {}).get(key, {})

    def everywhere(self, test: str, key: str) -> list[float | None]:
        """Values at the PVT points and the nominal point."""
        values = self.pvt(test, key)
        return values + [self.nominal(test, key)] if values else []

    def worst(self, test: str, key: str, lowest: bool, with_load: bool = False) -> float | None:
        """Worst value over the PVT points and the nominal point."""
        values = self.everywhere(test, key)
        if not values or any(v is None for v in values):
            return None
        pick = min if lowest else max
        worst = pick(values)
        if with_load:
            entry = self.load_summary(test, key)
            extreme = number(entry.get("min" if lowest else "max"))
            if extreme is None or entry.get("missing_at") or entry.get("count", 0) != self.planned.get("load_points"):
                return None
            worst = pick(worst, extreme)
        return worst


def vdd_of(tag: str) -> float:
    return float(tag.split("_")[1].rstrip("v").replace("p", "."))


def completeness(view: View, plan: dict) -> tuple[bool, str]:
    r = view.r
    problems = []
    nominal = r.get("nominal", {})
    planned = r.get("planned", {})
    expected = planned.get("nominal_tests") or [t["id"] for t in r.get("tests", [])]
    errors = [tid for tid in expected
              if tid not in STRUCTURAL and "error" in nominal.get(tid, {"error": "missing"})]
    if errors:
        problems.append(f"nominal errors in {','.join(errors)}")
    pvt = r.get("pvt", {})
    pvt_errors = {tag: [tid for tid in tests if tid not in STRUCTURAL]
                  for tag, tests in pvt.get("errors", {}).items()}
    pvt_errors = {tag: tids for tag, tids in pvt_errors.items() if tids}
    pvt_count, load_count = planned.get("pvt_points", 0), planned.get("load_points", 0)
    if not pvt_count or len(pvt.get("points", [])) != pvt_count or pvt_errors:
        problems.append(f"PVT {len(pvt.get('points', []))}/{pvt_count} points, {len(pvt_errors)} with errors")
    load = r.get("worst_case_load", {})
    if not load_count or load.get("points", 0) != load_count or load.get("errors"):
        problems.append(f"load cases {load.get('points', 0)}/{load_count}, {len(load.get('errors', {}))} with errors")
    mc = r.get("monte_carlo", {})
    if mc.get("runs") != plan["offset_mc_runs"]:
        problems.append(f"offset MC {mc.get('runs')}/{plan['offset_mc_runs']} runs")
    process = r.get("process_mc", {})
    if plan["process_mc_runs"] and (process.get("runs") != plan["process_mc_runs"] or process.get("errors")):
        problems.append(f"process MC {process.get('runs')}/{plan['process_mc_runs']} runs, "
                        f"{len(process.get('errors', {}))} with errors")
    extremes = r.get("temperature_extremes", {})
    bad = [tag for tag, tests in extremes.items() if any(not isinstance(res, dict) for res in tests.values())]
    if len(extremes) != 2 or bad:
        problems.append(f"temperature extremes {len(extremes)}/2, errors at {','.join(bad) or '-'}")
    ranges = r.get("operating_range", {})
    if set(ranges) != {"vdd", "temp"}:
        problems.append("operating-range sweeps missing")
    counted = [tid for tid in expected if tid not in STRUCTURAL]
    detail = (f"nominal {len(counted) - len(errors)}/{len(counted)} tests, PVT {len(pvt.get('points', []))}, "
              f"load {load.get('points', 0)}, offset MC {mc.get('runs')}, "
              + (f"process MC {process.get('runs')}, " if plan["process_mc_runs"] else "")
              + f"temperature extremes {len(extremes)}")
    return not problems, detail + ("; " + "; ".join(problems) if problems else "")


def score(result: dict, spec: dict, plan: dict) -> tuple[list[tuple[str, bool, str]], dict]:
    """Checks (name, passed, message) and the scored measurements."""
    v = View(result)
    se = v.klass == "se"
    s = spec
    checks: list[tuple[str, bool, str]] = []
    meas: dict[str, float | None] = {}

    def check(name: str, ok: bool, message: str) -> None:
        checks.append((name, bool(ok), message))

    # Runs whose transient tests were skipped (open-loop margins not positive).
    gate = result.get("transient_gate", {})
    gate_note = (f"; transient tests not run at {len(gate)} of the runs because the open-loop phase or "
                 f"gain margin there is not positive ({', '.join(sorted(gate)[:4])}"
                 f"{', ...' if len(gate) > 4 else ''})" if gate else "")

    def le(value, limit):
        return value is not None and value <= limit

    def ge(value, limit):
        return value is not None and value >= limit

    ok, message = completeness(v, plan)
    check("complete_signoff", ok, message)

    avo = meas["avo_min_db"] = v.worst("ol", "avo_db", True, with_load=True)
    check("pvt_open_loop_gain", ge(avo, s["avo_min_db"]),
          f"worst AVO {fmt(avo)} dB over PVT + load cases (limit {s['avo_min_db']:g} dB)")

    gbw = meas["gbw_min_hz"] = v.worst("ol", "gbw_hz", True, with_load=True)
    check("pvt_gain_bandwidth", ge(gbw, s["gbw_min_hz"]),
          f"worst GBW {fmt(gbw, 1e-6)} MHz (limit {s['gbw_min_hz'] / 1e6:g} MHz)")

    pm = meas["pm_min_deg"] = v.worst("ol", "pm_deg", True, with_load=True)
    gm = meas["gain_margin_min_db"] = v.worst("ol", "gain_margin_db", True, with_load=True)
    crossings = v.worst("ol", "unity_crossings", False, with_load=True)
    check("pvt_loop_stability", ge(pm, s["pm_min_deg"]) and ge(gm, s["gm_min_db"]) and crossings == 1,
          f"worst PM {fmt(pm)} deg, worst gain margin {fmt(gm)} dB, unity crossings max {fmt(crossings)} "
          f"(limits {s['pm_min_deg']:g} deg, {s['gm_min_db']:g} dB, 1)")

    peaking = meas["closed_loop_peaking_db"] = v.nominal("cl", "cl_peaking_db")
    check("closed_loop_flatness", le(peaking, s["peaking_max_db"]),
          f"closed-loop peaking {fmt(peaking)} dB (limit {s['peaking_max_db']:g} dB)")

    rise, fall = v.worst("step", "sr_rise_v_per_s", True), v.worst("step", "sr_fall_v_per_s", True)
    sr = meas["slew_rate_min_v_per_s"] = None if rise is None or fall is None else min(rise, fall)
    check("pvt_slew_rate", ge(sr, s["sr_min_v_per_s"]),
          f"worst SR {fmt(sr, 1e-6)} V/us (limit {s['sr_min_v_per_s'] / 1e6:g} V/us)" + gate_note)

    t_rise = v.worst("step", "large_settle_rise_0p1pct_s", False)
    t_fall = v.worst("step", "large_settle_fall_0p1pct_s", False)
    ts = meas["settling_max_s"] = None if t_rise is None or t_fall is None else max(t_rise, t_fall)
    check("pvt_settling", le(ts, s["settle_max_s"]),
          f"worst 0.1 % large-step settling {fmt(ts, 1e9)} ns (limit {s['settle_max_s'] * 1e9:g} ns)"
          + gate_note)

    lo, hi = v.nominal("icmr", "icmr_lo_v"), v.nominal("icmr", "icmr_hi_v")
    want_lo, want_hi = s["icmr_nominal_v"]
    half = s["icmr_pvt_half_window_v"]
    # Every PVT point must cover its own VCM (VDD/2) +/- half.
    margins = []
    for tag, plo, phi in zip(v.pvt_points(), v.pvt("icmr", "icmr_lo_v"), v.pvt("icmr", "icmr_hi_v")):
        vcm_pt = CONDITIONS["vcm_fraction"] * vdd_of(tag)
        margins.append(None if plo is None or phi is None else min(vcm_pt - half - plo, phi - vcm_pt - half))
    pvt_margin = None if not margins or None in margins else min(margins)
    meas.update(icmr_nominal_lo_v=lo, icmr_nominal_hi_v=hi, icmr_pvt_margin_v=pvt_margin)
    check("input_common_mode_range",
          le(lo, want_lo) and ge(hi, want_hi) and ge(pvt_margin, 0.0),
          f"nominal {fmt(lo)}..{fmt(hi)} V (must cover {want_lo:g}..{want_hi:g} V); every PVT point covers "
          f"VCM +/- {half * 1e3:g} mV with {fmt(pvt_margin, 1e3)} mV to spare at worst")

    swing = meas["swing_min_v"] = v.worst("swing", "vopp_v" if se else "vodpp_v", True)
    check("pvt_output_swing", ge(swing, s["swing_min_v"]),
          f"worst {'VOPP' if se else 'VODpp'} {fmt(swing)} V (limit {s['swing_min_v']:g} V)")

    if se:
        cmrr = meas["cmrr_min_db"] = v.worst("cmrr", "cmrr_db", True)
        check("pvt_cmrr", ge(cmrr, s["cmrr_min_db"]),
              f"worst CMRR {fmt(cmrr)} dB (limit {s['cmrr_min_db']:g} dB)")
        plus, minus = v.worst("psrr", "psrr_plus_db", True), v.worst("psrr", "psrr_minus_db", True)
        psrr = meas["psrr_min_db"] = None if plus is None or minus is None else min(plus, minus)
        check("pvt_psrr", ge(psrr, s["psrr_min_db"]),
              f"worst PSRR+ {fmt(plus)} dB, PSRR- {fmt(minus)} dB (limit {s['psrr_min_db']:g} dB)")
    else:
        cm_gain = meas["cm_gain_max_db"] = v.worst("cmrr", "acm_cm_db", False)
        check("pvt_cm_rejection", le(cm_gain, s["cm_gain_max_db"]),
              f"worst input-CM to output-CM gain {fmt(cm_gain)} dB (limit {s['cm_gain_max_db']:g} dB)")
        plus, minus = v.worst("psrr", "psrr_plus_cm_db", True), v.worst("psrr", "psrr_minus_cm_db", True)
        psrr = meas["psrr_cm_min_db"] = None if plus is None or minus is None else min(plus, minus)
        check("pvt_cm_supply_rejection", ge(psrr, s["psrr_cm_min_db"]),
              f"worst VDD->CM {fmt(plus)} dB, VSS->CM {fmt(minus)} dB (limit {s['psrr_cm_min_db']:g} dB)")
        errors = v.everywhere("op", "vocm_error_v")
        cm_err = meas["cm_error_max_v"] = None if not errors or None in errors else max(abs(x) for x in errors)
        check("pvt_output_common_mode", le(cm_err, s["cm_error_max_v"]),
              f"worst |Vocm - VCM| {fmt(cm_err, 1e3)} mV (limit {s['cm_error_max_v'] * 1e3:g} mV)")

    en = meas["noise_white_max_v_rthz"] = v.worst("noise", "en_white_v_rthz", False)
    check("pvt_noise", le(en, s["en_max_v_rthz"]),
          f"worst white input noise {fmt(en, 1e9)} nV/rtHz (limit {s['en_max_v_rthz'] * 1e9:g} nV/rtHz)")

    thd = meas["thd_max_db"] = v.worst("thd", "thd_10khz_db", False)
    check("pvt_distortion", le(thd, s["thd_max_db"]),
          f"worst THD at 10 kHz {fmt(thd)} dB (limit {s['thd_max_db']:g} dB)" + gate_note)

    fpbw = meas["fpbw_hz"] = v.nominal("fpbw", "fpbw_hz")
    check("full_power_bandwidth", ge(fpbw, s["fpbw_min_hz"]),
          f"full-power bandwidth {fmt(fpbw, 1e-6)} MHz (limit {s['fpbw_min_hz'] / 1e6:g} MHz)" + gate_note)

    sigma = meas["offset_sigma_v"] = number(result.get("monte_carlo", {}).get("sigma"))
    offsets = v.everywhere("op", "vos_v" if se else "vos_in_v")
    vos = meas["vos_max_v"] = None if not offsets or None in offsets else max(abs(x) for x in offsets)
    check("offset", le(sigma, s["offset_sigma_max_v"]) and le(vos, s["vos_max_v"]),
          f"mismatch sigma {fmt(sigma, 1e3)} mV (limit {s['offset_sigma_max_v'] * 1e3:g} mV), "
          f"worst systematic |VOS| {fmt(vos, 1e3)} mV (limit {s['vos_max_v'] * 1e3:g} mV)")

    icc = meas["icc_max_a"] = v.worst("op", "icc_a", False)
    check("pvt_supply_current", le(icc, s["icc_max_a"]),
          f"worst supply current {fmt(icc, 1e6)} uA (limit {s['icc_max_a'] * 1e6:g} uA)")

    vbi = v.pvt("op", "vbi_v") + [v.nominal("op", "vbi_v")]
    vdds = [vdd_of(tag) for tag in v.pvt_points()] + [CONDITIONS["vdd"]]
    if v.pvt_points() and None not in vbi:
        excess = max(max(-x, x - vd) for x, vd in zip(vbi, vdds))
        lowest, highest = min(vbi), max(x - vd for x, vd in zip(vbi, vdds))
    else:
        excess = lowest = highest = None
    meas["ibias_pin_excess_v"] = excess
    check("pvt_bias_pin_compliance", excess is not None and excess <= 0.0,
          f"lowest V(ibias) {fmt(lowest)} V (must be >= 0 V), highest V(ibias) - V(vdd) {fmt(highest)} V "
          f"(must be <= 0 V) over every PVT point"
          if excess is not None else "V(ibias) missing at some operating point")

    if not se:
        ivcm = meas["vcm_pin_current_max_a"] = v.worst("op", "vcm_pin_current_a", False)
        check("pvt_vcm_pin_current", le(ivcm, s["vcm_current_max_a"]),
              f"worst current drawn from the vcm reference {fmt(ivcm, 1e9)} nA "
              f"(limit {s['vcm_current_max_a'] * 1e9:g} nA)")

    tracking = meas["icc_tracking"] = v.nominal("track", "icc_tracking")
    check("bias_current_tracking", ge(tracking, s["icc_tracking_min"]),
          f"supply current tracks the bias current with exponent {fmt(tracking)} (limit {s['icc_tracking_min']:g})")

    ranges = result.get("operating_range", {})
    vdd_lo, vdd_hi = (number(ranges.get("vdd", {}).get(k)) for k in ("low", "high"))
    t_lo, t_hi = (number(ranges.get("temp", {}).get(k)) for k in ("low", "high"))
    meas.update(supply_range_low_v=vdd_lo, supply_range_high_v=vdd_hi, temp_range_low_c=t_lo, temp_range_high_c=t_hi)
    want_v, want_t = s["supply_range_v"], s["temp_range_c"]
    check("operating_range",
          le(vdd_lo, want_v[0]) and ge(vdd_hi, want_v[1]) and le(t_lo, want_t[0]) and ge(t_hi, want_t[1]),
          f"supply {fmt(vdd_lo)}..{fmt(vdd_hi)} V (must cover {want_v[0]:g}..{want_v[1]:g} V), "
          f"temperature {fmt(t_lo)}..{fmt(t_hi)} C (must cover {want_t[0]:g}..{want_t[1]:g} C)")

    flags = []
    runs = [("nominal", result.get("nominal", {}))]
    runs += [(tag, {tid: res if "skipped" in res else {"metrics": res}
                    for tid, res in tests.items() if isinstance(res, dict)})
             for tag, tests in result.get("temperature_extremes", {}).items()]
    for where, tests in runs:
        for test in ("power", "surge"):
            entry = tests.get(v.ids[test], {})
            metrics = entry.get("metrics")
            if "skipped" in entry:
                flags.append(f"{v.ids[test]} not run at {where} ({entry['skipped']})")
                continue
            if metrics is None:
                flags.append(f"{v.ids[test]} missing at {where}")
                continue
            hits = [k for k, x in metrics.items() if k.endswith("oscillation") and x is not False]
            if hits:
                flags.append(f"{v.ids[test]} {','.join(hits)} at {where}")
    reversal = v.nominal_raw("gm", "phase_reversal")
    if reversal is not False:
        flags.append(f"phase reversal {reversal}")
    pvt_flags = result.get("pvt", {}).get("summary", {}).get(v.ids["gm"], {}).get("phase_reversal", {})
    if pvt_flags.get("true_at"):
        flags.append(f"phase reversal at {','.join(pvt_flags['true_at'])}")
    check("transient_stability", not flags,
          "power-up/down and supply surge free of oscillation at -40/27/125 C, no phase reversal"
          if not flags else "; ".join(flags))

    if "gm_min_s" in s:
        gm_s = meas["transconductance_min_s"] = v.worst("gm", "gm_s", True)
        check("pvt_transconductance", ge(gm_s, s["gm_min_s"]),
              f"worst gm {fmt(gm_s, 1e3)} mS (limit {s['gm_min_s'] * 1e3:g} mS)")
    return checks, meas


# ------------------------------------------------------------ task constants

TASK_ID = 'sky130-fd-opamp-cmfb-pvt'
KLASS = 'fd'
SUBCKT = 'fd_opamp'
PINS = ['vss', 'ibias', 'vdd', 'vcm', 'vinp', 'vinn', 'voutp', 'voutn']
CONDITIONS = {'schema_version': 1, 'model_lib': '/opt/sky130/continuous/sky130.lib.spice', 'vdd': 1.8, 'vcm_fraction': 0.5, 'temperature': 27.0, 'ibias': 1e-05, 'cl': 2e-12, 'rl': 1000000.0, 'r_net': 100000.0, 'r_gm': 1000.0, 'c_net': 1e-12, 'small_step': 0.1, 'large_step': 0.5, 'cm_step': 0.3, 'thd_amplitude_pp': 1.0, 'thd_frequencies': [1000.0, 10000.0, 100000.0, 1000000.0], 'thd_pvt_frequency': 10000.0, 'thd_sweep_pp': [0.1, 0.2, 0.4, 0.6, 0.8, 1.0, 1.2], 'thd_sweep_frequencies': [10000.0, 1000000.0], 'thd_settle_cycles': 2, 'thd_measure_cycles': 2, 'thd_limit_db': -40.0, 'swing_tran_freq': 1000.0, 'fpbw_amplitude_pp': 1.0, 'f_low': 0.1, 'ibias_tracking_fraction': 0.2, 'source_resistance': 10000.0, 'fd_input_skew': 0.01, 'swing_rl_sweep': [1000.0, 3000.0, 10000.0, 30000.0, 100000.0, 300000.0, 1000000.0], 'psrr_temps': [-40.0, -15.0, 10.0, 27.0, 50.0, 75.0, 100.0, 125.0], 'temp_sweep': [-40.0, 125.0], 'icmr_step': 0.025, 'icmr_cmrr_cap_db': 100.0, 'follower_tolerance': 0.01, 'swing_gain_fraction': 0.9, 'gm_fraction': 0.9, 'operating_avo_min_db': 70.0, 'operating_gbw_min_hz': 8000000.0, 'operating_pm_min_deg': 60.0, 'supply_sweep': [0.9, 1.98, 0.02], 'supply_dc_probe_v': 0.05, 'temp_range_sweep': [-55.0, 150.0, 5.0], 'power_ramps_s': [0.005, 0.01], 'power_hold_s': 0.002, 'power_tmax_s': 1e-06, 'power_settle_band_v': 0.001, 'oscillation_grid_s': 1e-07, 'oscillation_window_s': 2e-06, 'oscillation_threshold_v': 0.002, 'oscillation_max_busy_s': 2e-05, 'supply_step_v': 0.05, 'supply_step_edge_s': 1e-08, 'supply_step_frequencies': [100000.0, 300000.0, 1000000.0], 'supply_step_periods': 3, 'supply_step_band_v': 0.0001, 'pvt': {'corners': ['tt', 'ff', 'ss', 'fs', 'sf'], 'supplies': [1.62, 1.8, 1.98], 'temperatures': [-40.0, 27.0, 125.0], 'load_current': 2e-05, 'worst_case_loads': ['+i', '-i', 'rl_to_ground']}, 'mc': {'corner': 'tt_mm', 'runs': 100, 'seed': 64000, 'process_corner': 'mc', 'process_param': '.param MC_MM_SWITCH=1', 'process_runs': 30, 'process_seed': 65000}}
SIGNOFF_PLAN = {'nominal_detail': False, 'pvt': True, 'pvt_set': 'vertices', 'temperature_extremes': 'stability', 'load_cases': True, 'offset_mc_runs': 30, 'process_mc_runs': 0, 'process_mc_tests': 'core', 'load_tests': 'ac', 'range_tests': 'ac', 'supply_sweep': [1.62, 1.98, 0.09], 'temp_range_sweep': [-40.0, 125.0, 27.5], 'nominal_tests': ['FD-04', 'FD-05', 'FD-08', 'FD-09', 'FD-10', 'FD-12', 'FD-14', 'FD-15', 'FD-16', 'FD-17', 'FD-25', 'FD-26', 'FD-30'], 'pvt_tests': ['FD-01', 'FD-07', 'FD-04', 'FD-05', 'FD-09', 'FD-10', 'FD-12', 'FD-14', 'FD-15']}
SPEC = {'pm_min_deg': 60.0, 'gm_min_db': 10.0, 'thd_max_db': -70.0, 'icmr_pvt_half_window_v': 0.05, 'supply_range_v': [1.62, 1.98], 'temp_range_c': [-40.0, 125.0], 'vos_max_v': 0.002, 'icc_tracking_min': 0.7, 'avo_min_db': 70.0, 'gbw_min_hz': 8000000.0, 'peaking_max_db': 1.0, 'sr_min_v_per_s': 4000000.0, 'settle_max_s': 1e-06, 'icmr_nominal_v': [0.6, 1.6], 'swing_min_v': 2.5, 'cm_gain_max_db': -50.0, 'psrr_cm_min_db': 30.0, 'cm_error_max_v': 0.02, 'en_max_v_rthz': 6e-08, 'fpbw_min_hz': 300000.0, 'offset_sigma_max_v': 0.005, 'icc_max_a': 0.00045, 'vcm_current_max_a': 1e-06}

TEMPLATE_ROOT = Path(__file__).resolve().parent / "benches"
VCM_SOURCE_OHM = 1e3  # every bench feeds the DUT's vcm pin through this resistor
REPORT_DIR = Path("/logs/verifier/reports/analog-signoff")


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
    return max(1, int(os.environ.get("V4_VERIFIER_JOBS", count)))


def main() -> int:
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from utils import write_results

    started = time.time()
    jobs = available_cpus()
    work = Path(tempfile.mkdtemp(prefix="v4-signoff-"))
    result, _ = characterize(KLASS, SUBCKT, "/app/circuit.spi", CONDITIONS, work, jobs,
                             SIGNOFF_PLAN, task_id=TASK_ID)
    shutil.rmtree(work, ignore_errors=True)
    checks, measurements = score(result, SPEC, SIGNOFF_PLAN)
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
