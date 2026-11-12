#!/usr/bin/env python3
"""SKY130 signoff rules shared by every verifier of this family; imported by signoff_ngspice.py.

The netlist rules, voltage ratings, stored-energy accounting, res_xhigh_po temperature coefficient, model-failure
and convergence handling that /app/spec/signoff-rules.md describes. Task verifiers do not call it directly: they run
ngspice through utils.run_spice, which goes through signoff_ngspice.run. Standard library only.
"""
from __future__ import annotations

import argparse
import concurrent.futures as futures
import itertools
import json
import math
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
CORNERS = {"tt", "ff", "ss", "fs", "sf"}
MEASURE_RE = re.compile(r"^@@\s+([A-Za-z_][A-Za-z0-9_]*)\s*(\S*)\s*$")
LIB_RE = re.compile(r'^(\s*\.lib\s+(?:"[^"]*"|\S+)\s+)(\w+)(\s*)$', re.I)
PARAM_RE = {
    key: re.compile(rf"^(\s*\.param\s+{key}\s*=\s*)(\S+)(\s*)$", re.I)
    for key in ("supply", "temperature")
}
INCLUDE_RE = re.compile(r'^\s*\.include\s+"/app/circuit\.spi"\s*$', re.I)
FATAL_RE = re.compile(r"timestep too small|simulation\(s\) aborted|run simulation\(s\) aborted", re.I)
# ngspice's search for a DC operating point can fail, or reach the operating point only through its transient-op
# fallback (a pseudo-transient that can stop short of the DC solution), depending on details as small as the order of
# the netlist's lines. A run that fails to converge, or whose operating point came from that fallback, runs again with
# more gmin and source steps and more DC iterations, and a run whose transient stopped on its timestep also with gear
# integration; the first run that converges directly is graded. A run whose operating point still comes from the
# fallback is graded as it is and reported.
CONVERGENCE_RE = re.compile(r"timestep too small|singular matrix|gmin stepping failed|source stepping failed|"
                            r"no convergence|simulation\(s\) aborted", re.I)
OP_FALLBACK_RE = re.compile(r"transient op started", re.I)
RETRY_OPTIONS = [".option gminsteps=50 srcsteps=50 itl1=500", ".option method=gear"]
# The ngspice line worth showing when a run exits with an error ("Error on line N" only says where).
ERROR_LINE_RE = re.compile(r"could not|fatal|not found|unknown|no such|singular|too small|aborted|error", re.I)


def first_error(log: str) -> str:
    lines = [ln.strip() for ln in log.splitlines() if ERROR_LINE_RE.search(ln) and not ln.lower().lstrip().startswith("error on line")]
    fatal = [ln for ln in lines if ln.lower().startswith("fatal")]
    return (fatal or lines or [""])[0][:160]


# The PDK's own ngspice settings (libs.tech/ngspice/spinit) read its libraries with `set ngbehavior=hsa`, HSPICE
# compatibility, which among other things makes BSIM4 pick a multi-finger device's model bin by its finger width
# w/nf, as the SKY130 models were fitted. ngspice's default picks it by the total w, so `w=24 nf=4` and four `w=6`
# copies would simulate as different devices. Every run therefore starts from this setting; the PDK file's
# ng_nomodcheck is left out so the model parameter check below still runs. Each run stays on one thread (the image's
# spinit sets num_threads=8 and ngspice ignores OMP_NUM_THREADS; ./.spiceinit is read after spinit).
SPICEINIT = "set ngbehavior=hsa\nset num_threads=1\n"

# ngspice's BSIM parameter check: a device whose binned parameters come out invalid at this corner and temperature
# (some multi-finger sizes do) gets "Fatal: u0 ... is not positive" and the run goes on with a wrong device.
MODEL_CHECK_RE = re.compile(r"Fatal error: detected during \S+ parameter checking for\s+model \S+ of device instance (\S+)", re.I)
FATAL_PARAM_RE = re.compile(r"^\s*Fatal:\s*(.+?)\s*$", re.M)


def device_name(path: str) -> str:
    """m.xdut.xm2.msky130_fd_pr__nfet_01v8 -> xdut.xm2 (sky130_fd_pr__nfet_01v8)"""
    parts = path.split(".")
    if len(parts) >= 3 and parts[-1].startswith("m"):
        return f"{'.'.join(parts[1:-1])} ({parts[-1][1:]})"
    return path


def model_failures(log: str) -> list[tuple[str, str]]:
    """(device, failed parameter) for every device whose model parameters failed ngspice's check in a log."""
    found, start = [], 0
    for m in MODEL_CHECK_RE.finditer(log):
        params = FATAL_PARAM_RE.findall(log[start:m.start()])
        found.append((device_name(m.group(1)), params[-1] if params else "parameter check failed"))
        start = m.end()
    return found


def model_failure(log: str) -> str | None:
    """The first device whose model parameters failed ngspice's check in this run, or None."""
    m = MODEL_CHECK_RE.search(log)
    if not m:
        return None
    params = FATAL_PARAM_RE.findall(log[:m.start()])
    return (f"device model invalid at this corner and temperature: {device_name(m.group(1))}: "
            f"{params[-1] if params else 'parameter check failed'}")
# Device voltage ratings, enforced through ngspice's safe-operating-area check on every SKY130 MOS inside the
# candidate. A 1.8 V device may not see more than 2.8 V gate-source, gate-drain or drain-source in any run: clock
# feedthrough on dynamic nodes stays below that, a 2.97-3.63 V domain does not. Gate-bulk gets its own, looser limit
# (ngspice would otherwise reuse vgs_max): with a channel formed the oxide sees gate-channel, not gate-bulk, so a
# bootstrapped gate above the supply is legitimate; only accumulation-mode stress near 4 V is caught.
# Junctions: at an operating point (an `op`, or a transient's t = 0) no source-bulk or drain-bulk junction may be
# forward biased by more than JUNCTION_FORWARD_MAX, so a design cannot sit on forward body bias or a forward-biased
# well. Transient forward bias is left to the junction models: switching edges overshoot a rail by ~0.4 V and a power
# stage's body diode conducts through its dead time. vbs_max/vbd_max act in the device's own polarity; the reverse
# limits are set far away (11 V) only so ngspice does not reuse the forward ones.
JUNCTION_FORWARD_MAX = 0.4
JUNCTION = {"vbs_max": JUNCTION_FORWARD_MAX, "vbd_max": JUNCTION_FORWARD_MAX, "vbsr_max": 11.0, "vbdr_max": 11.0}
RATINGS = {
    **{f"sky130_fd_pr__{k}": {"vgs_max": 2.8, "vgd_max": 2.8, "vds_max": 2.8, "vgb_max": 4.0, **JUNCTION} for k in (
        "nfet_01v8", "pfet_01v8", "nfet_01v8_lvt", "pfet_01v8_lvt", "pfet_01v8_hvt")},
    **{f"sky130_fd_pr__{k}": {"vgs_max": 4.0, "vgd_max": 4.0, "vds_max": 4.0, "vgb_max": 6.0, **JUNCTION} for k in ("nfet_03v3_nvt",)},
    **{f"sky130_fd_pr__{k}": {"vgs_max": 6.0, "vgd_max": 6.0, "vds_max": 6.0, "vgb_max": 9.0, **JUNCTION} for k in ("nfet_05v0_nvt",)},
    **{f"sky130_fd_pr__{k}": {"vgs_max": 6.0, "vgd_max": 11.0, "vds_max": 11.0, "vgb_max": 11.0, **JUNCTION} for k in ("nfet_g5v0d10v5", "pfet_g5v0d10v5")},
}
# A transient prints its warnings with a time; an `op` and every point of a `dc` sweep print them without one.
# with_ratings brackets every `dc` command with DC_MARK lines, which ngspice prints on stdout in order with the
# warnings: a sweep drives nodes past normal operation on purpose, so neither rule applies there, and every other
# warning without a time comes from an operating point. (Lifting the limits with altermod around each sweep would cost
# one search of the whole device list per device and parameter.)
SOA_RE = re.compile(r"Instance:\s*(\S+)\s+Model:\s*\S+\s+(?:Time:\s*(\S+)\s+)?(\w+)=(\S+)\s+has exceeded\s+(\w+)=(\S+)")
DC_MARK = "@@SOA"  # not a measurement: MEASURE_RE needs a space after @@
MAXWARNS = 20  # per device and parameter; ignored sweep warnings count too, so leave room after a sweep


def rating_failure(log: str) -> str | None:
    """The first rating violation in an ngspice log, or None (see JUNCTION_FORWARD_MAX for the junction rule)."""
    in_dc = False
    for line in log.splitlines():
        if line.startswith(DC_MARK):
            in_dc = line.split()[1:2] == ["dc"]
            continue
        m = SOA_RE.search(line)
        if not m or (m.group(2) is None and in_dc):
            continue
        static = m.group(2) is None or float(m.group(2)) == 0.0
        junction = m.group(5).lower() in ("vbs_max", "vbd_max")
        if junction and not static:
            continue
        what = ("junction forward biased" if junction else "device rating exceeded") + (
            " at an operating point" if m.group(2) is None else "")
        at = f" at t={m.group(2)} s" if m.group(2) is not None else ""
        return f"{what}: {device_name(m.group(1))} {m.group(3)}={m.group(4)} > {m.group(6)}{at}"
    return None
