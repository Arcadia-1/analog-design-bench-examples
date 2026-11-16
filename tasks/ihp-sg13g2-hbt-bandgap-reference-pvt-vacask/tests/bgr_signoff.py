"""Bench runner, signoff rules and metric definitions of the IHP SG13G2 bandgap task.

The hidden verifier and /app/signoff/run_bench.py import this same file, so a
public bench is simulated, checked and measured exactly the way grading does it.
"""
import os
import re
import shutil
import subprocess
import tempfile

import numpy as np

import check_circuit

SUBCKT = ("bandgap", "vdd", "vss", "en", "vref")
DUT = "xdut"
DUT_PORTS = {"vdd": "vdd", "vss": "0", "en": "en", "vref": "vref"}
RC_FILE = os.path.join(os.environ.get("IHP_PDK_ROOT", "/opt/ihp/pdk/ihp-sg13g2"), "libs.tech/vacask/.vacaskrc.toml")
TIMEOUT_S = 900
CORNERS = {  # mos, hbt, res, cap library sections
    "tt": ("mos_tt", "hbt_typ", "res_typ", "cap_typ"),
    "ss": ("mos_ss", "hbt_wcs", "res_wcs", "cap_wcs"),
    "ff": ("mos_ff", "hbt_bcs", "res_bcs", "cap_bcs"),
    "sf": ("mos_sf", "hbt_wcs", "res_bcs", "cap_typ"),
    "fs": ("mos_fs", "hbt_bcs", "res_wcs", "cap_typ"),
}
# Operating-point analyses of the benches; "tr" is the only transient.
OP_ANALYSES = ("dc", "load", "nslo", "nshi", "off", "target")
TRAN_ANALYSES = ("tr", "dis")
DISABLE_WINDOW = 2e-6

# Signoff limits (spec/signoff-rules.md).
# MOS: rated voltage + 10 % at DC operating points, a further 10 % during transients.
MOS_RATING = {"sg13_hv_nmos": 3.63, "sg13_hv_pmos": 3.63, "sg13_lv_nmos": 1.32, "sg13_lv_pmos": 1.32}
TRANSIENT_ALLOWANCE = 1.10
# HBT: the safe-operating-area parameters of the IHP SG13G2 ngspice model cards (vbe_max, vbc_max,
# vce_max), applied at every point.
HBT_SOA = {"npn13G2": (1.6, 5.1, 1.6), "npn13G2l": (1.6, 5.1, 1.6), "npn13G2v": (1.6, 7.0, 2.2)}
JUNCTION_FORWARD = 0.4
RULE_TOLERANCE = 1e-3

# Metric definitions (spec/README.md).
SETTLE_BAND = 5e-3
LOAD_CURRENT = 10e-6


class RunError(RuntimeError):
    pass


class Plot:
    """The vectors of one analysis: one row per sweep point, one column per saved quantity."""

    def __init__(self, names, data):
        self.data = data
        self.index = {n: i for i, n in enumerate(names)}

    def __contains__(self, name):
        return name == "0" or name in self.index

    def __getitem__(self, name):
        if name == "0":
            return np.zeros(self.data.shape[0])
        return self.data[:, self.index[name]]

    def real(self, name):
        return np.real(self[name])


def read_raw(path):
    """Read a VACASK binary raw file."""
    with open(path, "rb") as handle:
        blob = handle.read()
    header_end = blob.find(b"Binary:\n")
    if header_end < 0:
        raise RunError(f"{os.path.basename(path)}: not a binary raw file")
    fields, names, in_vars = {}, [], False
    for line in blob[:header_end].decode("ascii", "replace").splitlines():
        if in_vars:
            parts = line.split()
            if len(parts) >= 2 and parts[0].isdigit():
                names.append(parts[1])
                continue
            in_vars = False
        key, _, value = line.partition(":")
        if key.strip().lower() == "variables":
            in_vars = True
            continue
        fields[key.strip().lower()] = value.strip()
    count, points = int(fields["no. variables"]), int(fields["no. points"])
    dtype = np.complex128 if "complex" in fields.get("flags", "") else np.float64
    data = np.frombuffer(blob, dtype=dtype, count=count * points, offset=header_end + len(b"Binary:\n"))
    return Plot(names, data.reshape(points, count))


