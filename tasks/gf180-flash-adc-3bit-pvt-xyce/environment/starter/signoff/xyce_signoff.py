"""Running Xyce under this task's GF180MCU signoff rules (/app/spec/signoff-rules.md).

`run(deck)` simulates one bench deck the way grading does:

- the circuit under test is every `.include` outside the PDK (/opt/gf180), the grader's own files and the public
  signoff folder. It must pass check_circuit.py. It is simulated as rewritten here: `m=K` on a local subcircuit
  instance becomes K parallel copies, and every MOS gets the GF180MCU xschem diffusion geometry
  (`ad = as = w * 0.18u`, `pd = ps = 2 * (w + 0.18u)`; the netlist may not set it);
- Xyce runs once in a fresh folder and writes every node voltage and source current to a binary raw file. A run that
  fails runs once more with gear integration;
- from the raw file: no MOS may see more than 4.0 V gate-source, gate-drain or drain-source, or 5.5 V gate-bulk, at
  any time point; and no source-bulk or drain-bulk junction may be forward biased by more than 0.4 V at the quiet
  instant after the supplies have ramped up and before the first clock edge (JUNCTION_TIME).

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
RATINGS = {"vgs": 4.0, "vgd": 4.0, "vds": 4.0, "vgb": 5.5}  # 3.63 V maximum operating voltage + 10 %
JUNCTION_FORWARD_MAX = 0.4
JUNCTION_TIME = 9e-9  # after the supplies ramp up (0-5 ns), before the first clock edge (10 ns)


# ---- circuit rewrite ------------------------------------------------------------------------------------------------

def _params(words: list[str]) -> dict[str, str]:
    return {w.split("=", 1)[0].lower(): w.split("=", 1)[1] for w in words if "=" in w}


def rewrite(circuit_text: str) -> str:
    """The circuit as simulated: local-subcircuit and R/C multipliers expanded, MOS diffusion geometry filled in.
    The text has passed check_circuit.py, so X, R and C take no parameter but m and MOS none but w, l and m."""
    out = []
    for _, line in cc.logical_lines(circuit_text):
        words = line.split()
        kind = words[0][0].lower()
        params = _params(words)
        plain = [w for w in words if "=" not in w]
        if kind == "x" and "m" in params:
            out += [" ".join([f"{plain[0]}_m{i}"] + plain[1:]) for i in range(1, int(cc.spice_number(params["m"])) + 1)]
        elif kind in "rc" and "m" in params:
            k, value = cc.spice_number(params["m"]), cc.spice_number(plain[3])
            out.append(" ".join(plain[:3] + [f"{value / k if kind == 'r' else value * k:.9g}"]))
        elif kind == "m":
            w = cc.spice_number(params["w"])
            area, perimeter = w * DIFFUSION_M, 2 * (w + DIFFUSION_M)
            out.append(" ".join(words + [f"{k}={v:.6g}" for k, v in
                                         (("ad", area), ("as", area), ("pd", perimeter), ("ps", perimeter))]))
        else:
            out.append(line)
    return "\n".join(out) + "\n"


# ---- raw file -------------------------------------------------------------------------------------------------------

def read_raw(path: Path) -> tuple[list[str], np.ndarray]:
    """Variables (upper case, as Xyce names them) and points of a binary SPICE raw file."""
    blob = path.read_bytes()
    marker = blob.find(b"Binary:\n")
    head = blob[:marker].decode(errors="replace")
    nvars = int(re.search(r"No\. Variables:\s*(\d+)", head).group(1))
    npoints = int(re.search(r"No\. Points:\s*(\d+)", head).group(1))
    names = []
    for line in head.split("Variables:", 2)[-1].splitlines():
        parts = line.split()
        if len(parts) >= 3 and parts[0].isdigit():
            names.append(parts[1].upper())
    data = np.frombuffer(blob[marker + 8:], dtype="<f8")
    npoints = min(npoints, data.size // nvars)
    return names[:nvars], data[: npoints * nvars].reshape(npoints, nvars)


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


def flat_mos(circuit_text: str, deck_text: str) -> list[dict]:
    """Every MOS inside every top-level instance of a circuit subcircuit, with Xyce's hierarchical names
    (XADC:XC1:TAIL); "0" is Xyce's only global ground."""
    subckts = _subckts(circuit_text)
    mos = []

    def walk(name, prefix, nodemap, depth):
        if depth > 32:
            raise ValueError("subcircuit nesting deeper than 32 levels")

        def node(n):
            n = n.lower()
            return "0" if n == "0" else nodemap.get(n, f"{prefix}{n}".upper())

        for words in subckts[name]["lines"]:
            kind = words[0][0].lower()
            plain = [w for w in words if "=" not in w]
            path = f"{prefix}{words[0]}".upper()
            if kind == "x":
                target = plain[-1].lower()
                walk(target, f"{path}:", {p: node(n) for p, n in zip(subckts[target]["pins"], plain[1:-1])},
                     depth + 1)
            elif kind == "m":
                mos.append({"path": path, "model": plain[5].lower(), "nodes": [node(n) for n in plain[1:5]]})

    in_sub = False
    for _, line in cc.logical_lines(deck_text):
        words = line.split()
        head = words[0].lower()
        if head == ".subckt":
            in_sub = True
        elif head == ".ends":
            in_sub = False
        elif not in_sub and head[0] == "x":
            plain = [w for w in words if "=" not in w]
            target = plain[-1].lower()
            if target in subckts:
                nodemap = {p: ("0" if n == "0" else n.upper()) for p, n in zip(subckts[target]["pins"], plain[1:-1])}
                walk(target, f"{words[0]}:".upper(), nodemap, 0)
    return mos