OPS = {
    ">=": lambda v, lim: v >= lim,
    ">": lambda v, lim: v > lim,
    "<=": lambda v, lim: v <= lim,
    "<": lambda v, lim: v < lim,
}


class SpecError(Exception):
    pass


def label(point: dict) -> str:
    return f"{point['corner']}_{point['supply']:.2f}V_{point['temperature']:g}C"


def expand(spec: dict, conditions) -> list[dict]:
    """Bench conditions: "pvt", "nominal", {corners, supplies, temperatures} or a list of points."""
    pvt, nominal = spec["pvt"], spec["nominal"]
    if conditions in (None, "pvt"):
        conditions = {}
    if conditions == "nominal":
        return [dict(nominal)]
    if isinstance(conditions, dict):
        grid = itertools.product(
            conditions.get("corners", pvt["corners"]),
            conditions.get("supplies", pvt["supplies"]),
            conditions.get("temperatures", pvt["temperatures"]),
        )
        return [{"corner": c, "supply": float(s), "temperature": float(t)} for c, s, t in grid]
    if isinstance(conditions, list):
        return [{**nominal, **p} for p in conditions]
    raise SpecError(f"unknown conditions {conditions!r}")


def is_nominal(point: dict, spec: dict) -> bool:
    n = spec["nominal"]
    return (
        point["corner"] == n["corner"]
        and math.isclose(point["supply"], n["supply"])
        and math.isclose(point["temperature"], n["temperature"])
    )


def render(text: str, point: dict, circuit: Path) -> str:
    """Set the corner, supply and temperature of a bench and point it at the candidate circuit."""
    counts = {"lib": 0, "supply": 0, "temperature": 0, "include": 0}
    out = []
    for line in text.splitlines():
        m = LIB_RE.match(line)
        if m and m.group(2).lower() in CORNERS:
            line = f"{m.group(1)}{point['corner']}{m.group(3)}"
            counts["lib"] += 1
        for key, rx in PARAM_RE.items():
            m = rx.match(line)
            if m:
                line = f"{m.group(1)}{point[key]:g}{m.group(3)}"
                counts[key] += 1
        if INCLUDE_RE.match(line):
            line = f'.include "{circuit}"'
            counts["include"] += 1
        out.append(line)
    if counts["lib"] < 1 or counts["supply"] != 1 or counts["temperature"] != 1 or counts["include"] != 1:
        raise SpecError(f"bench must have a corner .lib line and exactly one .param supply=, .param temperature= and .include \"/app/circuit.spi\" (found {counts})")
    return "\n".join(out) + "\n"


def logical_lines(text: str) -> list[str]:
    out: list[str] = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line[0] in "*;":
            continue
        if line.startswith("+") and out:
            out[-1] += " " + line[1:].strip()
        else:
            out.append(line)
    return out


def x_instances(lines: list[str]) -> list[tuple[str, str]]:
    """(instance name, subcircuit) for every X line, both lowercased."""
    found = []
    for line in lines:
        if line[0] in "xX":
            words = [w for w in line.split() if "=" not in w]
            if len(words) >= 2:
                found.append((words[0].lower(), words[-1].lower()))
    return found


def rated_devices(circuit_text: str, deck_text: str, top: str) -> list[tuple[str, str]]:
    """ngspice paths (m.xdut.xm1.msky130_fd_pr__nfet_01v8) and models of every rated MOS inside each DUT instance.
    Lines are read as netlist_lines reads them, so an inline `$`/`;` comment cannot hide a device."""
    subckts = parse_subckts(circuit_text)
    devices: list[tuple[str, str]] = []

    def walk(name: str, prefix: str, stack: tuple[str, ...]) -> None:
        if name in stack:  # a subcircuit that instantiates itself: ngspice rejects it anyway
            return
        for words in subckts[name]["lines"]:
            if words[0][0] in "xX":
                target = x_parts(words)[1]
                inst = words[0].lower()
                if target in subckts:
                    walk(target, f"{prefix}.{inst}", stack + (name,))
                elif target in RATINGS:
                    devices.append((f"m.{prefix}.{inst}.m{target}", target))

    for line in netlist_lines(deck_text):
        words = line.split()
        if words[0][0] in "xX" and x_parts(words)[1] == top.lower() and top.lower() in subckts:
            walk(top.lower(), words[0].lower(), ())
    return devices


def with_ratings(deck_text: str, devices: list[tuple[str, str]]) -> str:
    """Turn on ngspice's SOA warnings, set each rated device's limits right after .control, and mark every `dc`
    sweep so rating_failure can leave its warnings out."""
    if not devices:
        return deck_text
    # One parameter per altermod line: ngspice applies only the first parameter of a multi-parameter line.
    alters = [f"altermod @{path} {k}={v:g}" for path, model in devices for k, v in RATINGS[model].items()]
    out, in_ctrl, found = [], False, False
    for line in deck_text.splitlines():
        low = line.strip().lower()
        first = low.split()[0] if low else ""
        if in_ctrl and first == "dc":
            out += [f"echo {DC_MARK} dc", line, f"echo {DC_MARK} end"]
            continue
        out.append(line)
        if low == ".control" and not found:
            out += alters
            in_ctrl = found = True
        elif low == ".endc":
            in_ctrl = False
    if not found:
        return deck_text
    out.insert(1, f".option warn=1 maxwarns={MAXWARNS}")  # line 1 is the deck title
    return "\n".join(out) + "\n"


# ---- m on a local subcircuit instance ---------------------------------------------------------------------------
# ngspice-46 ignores m=K on an instance of a parameter-free local subcircuit unless some other instance in the deck
# carries an explicit m (then it multiplies). Signoff expands such an instance into K parallel copies, which is what
# m means, so the result does not depend on an unrelated line elsewhere in the netlist.

def multiplier_of(words: list[str]) -> str | None:
    for w in words[1:]:
        if w.lower().startswith("m="):
            return w[2:]
    return None


def with_expanded_multipliers(circuit_text: str) -> str | None:
    """The circuit with every `m=K` (K a whole number) on a local subcircuit instance replaced by K copies, or None
    when there is none."""
    subckts = parse_subckts(circuit_text)
    lines = circuit_text.splitlines()
    out: list[str] = []
    changed = False
    i = 0
    while i < len(lines):
        group = [lines[i]]
        i += 1
        while i < len(lines) and lines[i].lstrip().startswith("+"):
            group.append(lines[i])
            i += 1
        logical = netlist_lines("\n".join(group))
        if len(logical) == 1 and logical[0][0] in "xX":
            words = logical[0].split()
            value = multiplier_of(words)
            k = spice_number(value) if value is not None else None
            if x_parts(words)[1] in subckts and k is not None and k == int(k) and int(k) > 1:
                kept = [w for w in words[1:] if not w.lower().startswith("m=")]
                out += [" ".join([f"{words[0]}_m{j}"] + kept) for j in range(1, int(k) + 1)]
                changed = True
                continue
        out += group
    return "\n".join(out) + "\n" if changed else None


# Source and drain diffusion geometry, as xschem's SKY130 symbols write it for every MOS: with nf fingers there are
# int((nf+1)/2) drain and int((nf+2)/2) source diffusions, each W/nf wide and 0.29 um long. Without it the wrappers
# default ad=as=pd=ps=0: no junction capacitance, so a node between stacked devices can swing far past the rails on a
# fast ramp, and a junction saturation current fixed per instance that neither scales with size nor with temperature.
DIFFUSION_UM = 0.29


def with_diffusion_geometry(circuit_text: str) -> str | None:
    """The circuit with ad, as, pd and ps added to every PDK MOS instance that leaves them out (values it gives are
    kept), or None when there is nothing to add or w and nf are not plain numbers."""
    lines = circuit_text.splitlines()
    out: list[str] = []
    changed = False
    i = 0
    while i < len(lines):
        group = [lines[i]]
        i += 1
        while i < len(lines) and lines[i].lstrip().startswith("+"):
            group.append(lines[i])
            i += 1
        logical = netlist_lines("\n".join(group))
        if len(logical) == 1 and logical[0][0] in "xX" and is_mos(x_parts(logical[0].split())[1]):
            words = logical[0].split()
            given = {w.split("=", 1)[0].lower(): w.split("=", 1)[1] for w in words if "=" in w}
            w, nf = spice_number(given.get("w", "1")), spice_number(given.get("nf", "1"))
            if w and nf and nf >= 1:
                drains, sources, finger = int((nf + 1) / 2), int((nf + 2) / 2), w / nf
                geometry = {"ad": drains * finger * DIFFUSION_UM, "as": sources * finger * DIFFUSION_UM,
                            "pd": 2 * drains * (finger + DIFFUSION_UM), "ps": 2 * sources * (finger + DIFFUSION_UM)}
                missing = [f"{k}={v:.6g}" for k, v in geometry.items() if k not in given]
                if missing:
                    out.append(" ".join([logical[0]] + missing))
                    changed = True
                    continue
        out += group
    return "\n".join(out) + "\n" if changed else None