def run_deck(deck, circuit, keep=None):
    """Simulate `deck` (VACASK text that includes "circuit.sim") with a copy of `circuit`.

    Return {analysis name: Plot}. Raises RunError when VACASK fails.
    """
    work = tempfile.mkdtemp(prefix="bgr-")
    try:
        shutil.copy(circuit, os.path.join(work, "circuit.sim"))
        if os.path.exists(RC_FILE):
            shutil.copy(RC_FILE, os.path.join(work, ".vacaskrc.toml"))
        with open(os.path.join(work, "tb.sim"), "w") as handle:
            handle.write(deck)
        try:
            proc = subprocess.run(["vacask", "-qp", "tb.sim"], cwd=work, capture_output=True, text=True,
                                  timeout=TIMEOUT_S, env=dict(os.environ, OMP_NUM_THREADS="1"))
        except subprocess.TimeoutExpired as exc:
            raise RunError(f"VACASK timed out after {TIMEOUT_S} s") from exc
        plots = {name[:-4]: read_raw(os.path.join(work, name)) for name in os.listdir(work) if name.endswith(".raw")}
        missing = [a for a in re.findall(r"^\s*analysis\s+(\w+)", deck, flags=re.M) if a not in plots]
        if proc.returncode != 0 or missing:
            log = proc.stdout + proc.stderr
            tail = "\n".join(line for line in log.splitlines() if line.strip())[-1500:]
            raise RunError(f"VACASK failed (exit {proc.returncode}, missing {missing}):\n{tail}")
        return plots
    finally:
        if keep:
            shutil.copytree(work, keep, dirs_exist_ok=True)
        shutil.rmtree(work, ignore_errors=True)


def dut_devices(circuit):
    subckts, errors = check_circuit.check_file(circuit, list(SUBCKT))
    if errors:
        raise RunError("netlist check failed: " + "; ".join(errors))
    return check_circuit.flatten(subckts, SUBCKT[0], DUT, DUT_PORTS)


def rule_violations(devices, plot, label, dc):
    """Return the signoff rule violations of `devices` in every point of `plot`.

    `dc` marks operating-point results, where the junction rule applies and MOS ratings have no
    transient allowance.
    """
    found = []

    def report(device, what, values, limit):
        worst = int(np.argmax(values))
        if values[worst] > limit + RULE_TOLERANCE:
            found.append(f"{label}: {device}: {what} = {values[worst]:.3f} V exceeds {limit:.2f} V")

    for path, master, terminals, _ in devices:
        missing = [node for node in terminals.values() if node not in plot]
        if missing:
            raise RunError(f"node {missing[0]} is missing from the simulation results")
        v = {t: plot.real(node) for t, node in terminals.items()}
        if master in MOS_RATING:
            limit = MOS_RATING[master] * (1.0 if dc else TRANSIENT_ALLOWANCE)
            report(path, "|Vgs|", np.abs(v["g"] - v["s"]), limit)
            report(path, "|Vgd|", np.abs(v["g"] - v["d"]), limit)
            report(path, "|Vds|", np.abs(v["d"] - v["s"]), limit)
            report(path, "|Vgb|", np.abs(v["g"] - v["b"]), limit)
            if dc:
                sign = -1.0 if master.endswith("pmos") else 1.0
                report(path, "source-bulk forward bias", sign * (v["b"] - v["s"]), JUNCTION_FORWARD)
                report(path, "drain-bulk forward bias", sign * (v["b"] - v["d"]), JUNCTION_FORWARD)
        elif master in HBT_SOA:
            vbe_max, vbc_max, vce_max = HBT_SOA[master]
            report(path, "|Vbe|", np.abs(v["b"] - v["e"]), vbe_max)
            report(path, "|Vbc|", np.abs(v["b"] - v["c"]), vbc_max)
            report(path, "|Vce|", np.abs(v["c"] - v["e"]), vce_max)
    return found


def signoff_violations(devices, plots, prefix=""):
    """Rule violations over every operating-point and transient analysis of one run."""
    found = []
    for name, plot in plots.items():
        if name in OP_ANALYSES or name in TRAN_ANALYSES:
            found += rule_violations(devices, plot, prefix + name, dc=name not in TRAN_ANALYSES)
    return found


# ---- metrics ----------------------------------------------------------------

def pin_currents(plot):
    """Currents delivered by the supply and by the enable source (A), per point, each signed."""
    return -plot.real("vdd:flow(br)"), -plot.real("vensrc:flow(br)")


def keyed(plot, *sweeps):
    """Map each point of `plot` to a key of rounded sweep values."""
    columns = [np.round(plot.real(s), 9) for s in sweeps]
    return [tuple(float(c[i]) for c in columns) for i in range(plot.data.shape[0])]