# ---- rules ----------------------------------------------------------------------------------------------------------

class Waves:
    """Node voltages and source currents of one raw file. Xyce writes some hierarchical names with a '#' in place of
    a '0' (XADC:X00:TAIL as XADC:X#0:TAIL, XADC:X10:N[0] as XADC:X1#0:N[0]); check_circuit.py refuses '#' in names, so
    every '#' in a voltage name is a '0' and is turned back here. Branch currents keep their '#branch'."""

    def __init__(self, names, data):
        keys = [n if "#BRANCH" in n else n.replace("#", "0") for n in names]
        if len(set(keys)) != len(keys):
            raise ValueError("the raw file names a variable twice")
        self.index = {k: i for i, k in enumerate(keys)}
        self.data = data
        self.t = data[:, 0]

    def v(self, node):
        if node == "0":
            return np.zeros_like(self.t)
        if node not in self.index:
            raise KeyError(f"node {node} not in the raw file")
        return self.data[:, self.index[node]]


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
            quiet = float(np.interp(JUNCTION_TIME, t, diff))
            if sign * quiet > JUNCTION_FORWARD_MAX:
                return (f"junction forward biased before the first clock edge: {dev['path']} ({dev['model']}) "
                        f"{key}={quiet:.3f} V at t={JUNCTION_TIME:.3g} s (forward {sign * quiet:.3f} V > "
                        f"{JUNCTION_FORWARD_MAX:g} V)")
    return None


# ---- running --------------------------------------------------------------------------------------------------------

def _error_line(log: str) -> str:
    dc = [ln.strip() for ln in log.splitlines() if re.search(r"DC path to ground", ln, re.I)]
    if dc:  # a node with no DC path to ground: Xyce names it in this warning before it aborts
        return dc[0][:200]
    lines = [ln.strip() for ln in log.splitlines()
             if re.search(r"error|fatal|abort|too small|failed to|unable", ln, re.I)
             and not re.search(r"failed steps|failed linear|convergence failures|steps attempted", ln, re.I)]
    return (lines[-1] if lines else log.strip().splitlines()[-1] if log.strip() else "no output")[:200]


def run(deck: Path, keep: Path | None = None) -> dict:
    """Simulate `deck` under the signoff rules; see the module docstring."""
    deck = Path(deck).resolve()
    lines = deck.read_text(errors="replace").splitlines()
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
        result["rule"] = rating_violation(waves, flat_mos("\n".join(circuit_parts), rewritten))
    except (KeyError, ValueError) as error:
        result["rule"] = f"signoff accounting failed: {error}"
    return result