def with_mismatch_mult(circuit_text: str) -> str | None:
    """The circuit with `mult=K` added to every PDK instance that has `m=K` and no `mult`, or None when there is none.
    The SKY130 wrappers use `mult` only to scale their local mismatch (sigma over sqrt(w*l*mult)), while `m` makes the
    parallel copies, so `m=K` alone would give K copies one shared mismatch draw of a single device's size. With `mult`
    the K copies average their mismatch, as K real devices do. Without device mismatch (MC_MM_SWITCH=0) nothing changes."""
    subckts = parse_subckts(circuit_text)
    lines = circuit_text.splitlines()
    out: list[str] = []
    changed = False
    i = 0
    while i < len(lines):
        group = [lines[i]]
        i += 1
        while i < len(lines) and lines[i].lstrip().startswith("+"):
            group.append(lines[i])
            i += 1
        logical = netlist_lines("\n".join(group))
        if len(logical) == 1 and logical[0][0] in "xX":
            words = logical[0].split()
            target = x_parts(words)[1]
            value = multiplier_of(words)
            if (target.startswith("sky130_fd_pr__") and target not in subckts and value is not None
                    and not any(w.lower().startswith("mult=") for w in words[1:])):
                out.append(f"{logical[0]} mult={value}")
                changed = True
                continue
        out += group
    return "\n".join(out) + "\n" if changed else None


# ---- res_xhigh_po temperature coefficient ----------------------------------------------------------------------
# The PDK's res_xhigh_po body is a voltage-dependent resistor, which ngspice turns into a behavioural resistor: that
# drops the tc1/tc2 of its model card and has no thermal noise, so in plain ngspice the device is flat in temperature
# and noiseless. Signoff includes the candidate through a copy of the PDK subcircuit whose body is a plain resistor
# with the card's coefficients, referred to the circuit's 27 °C rather than 25 °C so 27 °C values are unchanged. The
# copy drops the body's voltage coefficient (under 1 % below about 0.3 V per µm of length) and keeps the heads, the
# parasitic capacitors, the corner and mismatch parameters.
XHIGH_PO_RE = re.compile(r"(?<![\w.])sky130_fd_pr__res_xhigh_po(?![\w.])", re.I)
XHIGH_PO_TC = "gdb_res_xhigh_po_tc"
XHIGH_PO_SUBCKT = f"""
.subckt {XHIGH_PO_TC} r0 r1 sub mult=1
+ w=1 l=1
.param leff = {{l-0.0592}}
+ weff = {{w-0.056+2*sw_polycd}}
+ rhead0 = {{188.2/(weff-0.0672*max(0.69-w,0)+1.41)}}
+ rbody0 = {{sw_sky130_fd_pr__res_xhigh_po_rs*leff/(weff-0.0672*max(0.69-w,0))}}
+ rhead = {{rhead0*sw_poly_head_res*(1+sw_mm_sky130_fd_pr__res_generic_po_head*mismatch_factor*MC_MM_SWITCH*AGAUSS(0,1.0,1)/sqrt(w*mult))}}
+ rbody = {{rbody0*(1+sw_mm_sky130_fd_pr__res_xhigh_po*mismatch_factor*MC_MM_SWITCH*AGAUSS(0,1.0,1)/sqrt(w*l*mult))}}
+ tcbody = {{-1.47e-3-5e-7*sw_sky130_fd_pr__res_xhigh_po_rs}}
rend1 r0 t1 reshead r = {{rhead}}
rend2 t2 r1 reshead r = {{rhead}}
xc0 r0 sub sky130_fd_pr__model__parasitic__cap_p2ps w = {{weff}} l = {{l+2.08}} m = 0.5
xc1 r1 sub sky130_fd_pr__model__parasitic__cap_p2ps w = {{weff}} l = {{l+2.08}} m = 0.5
rbody t1 t2 resbody r = {{rbody}}
.model resbody r tc1 = {{tcbody}} tc2 = 2.7e-6 tnom = 27
.model reshead r tc1 = -4.3e-4 tc2 = -1.3e-5 tnom = 25
.ends {XHIGH_PO_TC}
"""


def with_xhigh_po_tc(circuit_text: str) -> str | None:
    """The candidate with every res_xhigh_po on the copy with temperature coefficient and noise; None if it uses none."""
    if not XHIGH_PO_RE.search(circuit_text):
        return None
    return XHIGH_PO_RE.sub(XHIGH_PO_TC, circuit_text) + XHIGH_PO_SUBCKT


# ---- Netlist rules, checked on the candidate before any simulation ----------------------------------------------
# Unrated MOS: the rating check above covers only the RATINGS models, so any other SKY130 MOS model (special_*,
# rf_*, esd_*, *_20v0*, *_g5v0d16v0*, pfet_01v8_mvt, sonos ...) is refused. Other PDK devices (resistors,
# capacitors, diodes, BJTs) stay allowed.
#
# Passive rules (spec "passive_rules"), applied to the flattened top subcircuit:
#   inductor_series_r_per_nh  every L has its own series ideal resistor of at least this many ohms per nH. "Series"
#                             means one terminal node of the L joins exactly two element terminals, this L and that
#                             resistor, and is neither a subcircuit pin nor ground; each resistor serves one L.
#   inductor_max_nh           largest single L
#   inductor_total_max_nh     largest sum of all L
#   k_max                     largest |k| of a K
#   k_per_inductor_max        most K statements one L may appear in
# L, K and the series R must be written as `name node node value` with a plain numeric value and no parameters.
#
# Body rules (spec "body_rules", e.g. {"pfet": ["vss"]}): the substrate-side terminal of every device of that kind in
# the flattened top subcircuit must be one of the listed top-level pins, or ground ("0" or "gnd") when the list holds
# "0". The terminal is the body of a "pfet" or "nfet", the `s` terminal of an "npn", and the collector of a "pnp" (the
# SKY130 PNPs are vertical devices whose collector is the p-substrate). For rules the models cannot see, such as an
# n-well below the grounded p-substrate: they include no well-to-substrate junction.
BODY_TERMINAL = {"pfet": (3, "body"), "nfet": (3, "body"), "npn": (3, "substrate terminal s"),
                 "pnp": (0, "collector (the substrate)")}
SCALE = {"meg": 1e6, "mil": 25.4e-6, "t": 1e12, "g": 1e9, "k": 1e3, "m": 1e-3, "u": 1e-6, "n": 1e-9, "p": 1e-12, "f": 1e-15}
NUMBER_RE = re.compile(r"^([+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:e[+-]?\d+)?)([a-z]*)$", re.I)
MOS_RE = re.compile(r"(?:^|_)(?:[np]fet|sonosfet)(?:_|$)")
GROUND = {"0", "gnd"}
REL_TOL = 1e-9


def spice_number(token: str) -> float | None:
    """A plain SPICE literal such as 5n, 2.5nH, 1meg or 1e-9; None for anything else (expressions included)."""
    m = NUMBER_RE.match(token.strip())
    if not m:
        return None
    suffix = m.group(2).lower()
    scale = 1.0
    if suffix:
        prefix = next((p for p in SCALE if suffix.startswith(p)), None)
        if prefix is None:
            return None
        scale = SCALE[prefix]
    v = float(m.group(1)) * scale
    return v if math.isfinite(v) else None


def netlist_lines(text: str) -> list[str]:
    """Logical lines with comments removed. `$` and `;` end a line (as check_circuit.py reads it); a `+` line joins
    the previous non-comment line (as ngspice reads it), so a continuation hidden behind a comment still counts."""
    out: list[str] = []
    for raw in text.splitlines():
        line = re.split(r"[$;]", raw, maxsplit=1)[0].strip()
        if not line or line[0] == "*":
            continue
        if line[0] == "+":
            if out:
                out[-1] += " " + line[1:].strip()
        else:
            out.append(line)
    return out


def x_parts(words: list[str]) -> tuple[list[str], str]:
    """(nodes, target) of an X line: the target is the last word before the first parameter."""
    plain = []
    for w in words[1:]:
        if "=" in w or w.lower() == "params:":
            break
        plain.append(w)
    return (plain[:-1], plain[-1].lower()) if plain else ([], "")


def is_mos(target: str) -> bool:
    return target.startswith("sky130_") and "__" in target and bool(MOS_RE.search(target.split("__", 1)[1]))


def parse_subckts(text: str) -> dict[str, dict]:
    subckts: dict[str, dict] = {}
    current = None
    for line in netlist_lines(text):
        words = line.split()
        low = words[0].lower()
        if low == ".subckt" and len(words) > 1:
            pins = []
            for w in words[2:]:
                if "=" in w or w.lower() == "params:":
                    break
                pins.append(w.lower())
            current = words[1].lower()
            subckts[current] = {"pins": pins, "lines": []}
        elif low == ".ends":
            current = None
        elif current is not None:
            subckts[current]["lines"].append(words)
    return subckts


