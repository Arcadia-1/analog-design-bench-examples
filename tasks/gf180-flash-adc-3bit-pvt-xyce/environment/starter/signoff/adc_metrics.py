"""The graded metrics of one flash-ADC bench run, from Xyce's raw waveforms (/app/spec/README.md defines them).

Shared by the grader and /app/signoff/run_bench.py. Standard library and NumPy.
"""
from __future__ import annotations

import math
import re

import numpy as np

WARMUP = 2                 # conversions 0 and 1 of every bench are not scored
FIRST_EDGE = 10e-9         # the clock source starts its first rising edge here
PERIOD = 20e-9             # 50 MS/s
SETTLE = 3e-9              # outputs valid from this long after the edge ...
READ = 19e-9               # ... to the read instant
LOW_MAX, HIGH_MIN = 0.2, 0.8  # valid logic levels, as fractions of vdd
POWER_FROM = 50e-9         # power windows start at the third rising edge
# pin -> (node, bench source whose current flows into that pin)
PINS = {"vdd": ("VDD", "VSUP"), "vrefp": ("VREFP", "VRP"), "vrefn": ("VREFN", "VRN"), "clk": ("CLK", "VCK"),
        "vin": ("VIN", "VSIG")}


def bench_info(text: str) -> dict:
    """What a bench deck states about itself: supply, stop time, conversions, expected codes, sine tone."""
    vdd = float(re.search(r"(?mi)^VSUP vdd vss (\S+)", text).group(1))
    stop = re.search(r"(?mi)^\.tran\s+\S+\s+(\S+)", text).group(1)
    stop = float(stop.lower().replace("n", "e-9"))
    expected = re.search(r"(?m)^\* expected codes[^:]*: (.*)$", text)
    sine = re.search(r"(?mi)^VSIG vin vss SIN\(\S+ \S+ (\S+)", text)
    conversions = int(round((stop - 1e-9 - FIRST_EDGE) / PERIOD))
    info = {"vdd": vdd, "stop": stop, "conversions": conversions, "power_to": stop - 1e-9,
            "expected": [int(c) for c in expected.group(1).split()] if expected else None, "tone_bin": None}
    if sine:
        freq = float(sine.group(1).lower().replace("meg", "e6"))
        info["tone_bin"] = round(freq / (1 / PERIOD) * (conversions - WARMUP))
    return info


def _column(names: list[str], data: np.ndarray, name: str) -> np.ndarray:
    return data[:, names.index(name.upper())]


def _window_avg(t: np.ndarray, y: np.ndarray, a: float, b: float) -> float:
    inside = (t > a) & (t < b)
    tt = np.concatenate(([a], t[inside], [b]))
    yy = np.concatenate(([np.interp(a, t, y)], y[inside], [np.interp(b, t, y)]))
    return float(np.sum((yy[1:] + yy[:-1]) * np.diff(tt)) / 2 / (b - a))


def evaluate(names: list[str], data: np.ndarray, info: dict) -> dict:
    """Codes, output-level violations and pin power of one run."""
    t = data[:, 0]
    vdd = info["vdd"]
    bits = {b: _column(names, data, f"B{b}") for b in (2, 1, 0)}
    codes, bad = [], []
    for k in range(WARMUP, info["conversions"]):
        edge = FIRST_EDGE + k * PERIOD
        a, b = edge + SETTLE, edge + READ
        inside = (t > a) & (t < b)
        code = 0
        for bit, weight in ((2, 4), (1, 2), (0, 1)):
            y = bits[bit]
            read = float(np.interp(b, t, y))
            span = np.concatenate(([np.interp(a, t, y), read], y[inside]))
            if read > 0.5 * vdd:
                code += weight
                if span.min() < HIGH_MIN * vdd:
                    bad.append(f"conversion {k} b{bit} fell to {span.min():.3f} V")
            elif span.max() > LOW_MAX * vdd:
                bad.append(f"conversion {k} b{bit} rose to {span.max():.3f} V")
        codes.append(code)
    power = {}
    for pin, (node, source) in PINS.items():
        p = -_column(names, data, node) * _column(names, data, f"{source}#BRANCH")
        power[pin] = _window_avg(t, p, POWER_FROM, info["power_to"])
    return {"codes": codes, "bad_levels": bad, "power_w": sum(power.values()),
            **{f"power_{pin}_w": v for pin, v in power.items()}}


def spectrum(codes: list[int], tone_bin: int = 1) -> tuple[float, float]:
    """SNDR and SFDR of a coherent record: DC removed, the tone bin against every other bin up to Nyquist
    (the Nyquist bin at half weight, as a one-sided spectrum counts it)."""
    n = len(codes)
    mean = sum(codes) / n
    power = []
    for k in range(1, n // 2 + 1):
        re_ = sum((c - mean) * math.cos(2 * math.pi * k * i / n) for i, c in enumerate(codes))
        im_ = sum((c - mean) * math.sin(2 * math.pi * k * i / n) for i, c in enumerate(codes))
        power.append((re_ * re_ + im_ * im_) * (0.5 if k == n // 2 else 1.0))
    signal = power[tone_bin - 1]
    others = [p for k, p in enumerate(power, start=1) if k != tone_bin]
    if signal <= 0:
        return -math.inf, -math.inf
    noise, spur = sum(others), max(others)
    return (10 * math.log10(signal / noise) if noise > 0 else math.inf,
            10 * math.log10(signal / spur) if spur > 0 else math.inf)