def dc_metrics(plots):
    """Metrics of a DC bench run: analyses dc, load, off, nslo and nshi."""
    out = {}
    dc = plots["dc"]
    keys = keyed(dc, "vsup", "temp")
    vref = dc.real("vref")
    i_vdd, i_en = pin_currents(dc)
    out["vref"] = {k: float(v) for k, v in zip(keys, vref)}
    # Quiescent current: |current from the supply| + |current from the enable source|.
    out["iq"] = {k: float(i) for k, i in zip(keys, np.abs(i_vdd) + np.abs(i_en))}
    out["tc_ppm"] = {}
    for vs in sorted({k[0] for k in keys}):
        idx = [i for i, k in enumerate(keys) if k[0] == vs]
        temps = np.array([keys[i][1] for i in idx])
        values = vref[idx]
        span = temps.max() - temps.min()
        out["tc_ppm"][vs] = float((values.max() - values.min()) / np.mean(values) / span * 1e6) if span else 0.0
    out["line_mv_per_v"] = {}
    for t in sorted({k[1] for k in keys}):
        pts = sorted((k[0], vref[i]) for i, k in enumerate(keys) if k[1] == t)
        if len(pts) >= 2:
            lo, hi = pts[0], pts[-1]
            out["line_mv_per_v"][t] = float(abs(hi[1] - lo[1]) / (hi[0] - lo[0]) * 1e3)
    load = plots["load"]
    lkeys = keyed(load, "vsup", "temp", "iload")
    lv = load.real("vref")
    out["load_mv"] = {}
    for (vs, t, il), v in zip(lkeys, lv):
        if il == 0:
            other = [lv[j] for j, k in enumerate(lkeys) if k[:2] == (vs, t) and k[2] > 0]
            if other:
                out["load_mv"][(vs, t)] = float(max(abs(v - o) for o in other) * 1e3)
    off = plots["off"]
    i_vdd, i_en = pin_currents(off)
    # (current from the supply, current from the enable source, vref), each pin metered on its own
    out["off"] = {k: (float(a), float(b), float(v))
                  for k, a, b, v in zip(keyed(off, "vsup", "temp"), i_vdd, i_en, off.real("vref"))}
    lo, hi = plots["nslo"], plots["nshi"]
    out["nodeset_mv"] = {}
    for k, a, b in zip(keyed(lo, "vsup", "temp"), lo.real("vref"), hi.real("vref")):
        values = [a, b] + ([out["vref"][k]] if k in out["vref"] else [])
        out["nodeset_mv"][k] = float((max(values) - min(values)) * 1e3)
    return out


def ac_metrics(plots):
    """PSRR (dB) and output noise density (V/sqrt(Hz)) at 10 kHz, per (vsup, temp)."""
    ac, nz = plots["ac"], plots["nz"]
    psrr = {k: float(-20 * np.log10(max(abs(g), 1e-30))) for k, g in zip(keyed(ac, "vsup", "temp"), ac["vref"])}
    noise = {k: float(np.sqrt(max(np.real(s), 0.0))) for k, s in zip(keyed(nz, "vsup", "temp"), nz["onoise"])}
    return {"psrr_db": psrr, "noise": noise}


def startup_metrics(plots, t_end):
    """Settling time (s after t_end) to within SETTLE_BAND of the target operating point, and overshoot (V).

    The target is the operating point of the same circuit with the supply and enable at their final DC
    values (analysis "target" of the same bench), fixed before the transient starts.
    """
    tr = plots["tr"]
    t = tr.real("time")
    v = tr.real("vref")
    target = float(plots["target"].real("vref")[0])
    outside = np.where(np.abs(v - target) > SETTLE_BAND)[0]
    if not len(outside):
        settle = 0.0
    elif outside[-1] == len(t) - 1:
        settle = float("inf")
    else:
        settle = float(max(t[outside[-1]] - t_end, 0.0))
    after = t >= t_end
    overshoot = float(np.max(v[after] - target)) if after.any() else float("inf")
    return {"target": target, "settle_s": settle, "overshoot_v": overshoot}


# ---- bench text ---------------------------------------------------------------

def corner_tokens(corner):
    return dict(zip(("mos", "hbt", "res", "cap"), CORNERS[corner]))


def render(template, **values):
    """Replace __NAME__ tokens of a bench template."""
    def sub(match):
        key = match.group(1).lower()
        if key not in values:
            raise KeyError(f"bench template token {match.group(0)} has no value")
        return str(values[key])
    return re.sub(r"__([A-Z0-9_]+)__", sub, template)


def disable_metrics(plots):
    """Pin currents and vref over the last DISABLE_WINDOW of the disable transient (analysis "dis")."""
    tr = plots["dis"]
    t = tr.real("time")
    window = t >= t[-1] - DISABLE_WINDOW
    i_vdd, i_en = pin_currents(tr)
    return {"i_vdd": float(np.max(np.abs(i_vdd[window]))), "i_en": float(np.max(np.abs(i_en[window]))),
            "vref": float(np.max(tr.real("vref")[window])), "vref_before": float(np.interp(19e-6, t, tr.real("vref")))}


def edge_end(deck):
    """End time of the last supply or enable edge that a startup bench's alter lines program."""
    ends = []
    for line in re.findall(r'^\s*alter\s+instance\(.*$', deck, flags=re.M):
        rise = re.search(r"\brise=([0-9.eE+-]+[a-z]*)", line)
        delay = re.search(r"\bdelay=([0-9.eE+-]+[a-z]*)", line)
        if rise and delay:
            ends.append(check_circuit.parse_value(rise.group(1)) + check_circuit.parse_value(delay.group(1)))
    if not ends:
        raise RunError("the startup bench programs no pulse edge")
    return max(ends)