def device_check(circuit_text: str, spec: dict, benches: Path, grid: list[tuple[str, float]], jobs: int) -> list[str]:
    """Every PDK MOS of the circuit at every (corner, temperature) the signoff grid uses, before any bench runs: a
    device whose model parameters fail ngspice's check there would be simulated wrong at that point. The nominal
    preflight runs this too, so such a size shows up before signoff."""
    subckts = parse_subckts(circuit_text)
    top = spec["subckt"]["name"].lower()
    if top not in subckts or not any(
            words[0][0] in "xX" and is_mos(x_parts(words)[1]) for sub in subckts.values() for words in sub["lines"]):
        return []
    lib = next((m.group(1) for b in spec["benches"] for line in (benches / b["file"]).read_text().splitlines()
                if (m := LIB_RE.match(line)) and m.group(2).lower() in CORNERS), None)
    if lib is None:
        return []
    pins = subckts[top]["pins"]

    def one(point: tuple[str, float]) -> list[str]:
        corner, temperature = point
        work = Path(tempfile.mkdtemp(prefix="gdb-dev-"))
        try:
            (work / ".spiceinit").write_text(SPICEINIT)
            (work / "circuit.spi").write_text(circuit_text)
            nodes = [f"gdb_p{i}" for i in range(len(pins))]
            deck = "\n".join(["* device check", f"{lib}{corner}", '.include "circuit.spi"',
                              f"Xgdb_check {' '.join(nodes)} {top}",
                              *(f"Rgdb_{i} {n} 0 1e9" for i, n in enumerate(nodes)),
                              f".temp {temperature:g}", ".control", "op", ".endc", ".end", ""])
            (work / "check.sp").write_text(deck)
            try:
                proc = subprocess.run(["ngspice", "-b", "check.sp"], cwd=work, capture_output=True, text=True,
                                      timeout=120, env={**os.environ, "OMP_NUM_THREADS": "1"})
                log = proc.stdout + proc.stderr
            except subprocess.TimeoutExpired:
                return []
            return [f"{dev.removeprefix('xgdb_check.')} at {corner}, {temperature:g} °C: {param}"
                    for dev, param in model_failures(log)]
        finally:
            shutil.rmtree(work, ignore_errors=True)

    with futures.ThreadPoolExecutor(max_workers=max(1, jobs)) as pool:
        found = [v for vs in pool.map(one, grid) for v in vs]
    return list(dict.fromkeys(found))


def netlist_violations(circuit_text: str, spec: dict) -> list[str]:
    subckts = parse_subckts(circuit_text)

    violations = []
    for name, sub in subckts.items():
        for words in sub["lines"]:
            if words[0][0] in "xX":
                target = x_parts(words)[1]
                value = multiplier_of(words)
                if target in subckts and value is not None:
                    k = spice_number(value)
                    if k is None or k < 1 or k != int(k):
                        violations.append(f"{words[0]} in {name}: m={value} on a local subcircuit instance must be a "
                                          "whole number (signoff makes that many parallel copies)")
                if target not in subckts and is_mos(target) and target not in RATINGS:
                    violations.append(
                        f"{words[0]} in {name} uses {target}, a MOS model without a voltage rating in signoff; "
                        f"use one of {', '.join(sorted(m.removeprefix('sky130_fd_pr__') for m in RATINGS))}")
            elif words[0][0] in "rRcClL":
                # An ideal element is its value and nothing else: noisy=0, dtemp, temp or tc1 would give it no thermal
                # noise or a temperature behaviour no real device has.
                extra = [w for w in words[1:] if "=" in w and w.split("=", 1)[0].lower() != "m"]
                if extra:
                    violations.append(f"{words[0]} in {name}: an ideal element takes only its value (and m=), not "
                                      f"{', '.join(extra)}")

    rules = spec.get("passive_rules")
    body_rules = spec.get("body_rules")
    top = spec["subckt"]["name"].lower()
    if not (rules or body_rules) or top not in subckts:
        return violations

    # Flatten the top subcircuit: elements as (kind, path, nodes, words); child-internal nodes get the instance path.
    elements: list[tuple[str, str, list[str], list[str]]] = []
    couplings: list[tuple[str, list[str], str]] = []  # (path, words, prefix for the inductor names)

    def walk(name: str, prefix: str, nodemap: dict[str, str], depth: int) -> None:
        if depth > 16:
            violations.append(f"subcircuit nesting deeper than 16 levels at {prefix}")
            return

        def node(n: str) -> str:
            n = n.lower()
            return n if n in GROUND else nodemap.get(n, f"{prefix}{n}")

        for words in subckts[name]["lines"]:
            kind = words[0][0].upper()
            path = f"{prefix}{words[0].lower()}"
            if kind == "X":
                nodes, target = x_parts(words)
                if target in subckts:
                    pins = subckts[target]["pins"]
                    walk(target, f"{path}.", {p: node(n) for p, n in zip(pins, nodes)}, depth + 1)
                else:
                    elements.append(("X", path, [node(n) for n in nodes], words))
            elif kind == "K":
                couplings.append((path, words, prefix))
            else:  # R, C, L (check_circuit.py allows no other element)
                elements.append((kind, path, [node(n) for n in words[1:3]], words))

    walk(top, "", {p: p for p in subckts[top]["pins"]}, 0)
    pins = set(subckts[top]["pins"])

    for kind_name, allowed in (body_rules or {}).items():
        if kind_name not in BODY_TERMINAL:
            raise SpecError(f"body_rules: unknown device kind {kind_name!r} (one of {', '.join(BODY_TERMINAL)})")
        index, what = BODY_TERMINAL[kind_name]
        allowed = [p.lower() for p in allowed]
        for kind, path, nodes, words in elements:
            target = x_parts(words)[1] if kind == "X" else ""
            device = is_mos(target) if kind_name.endswith("fet") else target.startswith("sky130_fd_pr__")
            if device and f"__{kind_name}_" in target:
                node = nodes[index] if len(nodes) > index else None
                if node not in allowed and not (node in GROUND and "0" in allowed):
                    violations.append(f"{path} ({target.removeprefix('sky130_fd_pr__')}) has its {what} on "
                                      f"{node or 'no node'}; every {kind_name} {what} must be {' or '.join(allowed)}")
    if not rules:
        return violations

    def plain_value(words: list[str]) -> float | None:
        return spice_number(words[3]) if len(words) == 4 else None

    terminals: dict[str, list[int]] = {}
    for i, (_, _, nodes, _) in enumerate(elements):
        for n in nodes:
            terminals.setdefault(n, []).append(i)

    inductors = {}
    for i, (kind, path, nodes, words) in enumerate(elements):
        if kind != "L":
            continue
        value = plain_value(words)
        if value is None or value <= 0:
            violations.append(f"inductor {path}: write it as `Lname node node value` with a plain positive value and no parameters")
            continue
        inductors[path] = (i, value)

    per_nh = rules.get("inductor_series_r_per_nh")
    if per_nh is not None:
        options: dict[str, list[int]] = {}
        why: dict[str, str] = {}
        for path, (i, value) in inductors.items():
            need = per_nh * value / 1e-9
            options[path] = []
            notes = []
            for n in dict.fromkeys(elements[i][2]):
                others = [j for j in terminals.get(n, []) if j != i]
                if n in pins or n in GROUND or len(terminals[n]) != 2 or len(others) != 1:
                    continue
                kind, rpath, _, rwords = elements[others[0]]
                if kind != "R":
                    continue
                r = plain_value(rwords)
                if r is None:
                    notes.append(f"{rpath} is not written as `Rname node node value`")
                elif r < need * (1 - REL_TOL):
                    notes.append(f"{rpath} is {r:g} Ω")
                else:
                    options[path].append(others[0])
            if not options[path]:
                why[path] = "; ".join(notes) if notes else "neither terminal node joins only this inductor and one resistor"
        # Each resistor serves one inductor: bipartite matching by augmenting paths.
        owner: dict[int, str] = {}

        def assign(path: str, seen: set[int]) -> bool:
            for j in options[path]:
                if j in seen:
                    continue
                seen.add(j)
                if j not in owner or assign(owner[j], seen):
                    owner[j] = path
                    return True
            return False

        for path, (i, value) in inductors.items():
            need = per_nh * value / 1e-9
            if not assign(path, set()):
                reason = why.get(path) or "its series resistor already serves another inductor"
                violations.append(
                    f"inductor {path} ({value / 1e-9:g} nH) needs its own series resistor of at least {need:.4g} Ω "
                    f"({per_nh:g} Ω per nH) on a node that joins only the two: {reason}")

    if (lmax := rules.get("inductor_max_nh")) is not None:
        for path, (_, value) in inductors.items():
            if value / 1e-9 > lmax * (1 + REL_TOL):
                violations.append(f"inductor {path} is {value / 1e-9:g} nH; at most {lmax:g} nH per inductor")
    if (ltot := rules.get("inductor_total_max_nh")) is not None:
        total = sum(v for _, v in inductors.values()) / 1e-9
        if total > ltot * (1 + REL_TOL):
            violations.append(f"inductors total {total:g} nH; at most {ltot:g} nH in all")

    kmax, kper = rules.get("k_max"), rules.get("k_per_inductor_max")
    uses: dict[str, int] = {}
    for path, words, prefix in couplings:
        k = plain_value(words)
        if k is None:
            violations.append(f"coupling {path}: write it as `Kname Lname Lname value` with a plain value and no parameters")
            continue
        for lname in words[1:3]:
            uses[f"{prefix}{lname.lower()}"] = uses.get(f"{prefix}{lname.lower()}", 0) + 1
        if kmax is not None and abs(k) > kmax * (1 + REL_TOL):
            violations.append(f"coupling {path} has k={k:g}; |k| must be at most {kmax:g}")
    if kper is not None:
        for lpath, n in uses.items():
            if n > kper:
                violations.append(f"inductor {lpath} appears in {n} K statements; at most {kper}")
    return violations


