"""Running Xyce under this task's GF180MCU signoff rules (/app/spec/signoff-rules.md).

`run(deck, windows)` simulates one bench deck the way grading does:

- the circuit under test is every `.include` outside the PDK (/opt/gf180), the grader's own files and the public
  signoff folder. It must pass check_circuit.py. It is simulated as rewritten here: `m=K` on a local subcircuit
  instance becomes K parallel copies, and every MOS that leaves out its diffusion geometry gets the GF180MCU xschem
  values (`ad = as = w * 0.18u`, `pd = ps = 2 * (w + 0.18u)`);
- Xyce runs once in a fresh folder and writes every node voltage and source current to a binary raw file. A run that
  fails runs once more with gear integration;
- from the raw file: no MOS may see more than 4.4 V gate-source, gate-drain or drain-source, or 5.5 V gate-bulk, at
  any time point; no source-bulk or drain-bulk junction may be forward biased by more than 0.4 V at the operating
  point (t = 0); and in every power window the energy stored in the circuit's capacitors (ideal C, plus the oxide
  capacitance of every MOS whose channel carries no current) may not end the window more than 5 % of the energy the
  bench's sources deliver in it (plus 20 nA x supply x window length, plus 0.5 fJ) below the lowest level it had
  since the start of the run.

The result is a dict: `names` and `data` (the raw file's variables and points), `rule` (the first broken rule, or
None), `error` (the run did not complete), and `log`. Standard library and NumPy.
"""
from __future__ import annotations

import os
import re
import subprocess
import tempfile
from pathlib import Path

import numpy as np

import check_circuit as cc

XYCE = os.environ.get("XYCE", "Xyce")
TIMEOUT = int(os.environ.get("XYCE_TIMEOUT", "1800"))
NOT_THE_CIRCUIT = ["/opt/gf180/", "/app/analog_arena_tests/"]
INCLUDE_RE = re.compile(r'^(\s*\.inc(?:lude)?\s+)("?)([^"\s]+)("?)\s*$', re.I)
RETRY_OPTION = ".options timeint method=gear"

DIFFUSION_M = 0.18e-6
RATINGS = {"vgs": 4.4, "vgd": 4.4, "vds": 4.4, "vgb": 5.5}
JUNCTION_FORWARD_MAX = 0.4
COX = 3.9 * 8.854e-12 / 8e-9  # F/m^2: the 3.3 V devices' toxe = 8 nm
ENERGY_FRACTION = 0.05
ENERGY_FLOOR_CURRENT = 20e-9
ENERGY_FLOOR = 0.5e-15
GROUND = {"0", "gnd"}


# ---- circuit rewrite ----------------------------------------------------------------------------------------------

def _params(words: list[str]) -> dict[str, str]:
    return {w.split("=", 1)[0].lower(): w.split("=", 1)[1] for w in words if "=" in w}


def rewrite(circuit_text: str) -> str:
    """The circuit as simulated: local-subcircuit multipliers expanded, MOS diffusion geometry filled in."""
    out = []
    for _, line in cc.logical_lines(circuit_text):
        words = line.split()
        kind = words[0][0].lower()
        params = _params(words)
        plain = [w for w in words if "=" not in w]
        if kind == "x" and "m" in params:
            k = int(cc.spice_number(params["m"]))
            others = [w for w in words[1:] if "=" in w and not w.lower().startswith("m=")]
            for i in range(1, k + 1):
                out.append(" ".join([f"{plain[0]}_m{i}"] + plain[1:] + others))
            continue
        if kind in "rc" and "m" in params:
            k, value = cc.spice_number(params["m"]), cc.spice_number(plain[3])
            out.append(" ".join(plain[:3] + [f"{value / k if kind == 'r' else value * k:.9g}"]))
            continue
        if kind == "m":
            w = cc.spice_number(params.get("w", ""))
            if w:
                geometry = {"ad": w * DIFFUSION_M, "as": w * DIFFUSION_M,
                            "pd": 2 * (w + DIFFUSION_M), "ps": 2 * (w + DIFFUSION_M)}
                line = " ".join([line] + [f"{k}={v:.6g}" for k, v in geometry.items() if k not in params])
        out.append(line)
    return "\n".join(out) + "\n"


# ---- raw file -------------------------------------------------------------------------------------------------------