# ---- Stored-energy accounting over every supply window ----------------------------------------------------------
# Every transient starts from the DC operating point, so every capacitor inside the candidate starts charged at no
# cost. A large capacitor on an internal rail, fed from vdd through a large resistance or a switch, can run part of
# the circuit through a power window much shorter than its time constant without the measured sources paying for it.
# So every `meas tran <name> avg|integ <expr> from=a to=b` whose expression depends (directly or through `let`) on the
# current of a top-level voltage source of the bench is a supply window. Signoff follows the energy stored in the ideal
# capacitors, MIM capacitors, inductors and couplings inside each instance of the candidate (1/2 C v^2, 1/2 L i^2,
# M i1 i2, with ngspice's own effective C, L and M) and in its MOS capacitors (see dut_storage). The release in a window
# is how far the stored energy at b lies below the lowest level it had between the start of the run and a. Energy
# stored above that level was delivered by the bench's sources during the run, so a hold, reset, sampling or timing
# capacitor that charges earlier in the run and discharges inside the window releases nothing; a reservoir running down
# from its operating-point charge releases all it gives. The release may not exceed ENERGY_FRACTION of the energy all
# the bench's voltage sources deliver over the window (the positive part of each one's power, integrated) plus a floor
# of ENERGY_FLOOR_CURRENT x supply x window length + ENERGY_FLOOR; a larger release fails every check, like a netlist
# rule. The base is every source, not only the measured one, so a window that measures a small input pin still counts
# the circuit's own supply. The floor scales with the window, so a 1 ns window at 1.98 V hides at most 0.54 fJ
# (0.27 uA), not 5 fJ (2.5 uA). A bench whose supply power is not measured by such a window declares it in spec.json
# (bench "energy_windows").
ENERGY_FRACTION = 0.05
ENERGY_FLOOR_CURRENT = 20e-9
ENERGY_FLOOR = 0.5e-15
# Control commands after which zres_e must be rebuilt: a new or another plot, a changed circuit, or control flow.
PLOT_CHANGES = {"tran", "setplot", "linearize", "reset", "destroy", "alter", "altermod", "run", "op", "dc", "ac",
                "noise", "pz", "sens", "tf", "source", "remcirc", "load", "if", "else", "end", "foreach", "while",
                "repeat", "dowhile", "label", "goto", "break", "continue"}
WINDOW_RE = re.compile(r"^\s*meas(?:ure)?\s+tran\s+(\S+)\s+(?:avg|integ)\s+(.*?)\s+from=(\S+)\s+to=(\S+)\s*$", re.I)
SRC_REF_RE = re.compile(r"\bi\(\s*([A-Za-z0-9_]+)\s*\)|\b([A-Za-z0-9_]+)#branch\b", re.I)
IDENT_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
MIM_RE = re.compile(r"^sky130_fd_pr__cap_mim_m\d_\d$")


def dut_storage(circuit_text: str, deck_text: str, top: str) -> tuple[list[str], list[str], list[str]]:
    """ngspice expressions, one per energy-storing element inside each DUT instance, that sum to the stored energy:
    (passive terms, MOS capacitor terms, MOS charge vectors the transient must save)."""
    subckts = parse_subckts(circuit_text)
    terms: list[str] = []
    mos_terms: list[str] = []
    saves: list[str] = []
    top = top.lower()
    mosfets: list[tuple[str, str, list[str]]] = []  # (path, model, flattened d g s b)
    touches: dict[str, int] = {}  # element terminals per flattened node
    outside: set[str] = {"0"}  # nodes of the bench (the DUT pins): never dangling
    parent: dict[str, str] = {}  # nodes joined through resistors

    def find(n: str) -> str:
        while parent.get(n, n) != n:
            parent[n] = parent.get(parent[n], parent[n])
            n = parent[n]
        return n

    def v(n: str) -> str:
        return "" if n == "0" else f"v({n})"

    def diff(a: str, b: str) -> str:
        return v(a) if b == "0" else (f"-{v(b)}" if a == "0" else f"{v(a)}-{v(b)}")

    def walk(name: str, prefix: str, nodemap: dict[str, str], depth: int) -> None:
        if depth > 64:
            # A recursive or absurdly deep hierarchy: name an element ngspice cannot find, so the accounting fails closed.
            terms.append(f"@c.{prefix}.unaccounted[capacitance]")
            return

        def node(n: str) -> str:
            n = n.lower()
            return "0" if n in GROUND else nodemap.get(n, f"{prefix}.{n}")

        def cap(path: str, a: str, b: str) -> None:
            a, b = node(a), node(b)
            if a == b:
                return
            terms.append(f"0.5*@{path}[capacitance]*({diff(a, b)})^2")

        for words in subckts[name]["lines"]:
            kind, inst = words[0][0].upper(), words[0].lower()
            if kind == "X":
                nodes, target = x_parts(words)
                if target in subckts:
                    pins = subckts[target]["pins"]
                    walk(target, f"{prefix}.{inst}", {p: node(n) for p, n in zip(pins, nodes)}, depth + 1)
                    continue
                flat = [node(n) for n in nodes]
                for n in flat:
                    touches[n] = touches.get(n, 0) + 1
                if MIM_RE.match(target) and len(nodes) >= 2:
                    cap(f"c.{prefix}.{inst}.c1", nodes[0], nodes[1])
                elif target in RATINGS and len(nodes) >= 4:
                    mosfets.append((f"m.{prefix}.{inst}.m{target}", target, flat[:4]))
                elif target.startswith("sky130_fd_pr__res_") and len(flat) >= 2:
                    parent[find(flat[0])] = find(flat[1])
                continue
            flat = [node(n) for n in words[1:3]] if kind in "RCL" and len(words) >= 3 else []
            for n in flat:
                touches[n] = touches.get(n, 0) + 1
            if kind == "R" and len(flat) == 2:
                parent[find(flat[0])] = find(flat[1])
            elif kind == "C" and len(words) >= 3:
                cap(f"c.{prefix}.{inst}", words[1], words[2])
            elif kind == "L" and len(words) >= 3:
                terms.append(f"0.5*@l.{prefix}.{inst}[inductance]*l.{prefix}.{inst}#branch^2")
            elif kind == "K" and len(words) >= 3:
                # After setup ngspice reports a K's coefficient as the mutual inductance M = k*sqrt(L1*L2) (ngspice-46),
                # so M*i1*i2 needs no further factor.
                la, lb = (f"l.{prefix}.{w.lower()}#branch" for w in words[1:3])
                terms.append(f"@k.{prefix}.{inst}[coefficient]*{la}*{lb}")

    for words in (line.split() for line in logical_lines(deck_text)):
        if words[0][0] in "xX":
            nodes, target = x_parts(words)
            if target == top and top in subckts:
                pins = subckts[top]["pins"]
                nodemap = {p: ("0" if n.lower() in GROUND else n.lower()) for p, n in zip(pins, nodes)}
                outside.update(nodemap.values())
                walk(top, words[0].lower(), nodemap, 0)

    def dangling(n: str) -> bool:
        return n not in outside and touches.get(n, 0) <= 1

    for path, target, (d, g, s, b) in mosfets:
        # A MOS capacitor carries no channel current: drain and source are one node or joined through resistors, one of
        # them touches nothing else, or the gate sits on the source. Its whole charge counts like a capacitor's. Every
        # other MOS is a transistor: the circuit's own drivers move its gate and channel charge at every edge, and that
        # switching charge alone can release several times the allowance of a 1 ns window, so it is not counted.
        if not (d == s or dangling(d) or dangling(s) or g == s or find(d) == find(s)):
            continue
        # BSIM4 reports the intrinsic charges qg, qd, qs, qb (summing to zero) without the overlap and junction charges,
        # in the n-type sign convention, already multiplied by m. Intrinsic energy: half the charge-weighted sum of the
        # terminal voltages, taken from the body (any reference gives the same sum), signed by the device type; on a
        # MOS capacitor in inversion it reads 0.78-1.0 of the exact integral of v dq (NMOS 0.97-1.0, PMOS 0.78-0.92).
        # Overlap and junctions: 1/2 C v^2 with ngspice's cgdo, cgso, capbd and capbs at the end of the run.
        p = f"@{path}"
        charged = [(t, q) for t, q in ((g, "qg"), (d, "qd"), (s, "qs")) if t != b]
        if charged:
            sign = "-" if "pfet" in target else ""
            mos_terms.append(f"{sign}0.5*({' + '.join(f'({diff(t, b)})*{p}[{q}]' for t, q in charged)})")
            saves.extend(f"{p}[{q}]" for _, q in charged)
        mos_terms.extend(f"0.5*{p}[{c}]*({diff(x, y)})^2" for (x, y), c in
                         (((g, d), "cgdo"), ((g, s), "cgso"), ((d, b), "capbd"), ((s, b), "capbs")) if x != y)
    return terms, mos_terms, list(dict.fromkeys(saves))


def with_energy(deck_text: str, storage: tuple[list[str], list[str], list[str]], declared: list[dict]) -> tuple[str, list[dict]]:
    """Inject the stored-energy measurements at every supply window of the deck. Returns the deck and the windows."""
    terms, mos_terms, saves = storage
    every_term = terms + mos_terms
    lines = deck_text.splitlines()
    sources: dict[str, str] = {}  # sources that can deliver energy: name -> voltage expression
    every: set[str] = set()  # every top-level voltage source, 0 V ammeters included, for detection
    in_sub = in_ctrl = False
    for line in logical_lines(deck_text):
        low = line.lower()
        words = low.split()
        if words[0] == ".subckt":
            in_sub = True
        elif words[0] == ".ends":
            in_sub = False
        elif words[0] == ".control":
            in_ctrl = True
        elif words[0] == ".endc":
            in_ctrl = False
        elif not in_sub and not in_ctrl and low[0] == "v" and len(words) >= 3:
            every.add(words[0])
            a, b = ("0" if n in GROUND else f"v({n})" for n in words[1:3])
            if a != b and words[3:] not in (["0"], ["dc", "0"]):  # a 0 V source (return, ammeter) delivers nothing
                sources[words[0]] = a if b == "0" else (f"-{b}" if a == "0" else f"{a}-{b}")

    windows: list[dict] = []
    out: list[str] = []
    lets: dict[str, str] = {}
    in_ctrl = False
    pending = [dict(d, _after=" ".join(d["after"].lower().split())) for d in declared]

    def sources_of(expr: str) -> list[str]:
        found: set[str] = set()
        seen: set[str] = set()
        todo = [expr.lower()]
        while todo:
            e = todo.pop()
            for a, b in SRC_REF_RE.findall(e):
                found.add(a or b)
            for ident in IDENT_RE.findall(e):
                if ident in lets and ident not in seen:
                    seen.add(ident)
                    todo.append(lets[ident])
        return sorted(s for s in found if s in every)

    fresh = [False]  # zres_e (and zres_m) hold the stored energy of the current transient plot

    def low_since_start(vector: str, result: str) -> list[str]:
        """`result` = the lowest value of `vector` from the start of the run to the window's start edge: only a drop
        below that counts as release, since energy stored above it was put there by the bench's sources during the run."""
        return [f"meas tran {result} find {vector} at=$&zres_ta",
                "if zres_ta > zres_t0",
                "unlet zres_lo",
                f"meas tran zres_lo min {vector} from=$&zres_t0 to=$&zres_ta",
                f"let {result} = ({result}+zres_lo-abs({result}-zres_lo))/2",
                "end"]

    def inject(name: str | None, frm: str, to: str, srcs: list[str], linearized: bool) -> list[str]:
        k = len(windows)
        windows.append({"name": name or "declared", "from": frm, "to": to, "measures": srcs})
        names = [f"zres_a{k}", f"zres_b{k}", f"zres_da{k}", f"zres_db{k}", f"zres_r{k}",
                 *([f"zres_ma{k}", f"zres_mb{k}", f"zres_mda{k}", f"zres_mdb{k}", f"zres_mr{k}"] if mos_terms else []),
                 *(f"zres_{s}{k}" for s in sources)]
        # One echo per value: ngspice prints nothing for a missing vector, so each value carries its own key.
        cmds = [f"echo @@E {k} ok $&{name}" if name else f"echo @@E {k} ok 1"]
        if linearized:
            # A linearized plot keeps only the vectors the bench names; measure in the transient plot it came from.
            cmds += ["set zres_back = $curplot", "setplot $zres_plot"]
        # Earlier passes of a loop leave their results behind; a failed meas must not echo a stale one.
        cmds.append(f"unlet {' '.join(names)}")
        if not fresh[0]:
            # The stored-energy vector is built once per transient plot and shared by the windows that follow.
            cmds += ["unlet zres_e zres_m", "let zres_m = 0*time", *(f"let zres_m = zres_m + {t}" for t in mos_terms),
                     "let zres_e = zres_m", *(f"let zres_e = zres_e + {t}" for t in terms)]
            fresh[0] = True
        cmds += [
                 # The edges clipped to the simulated span: avg and integ clip by themselves, find at= does not, and
                 # $& prints 7 digits, so the end edge keeps a relative margin of 2e-5. The start edge is the second
                 # sample: a saved MOS charge reads 0 at the first one, taken before the device computes its charges.
                 f"let zres_ta = {frm}", f"let zres_tb = {to}", "let zres_t0 = time[1]",
                 "let zres_t1 = time[length(time)-1] - 2e-5*abs(time[length(time)-1])",
                 "let zres_ta = (zres_ta+zres_t0+abs(zres_ta-zres_t0))/2",
                 "let zres_tb = (zres_tb+zres_t1-abs(zres_tb-zres_t1))/2",
                 f"meas tran zres_a{k} find zres_e at=$&zres_ta", f"meas tran zres_b{k} find zres_e at=$&zres_tb",
                 # `meas` keeps and `$&` prints about 7 significant digits, and a 1 F reservoir at 3.6 V stores 6.48 J
                 # (7 digits of that are 1 uJ, a whole window's budget). So the edges are measured again on the stored
                 # energy less its rounded start value, a small number, and the release is printed as that difference.
                 f"let zres_d = zres_e - zres_a{k}", *low_since_start("zres_d", f"zres_da{k}"),
                 f"meas tran zres_db{k} find zres_d at=$&zres_tb", f"let zres_r{k} = zres_da{k} - zres_db{k}",
                 f"echo @@E {k} ea $&zres_a{k}", f"echo @@E {k} eb $&zres_b{k}", f"echo @@E {k} er $&zres_r{k}",
                 f"echo @@E {k} ta $&zres_ta", f"echo @@E {k} tb $&zres_tb"]
        if mos_terms:
            cmds += [f"meas tran zres_ma{k} find zres_m at=$&zres_ta", f"meas tran zres_mb{k} find zres_m at=$&zres_tb",
                     f"let zres_d = zres_m - zres_ma{k}", *low_since_start("zres_d", f"zres_mda{k}"),
                     f"meas tran zres_mdb{k} find zres_d at=$&zres_tb", f"let zres_mr{k} = zres_mda{k} - zres_mdb{k}",
                     f"echo @@E {k} mr $&zres_mr{k}"]
        for s in sources:
            cmds += [f"let zres_p = -({sources[s]})*i({s})", "let zres_p = (zres_p+abs(zres_p))/2",
                     f"meas tran zres_{s}{k} integ zres_p from={frm} to={to}", f"echo @@E {k} src:{s} $&zres_{s}{k}"]
        if linearized:
            cmds.append("setplot $zres_back")
        return cmds + [f"echo @@E {k} end"]

    linearized = False
    ctrl_at = None
    i = 0
    while i < len(lines):
        # One logical line: this raw line and its `+` continuations, which the injection must not split.
        group = [lines[i]]
        i += 1
        while i < len(lines) and lines[i].strip().startswith("+"):
            group.append(lines[i])
            i += 1
        line = " ".join([group[0].strip()] + [g.strip()[1:].strip() for g in group[1:]])
        low = " ".join(line.lower().split())
        first = low.split()[0] if low else ""
        if in_ctrl and first == "linearize":
            out.append("set zres_plot = $curplot")
            linearized = True
        elif in_ctrl and first in ("tran", "setplot"):
            linearized = False
        if in_ctrl and first in PLOT_CHANGES:
            fresh[0] = False
        if low == ".control" and ctrl_at is None:
            ctrl_at = len(out) + len(group)
        out += group
        if not low or low.startswith("*"):
            continue
        if low == ".control":
            in_ctrl = True
            continue
        if low == ".endc":
            in_ctrl = False
            continue
        if not in_ctrl:
            continue
        if first == "let" and "=" in low:
            name = low[4:].split("=", 1)[0].strip().split("[")[0].strip()
            lets[name] = lets.get(name, "") + " " + low.split("=", 1)[1]
        m = WINDOW_RE.match(line)
        if m and every_term:
            srcs = sources_of(m.group(2))
            if srcs:
                out += inject(m.group(1).lower(), m.group(3), m.group(4), srcs, linearized)
        for d in pending:
            if not d.get("_done") and low.startswith(d["_after"]):
                d["_done"] = True
                bad = [s for s in d["sources"] if s.lower() not in every]
                if bad:
                    raise SpecError(f"energy window after {d['after']!r} names unknown sources {bad}")
                if every_term:
                    out += inject(None, d["from"], d["to"], [s.lower() for s in d["sources"]], linearized)
    missing = [d["after"] for d in pending if not d.get("_done")]
    if missing:
        raise SpecError(f"energy window: no control line starts with {missing}")
    if windows and saves and ctrl_at is not None:
        # The MOS charges vary over the run, so the transient keeps them as vectors; `save all` keeps everything else.
        out[ctrl_at:ctrl_at] = ["save all"] + [f"save {' '.join(saves[j:j + 24])}" for j in range(0, len(saves), 24)]
    return "\n".join(out) + "\n", windows