def read_raw(path: Path) -> tuple[list[str], np.ndarray]:
    """Variables (upper case, as Xyce names them) and points of a binary or ASCII SPICE raw file."""
    blob = path.read_bytes()
    marker = blob.find(b"Binary:\n")
    ascii_marker = blob.find(b"Values:\n")
    head = blob[: marker if marker >= 0 else ascii_marker].decode(errors="replace")
    nvars = int(re.search(r"No\. Variables:\s*(\d+)", head).group(1))
    npoints = int(re.search(r"No\. Points:\s*(\d+)", head).group(1))
    names = []
    for line in head.split("Variables:", 2)[-1].splitlines():
        parts = line.split()
        if len(parts) >= 3 and parts[0].isdigit():
            names.append(parts[1].upper())
    if marker >= 0:
        data = np.frombuffer(blob[marker + 8:], dtype="<f8")
        npoints = min(npoints, data.size // nvars)
        data = data[: npoints * nvars].reshape(npoints, nvars)
    else:
        values = blob[ascii_marker + 8:].decode().split()
        rows = []
        i = 0
        while i + nvars < len(values) + 1 and len(rows) < npoints:
            rows.append([float(v) for v in values[i + 1:i + 1 + nvars]])
            i += nvars + 1
        data = np.array(rows)
    return names[:nvars], data


# ---- flattening -----------------------------------------------------------------------------------------------------

def _subckts(text: str) -> dict[str, dict]:
    found, current = {}, None
    for _, line in cc.logical_lines(text):
        words = line.split()
        head = words[0].lower()
        if head == ".subckt":
            current = words[1].lower()
            found[current] = {"pins": [w.lower() for w in words[2:]], "lines": []}
        elif head == ".ends":
            current = None
        elif current:
            found[current]["lines"].append(words)
    return found


def flatten(circuit_text: str, deck_text: str) -> dict:
    """MOS devices, capacitors and resistors inside every top-level instance of a circuit subcircuit, with Xyce's
    hierarchical node names (XADC:XC1:TAIL), and the set of nodes outside the circuit."""
    subckts = _subckts(circuit_text)
    mos, caps, res = [], [], []
    outside = set()

    def walk(name, prefix, nodemap, depth):
        if depth > 32:
            raise ValueError("subcircuit nesting deeper than 32 levels")

        def node(n):
            n = n.lower()
            return "0" if n in GROUND else nodemap.get(n, f"{prefix}{n}".upper())

        for words in subckts[name]["lines"]:
            kind = words[0][0].lower()
            plain = [w for w in words if "=" not in w]
            params = _params(words)
            path = f"{prefix}{words[0]}".upper()
            if kind == "x":
                target = plain[-1].lower()
                child = subckts[target]
                walk(target, f"{path}:", {p: node(n) for p, n in zip(child["pins"], plain[1:-1])}, depth + 1)
            elif kind == "m":
                w, l = cc.spice_number(params["w"]), cc.spice_number(params["l"])
                m = cc.spice_number(params.get("m", "1"))
                mos.append({"path": path, "model": plain[5].lower(), "nodes": [node(n) for n in plain[1:5]],
                            "area": w * l * m})
            elif kind == "c":
                caps.append({"path": path, "nodes": [node(n) for n in plain[1:3]],
                             "c": cc.spice_number(plain[3]) * cc.spice_number(params.get("m", "1"))})
            elif kind == "r":
                res.append([node(n) for n in plain[1:3]])

    in_sub = False
    for _, line in cc.logical_lines(deck_text):
        words = line.split()
        head = words[0].lower()
        if head == ".subckt":
            in_sub = True
        elif head == ".ends":
            in_sub = False
        elif not in_sub and not head.startswith("."):
            plain = [w for w in words if "=" not in w]
            for n in plain[1:3] if head[0] in "vrcli" else plain[1:-1] if head[0] == "x" else []:
                outside.add("0" if n.lower() in GROUND else n.upper())
            if head[0] == "x" and plain[-1].lower() in subckts:
                target = subckts[plain[-1].lower()]
                nodemap = {p: ("0" if n.lower() in GROUND else n.upper()) for p, n in zip(target["pins"], plain[1:-1])}
                walk(plain[-1].lower(), f"{words[0]}:".upper(), nodemap, 0)
    return {"mos": mos, "caps": caps, "res": res, "outside": outside}


def sources(deck_text: str) -> list[dict]:
    """Top-level voltage sources that can deliver energy (a 0 V source delivers none)."""
    found, in_sub = [], False
    for _, line in cc.logical_lines(deck_text):
        words = line.split()
        head = words[0].lower()
        if head == ".subckt":
            in_sub = True
        elif head == ".ends":
            in_sub = False
        elif not in_sub and head[0] == "v" and len(words) >= 4:
            if [w.lower() for w in words[3:]] in (["0"], ["dc", "0"]):
                continue
            dc = cc.spice_number(words[3]) if len(words) == 4 else None
            found.append({"name": words[0].upper(), "nodes": [n.upper() if n.lower() not in GROUND else "0"
                                                               for n in words[1:3]], "dc": dc})
    return found


# ---- rules ----------------------------------------------------------------------------------------------------------

class Waves:
    def __init__(self, names, data):
        self.index = {n: i for i, n in enumerate(names)}
        self.data = data
        self.t = data[:, 0]

    def v(self, node):
        if node == "0":
            return np.zeros_like(self.t)
        key = node.upper()
        if key not in self.index:
            raise KeyError(f"node {node} not in the raw file")
        return self.data[:, self.index[key]]

    def i(self, source):
        return self.data[:, self.index[f"{source.upper()}#BRANCH"]]


def _interp(t, y, at):
    return float(np.interp(at, t, y))


def _integral(t, y, a, b):
    """Integral of y over [a, b], linear between points."""
    inside = (t > a) & (t < b)
    tt = np.concatenate(([a], t[inside], [b]))
    yy = np.concatenate(([np.interp(a, t, y)], y[inside], [np.interp(b, t, y)]))
    return float(np.sum((yy[1:] + yy[:-1]) * np.diff(tt)) / 2)


def rating_violation(waves: Waves, mos: list[dict]) -> str | None:
    t = waves.t
    for dev in mos:
        d, g, s, b = (waves.v(n) for n in dev["nodes"])
        for key, diff in (("vgs", g - s), ("vgd", g - d), ("vds", d - s), ("vgb", g - b)):
            worst = int(np.argmax(np.abs(diff)))
            if abs(diff[worst]) > RATINGS[key]:
                return (f"device rating exceeded: {dev['path']} ({dev['model']}) |{key}|={abs(diff[worst]):.3f} V > "
                        f"{RATINGS[key]:g} V at t={t[worst]:.4g} s")
        sign = 1.0 if dev["model"].startswith("n") else -1.0
        for key, diff in (("vbs", b - s), ("vbd", b - d)):
            forward = sign * diff[0]
            if forward > JUNCTION_FORWARD_MAX:
                return (f"junction forward biased at the operating point: {dev['path']} ({dev['model']}) "
                        f"{key}={diff[0]:.3f} V (forward {forward:.3f} V > {JUNCTION_FORWARD_MAX:g} V)")
    return None


def stored_energy(waves: Waves, flat: dict) -> np.ndarray:
    energy = np.zeros_like(waves.t)
    for cap in flat["caps"]:
        a, b = (waves.v(n) for n in cap["nodes"])
        energy += 0.5 * cap["c"] * (a - b) ** 2
    parent = {}

    def find(n):
        while parent.get(n, n) != n:
            n = parent[n]
        return n

    touches = {}
    for a, b in flat["res"]:
        parent[find(a)] = find(b)
        for n in (a, b):
            touches[n] = touches.get(n, 0) + 1
    for dev in flat["mos"]:
        for n in dev["nodes"]:
            touches[n] = touches.get(n, 0) + 1
    for cap in flat["caps"]:
        for n in cap["nodes"]:
            touches[n] = touches.get(n, 0) + 1

    def dangling(n):
        return n not in flat["outside"] and touches.get(n, 0) <= 1

    for dev in flat["mos"]:
        d, g, s, b = dev["nodes"]
        # a MOS capacitor carries no channel current; its gate oxide stores 1/2 Cox W L (vg - vchannel)^2
        if d == s or dangling(d) or dangling(s) or g == s or find(d) == find(s):
            channel = s if not dangling(s) else d
            energy += 0.5 * COX * dev["area"] * (waves.v(g) - waves.v(channel)) ** 2
    return energy


def energy_violation(waves: Waves, flat: dict, srcs: list[dict], windows: list[tuple[float, float]]) -> str | None:
    t = waves.t
    energy = stored_energy(waves, flat)
    supply = max([abs(s["dc"]) for s in srcs if s["dc"] is not None] or [3.3])
    for a, b in windows:
        a, b = max(a, t[1]), min(b, t[-1])
        before = t <= a
        low = min(float(energy[before].min()) if before.any() else np.inf, _interp(t, energy, a))
        release = low - _interp(t, energy, b)
        delivered = 0.0
        for src in srcs:
            p = -(waves.v(src["nodes"][0]) - waves.v(src["nodes"][1])) * waves.i(src["name"])
            delivered += _integral(t, np.maximum(p, 0.0), a, b)
        allowed = ENERGY_FRACTION * delivered + ENERGY_FLOOR_CURRENT * supply * (b - a) + ENERGY_FLOOR
        if release > allowed:
            return (f"stored energy: window {a:.4g}..{b:.4g} s: capacitors inside the circuit released "
                    f"{release * 1e15:.4g} fJ, more than {ENERGY_FRACTION * 100:g} % of the {delivered * 1e15:.4g} fJ "
                    f"the bench's sources delivered plus {(allowed - ENERGY_FRACTION * delivered) * 1e15:.3g} fJ; the "
                    "circuit ran on charge stored at the operating point")
    return None


# ---- running --------------------------------------------------------------------------------------------------------

def _error_line(log: str) -> str:
    lines = [ln.strip() for ln in log.splitlines()
             if re.search(r"error|fatal|abort|too small|failed to|unable", ln, re.I)
             and not re.search(r"failed steps|failed linear|convergence failures|steps attempted", ln, re.I)]
    return (lines[-1] if lines else log.strip().splitlines()[-1] if log.strip() else "no output")[:200]


def run(deck: Path, windows: list[tuple[float, float]], keep: Path | None = None) -> dict:
    """Simulate `deck` under the signoff rules; see the module docstring."""
    deck = Path(deck).resolve()
    text = deck.read_text(errors="replace")
    lines = text.splitlines()
    circuit_parts = []
    with tempfile.TemporaryDirectory(prefix="xyce-") as tmp:
        work = keep or Path(tmp)
        work.mkdir(parents=True, exist_ok=True)
        for i, line in enumerate(lines):
            m = INCLUDE_RE.match(line)
            if not m:
                continue
            path = Path(m.group(3))
            path = path if path.is_absolute() else deck.parent / path
            if any(str(path.resolve()).startswith(p) for p in NOT_THE_CIRCUIT):
                continue
            try:
                included = path.read_text(errors="replace")
            except OSError as error:
                return {"error": f"cannot read {path}: {error}", "rule": None, "log": ""}
            found = cc.violations(included)
            if found:
                return {"rule": "netlist: " + "; ".join(found[:5]), "error": None, "log": ""}
            graded = rewrite(included)
            circuit_parts.append(graded)
            copy = work / f"circuit_{i}.spi"
            copy.write_text(graded)
            lines[i] = f'{m.group(1)}"{copy}"'
        rewritten = "\n".join(lines) + "\n"
        log = ""
        for attempt, option in enumerate((None, RETRY_OPTION)):
            body = rewritten if option is None else "\n".join(lines[:1] + [option] + lines[1:]) + "\n"
            run_deck = work / f"run{attempt}.cir"
            run_deck.write_text(body)
            raw = work / f"run{attempt}.raw"
            try:
                p = subprocess.run([XYCE, "-r", str(raw), str(run_deck)], cwd=work, capture_output=True, text=True,
                                   timeout=TIMEOUT)
                log = p.stdout + p.stderr
                ok = p.returncode == 0 and raw.exists() and raw.stat().st_size > 0
            except subprocess.TimeoutExpired:
                log, ok = f"Xyce timed out after {TIMEOUT} s", False
            if ok:
                break
        if not ok:
            return {"error": f"Xyce failed: {_error_line(log)}", "rule": None, "log": log}
        names, data = read_raw(raw)
    result = {"names": names, "data": data, "log": log, "error": None, "rule": None}
    if not circuit_parts:
        return result
    try:
        waves = Waves(names, data)
        flat = flatten("\n".join(circuit_parts), rewritten)
        result["rule"] = (rating_violation(waves, flat["mos"])
                          or energy_violation(waves, flat, sources(rewritten), windows))
    except (KeyError, ValueError) as error:
        result["rule"] = f"signoff accounting failed: {error}"
    return result