def energy_results(log: str, windows: list[dict], supply: float) -> list[dict]:
    """One row per measured window pass (windows over the same span in the same pass share one row)."""
    def num(text: str | None) -> float | None:
        try:
            x = float(text)
        except (TypeError, ValueError):
            return None
        return x if math.isfinite(x) else None

    spans: dict[tuple, dict] = {}
    current: dict[str, str | None] = {}
    for line in log.splitlines():
        words = line.split()
        if len(words) < 3 or words[0] != "@@E" or not words[1].isdigit() or int(words[1]) >= len(windows):
            continue
        k, key = int(words[1]), words[2]
        if key != "end":
            current[key] = words[3] if len(words) > 3 else None
            continue
        w, vals, current = windows[k], current, {}
        if num(vals.get("ok")) is None:
            continue  # the window's own measurement failed; its check fails on that already
        ea, eb = num(vals.get("ea")), num(vals.get("eb"))
        supplied = {key[4:]: num(v) for key, v in vals.items() if key.startswith("src:")}
        span = (w["from"], w["to"], ea, eb)
        row = spans.setdefault(span, {"window": w["name"], "from": w["from"], "to": w["to"], "measures": [],
                                      "ea": ea, "eb": eb, "supplied": supplied,
                                      "ta": num(vals.get("ta")), "tb": num(vals.get("tb"))})
        row["er"] = num(vals.get("er"))
        if "mr" in vals:
            row["mr"] = num(vals.get("mr"))
        if w["name"] not in row["window"].split(", "):
            row["window"] += f", {w['name']}"
        row["measures"] = sorted(set(row["measures"]) | set(w["measures"]))
    rows = []
    for row in spans.values():
        if None in (row["ea"], row["eb"], row["er"], row["ta"], row["tb"], row.get("mr", 0), *row["supplied"].values()):
            row["error"] = "stored energy could not be measured"
        else:
            row["release"] = row.pop("er")
            if "mr" in row:
                row["release_mos"] = row.pop("mr")
            row["total"] = sum(row["supplied"].values())
            row["seconds"] = max(0.0, row["tb"] - row["ta"])
            row["floor"] = ENERGY_FLOOR + ENERGY_FLOOR_CURRENT * abs(supply) * row["seconds"]
            row["allowed"] = ENERGY_FRACTION * row["total"] + row["floor"]
            row["ratio"] = round(row["release"] / row["allowed"], 4)
        rows.append(row)
    return rows


def energy_violations(runs: list[dict]) -> list[str]:
    out = []
    for r in runs:
        for e in r.get("energy", []):
            where = f"bench {r['bench']} at {r['label']}, window {e['window']} ({e['from']} .. {e['to']})"
            if "error" in e:
                out.append(f"stored energy: {where}: {e['error']} (energy accounting needs plain capacitor, inductor and node names)")
            elif e.get("ratio", 0) > 1:
                out.append(
                    f"stored energy: {where}: capacitors, inductors and MOS capacitors inside the circuit "
                    f"released {e['release'] * 1e15:.4g} fJ (MOS {e.get('release_mos', 0) * 1e15:.4g} fJ), more than "
                    f"{ENERGY_FRACTION * 100:g} % of the {e['total'] * 1e15:.4g} fJ the bench's sources delivered plus "
                    f"{e['floor'] * 1e15:.3g} fJ, counting only the drop below the lowest stored energy since the start "
                    "of the run; the circuit ran on charge stored at the operating point")
    return out


RUN_MEMORY = 1.25 * 2**30  # peak RSS of the largest benches is about 1.1 GB


def memory_job_cap() -> int:
    """Parallel runs that fit the container's memory limit (cgroup v2 or v1); no limit found means no cap."""
    for path in ("/sys/fs/cgroup/memory.max", "/sys/fs/cgroup/memory/memory.limit_in_bytes"):
        try:
            raw = Path(path).read_text().strip()
        except OSError:
            continue
        if raw.isdigit() and int(raw) < 2**60:
            return max(1, int(int(raw) // RUN_MEMORY))
    return 1 << 30


def run_one(bench: dict, point: dict, benches: Path, circuit: Path, keep: Path | None) -> dict:
    started = time.time()
    work = Path(tempfile.mkdtemp(prefix="gdb-"))
    try:
        shutil.copytree(benches, work, dirs_exist_ok=True)
        (work / ".spiceinit").write_text(SPICEINIT)  # parallelism comes from --jobs
        deck = work / f"run_{bench['file']}"
        circuit_text = circuit.read_text(errors="replace")
        included = circuit
        if (expanded := with_expanded_multipliers(circuit_text)) is not None:
            circuit_text = expanded
            included = work / "circuit_expanded.spi"
            included.write_text(expanded)
        if (drawn := with_diffusion_geometry(circuit_text)) is not None:
            circuit_text = drawn
            included = work / "circuit_geometry.spi"
            included.write_text(drawn)
        if (counted := with_mismatch_mult(circuit_text)) is not None:
            circuit_text = counted
            included = work / "circuit_mult.spi"
            included.write_text(counted)
        if (with_tc := with_xhigh_po_tc(circuit_text)) is not None:
            included = work / "circuit_xhigh_po_tc.spi"
            included.write_text(with_tc)
        text = render((benches / bench["file"]).read_text(), point, included)
        devices = rated_devices(circuit_text, text, bench.get("_top", ""))
        storage = dut_storage(circuit_text, text, bench.get("_top", ""))
        deck_text, windows = with_energy(with_ratings(text, devices), storage, bench.get("energy_windows", []))

        def simulate(text: str) -> tuple[str, str | None]:
            deck.write_text(text)
            try:
                proc = subprocess.run(
                    ["ngspice", "-b", deck.name],
                    cwd=work,
                    capture_output=True,
                    text=True,
                    timeout=bench.get("timeout_sec", 300),
                    env={**os.environ, "OMP_NUM_THREADS": "1"},
                )
            except subprocess.TimeoutExpired as e:
                out = (e.stdout or b"").decode(errors="replace") if isinstance(e.stdout, bytes) else (e.stdout or "")
                return out, f"timeout after {bench.get('timeout_sec', 300)} s"
            out = proc.stdout + proc.stderr
            if proc.returncode != 0:
                why = first_error(out) if proc.returncode > 0 else None
                return out, f"ngspice exit {proc.returncode}" + (f": {why}" if why else "")
            if bad := model_failure(out):
                return out, bad
            if m := FATAL_RE.search(out):
                return out, m.group(0)
            return out, rating_failure(out)

        log, failure = simulate(deck_text)
        retried = None
        # (not a run the kernel killed, exit -9: main reruns those one at a time)
        stalled = failure is not None and not failure.startswith("ngspice exit -") and bool(
            CONVERGENCE_RE.search(failure) or (failure.startswith("ngspice exit") and CONVERGENCE_RE.search(log)))
        if stalled or (not failure and OP_FALLBACK_RE.search(log)):
            for option in RETRY_OPTIONS:
                if "gear" in option and not re.search(r"timestep too small", log, re.I):
                    continue
                again, why = simulate(with_option(deck_text, option))
                if not why and not OP_FALLBACK_RE.search(again):
                    log, failure, retried = again, None, option
                    break
        values = {}
        for line in log.splitlines():
            m = MEASURE_RE.match(line.strip())
            if not m:
                continue
            try:
                v = float(m.group(2))
            except ValueError:
                v = None
            values[m.group(1)] = v if v is not None and math.isfinite(v) else None
        if keep is not None:
            dest = keep / bench["id"]
            dest.mkdir(parents=True, exist_ok=True)
            (dest / f"{label(point)}.log").write_text(log[-200000:])
        return {
            "bench": bench["id"],
            "point": point,
            "label": label(point),
            "failure": failure,
            "values": {} if failure else values,
            "energy": [] if failure else energy_results(log, windows, point["supply"]),
            "retried": retried,
            "op_fallback": not failure and bool(OP_FALLBACK_RE.search(log)),
            "seconds": round(time.time() - started, 2),
        }
    finally:
        shutil.rmtree(work, ignore_errors=True)


def with_option(deck_text: str, option: str) -> str:
    lines = deck_text.splitlines()
    return "\n".join(lines[:1] + [option] + lines[1:]) + "\n"  # line 1 is the deck title


def matches(point: dict, flt: dict | None) -> bool:
    if not flt:
        return True
    if "corners" in flt and point["corner"] not in flt["corners"]:
        return False
    if "supplies" in flt and not any(math.isclose(point["supply"], float(s)) for s in flt["supplies"]):
        return False
    if "temperatures" in flt and not any(math.isclose(point["temperature"], float(t)) for t in flt["temperatures"]):
        return False
    return True


def fmt(v) -> str:
    return "missing" if v is None else f"{v:.6g}"


def evaluate(check: dict, runs: list[dict], spec: dict) -> dict:
    rows = [r for r in runs if r["bench"] == check["bench"] and matches(r["point"], check.get("points"))]
    if check.get("agg") == "nominal":
        rows = [r for r in rows if is_nominal(r["point"], spec)]
    values = {r["label"]: (None if r["failure"] else r["values"].get(check["measure"])) for r in rows}
    result = {
        "id": check["id"],
        "label": check.get("label", check["id"]),
        "unit": check.get("unit", ""),
        "bench": check["bench"],
        "measure": check["measure"],
        "op": check["op"],
        "limit": check["limit"],
        "points": len(values),
        "value": None,
        "worst_point": None,
        "status": "failed",
        "message": "",
    }
    if not values:
        result["message"] = "no completed points"
        return result
    missing = [k for k, v in values.items() if v is None]
    if missing:
        failures = {r["label"]: r["failure"] for r in rows if r["failure"]}
        reason = "; ".join(f"{k}: {failures.get(k, 'measurement missing')}" for k in missing[:4])
        result["message"] = f"{len(missing)}/{len(values)} points without a value ({reason})"
        return result
    op, limit, agg = check["op"], check["limit"], check.get("agg", "worst")
    lo_point = min(values, key=values.get)
    hi_point = max(values, key=values.get)
    if op == "between":
        lo, hi = limit
        ok_lo, ok_hi = values[lo_point] >= lo, values[hi_point] <= hi
        result.update(
            value=[values[lo_point], values[hi_point]],
            worst_point=lo_point if not ok_lo else hi_point,
            status="passed" if ok_lo and ok_hi else "failed",
            message=f"range {fmt(values[lo_point])} ({lo_point}) .. {fmt(values[hi_point])} ({hi_point}); limit {lo:g}..{hi:g}",
        )
        return result
    if op not in OPS:
        raise SpecError(f"check {check['id']}: unknown op {op}")
    if agg in ("worst", "nominal"):
        point = lo_point if op in (">=", ">") else hi_point
        value = values[point]
    elif agg == "min":
        point, value = lo_point, values[lo_point]
    elif agg == "max":
        point, value = hi_point, values[hi_point]
    elif agg == "mean":
        point, value = None, sum(values.values()) / len(values)
    elif agg == "span":
        point, value = f"{lo_point}..{hi_point}", values[hi_point] - values[lo_point]
    else:
        raise SpecError(f"check {check['id']}: unknown agg {agg}")
    passed = OPS[op](value, limit)
    result.update(
        value=value,
        worst_point=point,
        status="passed" if passed else "failed",
        message=f"{agg} {fmt(value)}{(' at ' + point) if point else ''}; limit {op} {limit:g}",
    )
    return result


def report(args, spec: dict, results: list[dict], plan: list, runs: list[dict], blocked: int, started: float,
           violations: list[str] | None = None) -> int:
    passed = sum(r["status"] == "passed" for r in results)
    reward = 1 if passed == len(results) and not blocked else 0
    summary = {
        "task_id": spec.get("task_id"),
        "mode": "nominal-only" if args.nominal_only else "full",
        "reward": reward,
        "tests_total": len(results),
        "tests_passed": passed,
        "partial": round(passed / len(results), 4) if results else 0.0,
        "planned_runs": len(plan),
        "completed_runs": len(runs),
        "blocked_runs": blocked,
        "failed_runs": [{"bench": r["bench"], "point": r["label"], "failure": r["failure"]} for r in runs if r["failure"]],
        "rule_violations": violations or [],
        # per run, the supply window whose stored-energy release came closest to its allowance
        "stored_energy": [
            {"bench": r["bench"], "point": r["label"], **max(r["energy"], key=lambda e: e.get("ratio", math.inf))}
            for r in runs if r.get("energy")],
        # runs graded from a solver retry, and runs whose DC operating point came from ngspice's transient-op fallback
        "solver_retries": [{"bench": r["bench"], "point": r["label"], "option": r["retried"]} for r in runs if r.get("retried")],
        "op_fallback_runs": [{"bench": r["bench"], "point": r["label"]} for r in runs if r.get("op_fallback")],
        "seconds": round(time.time() - started, 1),
        "checks": results,
    }

    width = max(len(r["label"]) for r in results) if results else 10
    for r in results:
        mark = "PASS" if r["status"] == "passed" else "FAIL"
        print(f"{mark}  {r['label']:<{width}}  {r['message']}")
    if summary["op_fallback_runs"]:
        where = ", ".join(f"{r['bench']} {r['point']}" for r in summary["op_fallback_runs"][:5])
        print(f"NOTE  ngspice found the DC operating point only through its transient-op fallback in "
              f"{len(summary['op_fallback_runs'])} run(s) ({where}); those values may not be at the DC solution")
    print(f"{summary['mode']}: {passed}/{len(results)} checks passed, {len(runs)}/{len(plan)} runs, {summary['seconds']} s, score {reward}")

    if args.out:
        args.out.mkdir(parents=True, exist_ok=True)
        (args.out / "signoff.json").write_text(json.dumps(summary, indent=1) + "\n")
        (args.out / "reward.json").write_text(json.dumps({k: summary[k] for k in ("reward", "tests_total", "tests_passed", "partial")}) + "\n")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--spec", type=Path, default=HERE / "spec.json")
    ap.add_argument("--benches", type=Path, default=HERE / "benches")
    ap.add_argument("--circuit", type=Path, default=Path("/app/circuit.spi"))
    ap.add_argument("--out", type=Path, help="write reward.json and signoff.json here")
    ap.add_argument("--keep-logs", type=Path, help="keep every ngspice log here")
    ap.add_argument("--nominal-only", action="store_true")
    ap.add_argument("--jobs", type=int, default=int(os.environ.get("GDB_JOBS", "8")))
    args = ap.parse_args()

    spec = json.loads(args.spec.read_text())
    checks = spec["checks"]
    circuit = args.circuit.resolve()
    for b in spec["benches"]:
        b["_top"] = spec["subckt"]["name"]
    benches = {b["id"]: b for b in spec["benches"]}
    plan = []
    for b in spec["benches"]:
        pts = expand(spec, b.get("conditions", "pvt"))
        if args.nominal_only:
            pts = [dict(spec["nominal"])]
        plan += [(b, p) for p in pts]
    for c in checks:
        if c["bench"] not in benches:
            raise SpecError(f"check {c['id']} names unknown bench {c['bench']}")

    started = time.time()
    try:
        circuit_text = circuit.read_text(errors="replace")
    except OSError:
        circuit_text = ""
    def rule_failure(violations: list[str], runs: list[dict], blocked: int, kind: str) -> int:
        # A rule failure fails every check, like a failed legality check.
        first = violations[0] + (f" (+{len(violations) - 1} more)" if len(violations) > 1 else "")
        results = [{
            "id": c["id"], "label": c.get("label", c["id"]), "unit": c.get("unit", ""), "bench": c["bench"],
            "measure": c["measure"], "op": c["op"], "limit": c["limit"], "points": 0, "value": None,
            "worst_point": None, "status": "failed", "message": f"{kind}: {first}",
        } for c in checks]
        for v in violations:
            print(f"RULE  {v}")
        return report(args, spec, results, plan, runs, blocked, started, violations)

    violations = netlist_violations(circuit_text, spec)
    if violations:
        return rule_failure(violations, [], len(plan), "netlist rule")

    gate = [(b, p) for b, p in plan if is_nominal(p, spec)]
    rest = [(b, p) for b, p in plan if not is_nominal(p, spec)]
    runs: list[dict] = []

    jobs = min(max(1, args.jobs), memory_job_cap())
    # The whole signoff grid's corners and temperatures, in the nominal preflight too.
    grid = sorted({(p["corner"], float(p["temperature"])) for b in spec["benches"]
                   for p in expand(spec, b.get("conditions", "pvt"))})
    checked = with_expanded_multipliers(circuit_text) or circuit_text
    if invalid := device_check(with_diffusion_geometry(checked) or checked, spec, args.benches, grid, jobs):
        return rule_failure(invalid, [], len(plan), "device check")

    def execute(items):
        with futures.ThreadPoolExecutor(max_workers=jobs) as pool:
            done = list(pool.map(lambda bp: run_one(bp[0], bp[1], args.benches, circuit, args.keep_logs), items))
        # A run killed by the kernel (exit -9) almost always ran out of the container's memory next to its siblings;
        # rerun those one at a time before counting them as failures.
        for i, (r, bp) in enumerate(zip(done, items)):
            if r["failure"] == "ngspice exit -9":
                done[i] = run_one(bp[0], bp[1], args.benches, circuit, args.keep_logs)
        return done

    def covered(c, rs):
        return any(r["bench"] == c["bench"] and matches(r["point"], c.get("points")) for r in rs)

    runs += execute(gate)
    if stored := energy_violations(runs):
        return rule_failure(stored, runs, len(rest), "energy rule")
    gate_results = [evaluate(c, runs, spec) for c in checks if covered(c, runs)]
    gate_failed = [g["id"] for g in gate_results if g["status"] != "passed"]
    blocked = 0
    if gate_failed and rest:
        blocked = len(rest)
    else:
        runs += execute(rest)
        if stored := energy_violations(runs):
            return rule_failure(stored, runs, 0, "energy rule")

    results = []
    for c in checks:
        if args.nominal_only and not covered(c, runs):
            continue
        r = evaluate(c, runs, spec)
        if blocked and c["id"] not in gate_failed:
            r["status"] = "failed"
            r["message"] = f"blocked: nominal gate failed ({', '.join(gate_failed)}); " + r["message"]
        results.append(r)
    return report(args, spec, results, plan, runs, blocked, started)

if __name__ == "__main__":
    try:
        sys.exit(main())
    except SpecError as e:
        print(f"spec error: {e}", file=sys.stderr)
        sys.exit(2)
