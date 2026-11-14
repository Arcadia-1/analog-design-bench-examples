#!/usr/bin/env python3
"""Electrical signoff for the IHP SG13G2 tunable 28 Gb/s NRZ CTLE.

Every check comes from an AC analysis, a DC operating point or the disturbance transient; the definitions are in
/app/spec/README.md. Every ngspice run goes through ihp_signoff, so the task's IHP SG13G2 signoff rules apply.
Each check is scored on its own over every graded point: it fails only where its own measurements fail a limit or
could not be obtained.
"""

import json
import math
import re
import tempfile
import time
import traceback
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import ihp_signoff
from utils import write_results


HERE = Path(__file__).resolve().parent
OUTPUT = Path("/logs/verifier")
MODELS = "/opt/ihp/pdk/ihp-sg13g2/libs.tech/ngspice/models"
JOBS = 4  # the verifier container has 4 CPUs; each ngspice run is single-threaded

# (name, corner, supply, temperature, output load F). The nominal point runs first (for cost only).
LOAD_F = 200e-15
NOMINAL_PVT = ("tt_3.30v_27c", "tt", 3.30, 27, LOAD_F)
CORNER_PVT = tuple(
    (f"{corner}_{supply:.2f}v_{temperature}c", corner, supply, temperature, LOAD_F)
    for corner in ("tt", "ss", "ff", "sf", "fs")
    for supply in (2.97, 3.63)
    for temperature in (0, 125)
)
# Unpublished points of the declared output-load range (180-220 fF per output), at the nominal corner, supply and
# temperature.
LOAD_PVT = tuple((f"tt_3.30v_27c_{round(c * 1e15)}ff", "tt", 3.30, 27, c) for c in (186e-15, 217e-15))
PVT = (NOMINAL_PVT,) + CORNER_PVT + LOAD_PVT
# Library sections per corner: MOS (lv and hv, varactor), HBT, resistors, MIM capacitors.
CORNER_LIBS = {
    "tt": ("mos_tt", "hbt_typ", "res_typ", "cap_typ"),
    "ss": ("mos_ss", "hbt_wcs", "res_wcs", "cap_wcs"),
    "ff": ("mos_ff", "hbt_bcs", "res_bcs", "cap_bcs"),
    "sf": ("mos_sf", "hbt_typ", "res_typ", "cap_typ"),
    "fs": ("mos_fs", "hbt_typ", "res_typ", "cap_typ"),
}

CODES = tuple(
    (f"g{gb1}{gb0}_p{pb1}{pb0}", gb1, gb0, pb1, pb0)
    for gb1 in (0, 1) for gb0 in (0, 1)
    for pb1 in (0, 1) for pb0 in (0, 1)
)
ZIN_CODE = next(c for c in CODES if c[0] == "g00_p00")
# Disturbance transients: (code, load-return step V, differential pulse A, its time, common-mode pulse A per output,
# its time).
KICKS = (
    ("g00_p00", -0.1, -1e-3, "1.5u", -1e-3, "0.6u"),
    ("g11_p11", -0.1, 1e-3, "2u", 1e-3, "0.4u"),
)

CODE_ORDER = ("00", "01", "10", "11")
GAIN_TOLERANCE_DB = 1.5
PEAK_TOLERANCE_DB = 1.5
GAIN_TARGET_DB = {"00": -12.0, "01": -10.0, "10": -8.0, "11": -6.0}
PEAK_TARGET_DB = {"00": 0.0, "01": 2.0, "10": 4.0, "11": 6.0}
MIN_CODE_STEP_DB = 1.0
INBAND_EXCESS_MAX_DB = 1.0
INBAND_DROOP_MAX_DB = 3.0
FORWARD_REVERSE_MIN_DB = 15.0
ABOVE_NYQUIST_EXCESS_MAX_DB = 3.0
BW_FLOOR_HZ = 25.0e9
LOW_CORNER_MAX_HZ = 20e3
LOW_CORNER_FLOOR_DB = 3.0
ZIN_TARGET_OHM = 50.0
ZIN_TOLERANCE_OHM = 10.0  # +-20 %
RETURN_LOSS_MAX_DB = -15.0
CM_ERROR_MAX_V = 0.075
OFFSET_MAX_V = 0.005
POWER_MAX_W = 0.060
VOCM_PIN_MAX_A = 100e-6  # vocm is a reference, not a supply: DC current into or out of the pin
DEADLINE_S = 3000  # verifier budget (the Harbor verifier timeout is 3600 s); later runs score as missing
SWING_MARGIN_V = 0.25
VOCM_TRACK_MIN_V = 0.009  # output common-mode change for the bench's +10 mV vocm step
CM_KICK_PP_MAX_V = 1e-3
DM_KICK_PP_MAX_V = 1e-3
DM_KICK_ABS_MAX_V = OFFSET_MAX_V
ROUNDOFF = 1e-12  # absorbs only floating-point roundoff of derived sums and differences

METRICS = (
    "output_common_mode_v", "output_diff_v", "output_offset_v", "vocm_pin_v", "vocm_pin_i", "power_w",
    "other_source_power_w",
    "voutp_v", "voutn_v", "vocm_step_cm_v",
    "g_lf_db", "g_lf_re", "g_14_db", "inband_peak_db", "inband_min_db", "hf_peak_db", "gain_at_sweep_stop",
    "gain_start_db", "sdd11_max_db", "fwd_rev_min_db",
)
OPTIONAL_METRICS = ("bw_hz", "low_bw_hz")
ZIN_METRICS = {"p": ("zinp_1m", "s11p_max_db"), "n": ("zinn_1m", "s11n_max_db")}
KICK_METRICS = ("cm_kick_max_v", "cm_kick_min_v", "dm_kick_max_v", "dm_kick_min_v")
CHECK_NAMES = (
    "gain_code_table",
    "peak_code_table",
    "output_polarity",
    "inband_flatness",
    "above_nyquist_peaking",
    "bandwidth_floor",
    "forward_reverse_ratio",
    "input_coupling_corner",
    "input_termination_impedance",
    "input_return_loss",
    "common_mode_stability",
    "differential_stability",
    "bias_and_power",
)

COMPLETION_MARKER = "CTLE_ANALYSES_COMPLETE"
NUMBER = re.compile(r"[-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:e[-+]?\d+)?", re.I)
SCALAR = re.compile(r"([a-z]\w*)\s*=\s*(\S+)(?:\s+at\s*=\s*(\S+))?", re.I)
SIMULATOR_ERROR = re.compile(
    r"(?im)^\s*(?:error\b|fatal\b|doanalyses\s*:)|"
    r"\b(?:simulation|analysis|run)\b[^\n]*\baborted\b|timestep too small"
)
# The only simulator error text tolerated: a `when` measurement of one of the two optional crossings finding none.
OPTIONAL_MEASURE_FAILURE = re.compile(
    r"(?im)^\s*error:\s*measure\s+(?:bw_hz|low_bw_hz)\s+when\(when\)\s*:\s*out of interval\s*$"
)


class SimulationError(Exception):
    pass


def parse_number(text, key):
    if not NUMBER.fullmatch(text):
        raise SimulationError(f"non-numeric measurement {key}={text}")
    value = float(text)
    if not math.isfinite(value):
        raise SimulationError(f"non-finite measurement {key}")
    return value


def parse_scalars(output, names):
    """Read each published scalar exactly once; reject duplicates and non-finite values."""
    values = {}
    for line in output.splitlines():
        match = SCALAR.fullmatch(line.strip())
        if not match or match.group(1).lower() not in names:
            continue
        key = match.group(1).lower()
        if key in values:
            raise SimulationError(f"duplicate measurement {key}")
        values[key] = parse_number(match.group(2), key)
        if match.group(3) is not None:
            values[f"{key}_at"] = parse_number(match.group(3), f"{key}_at")
    return values


def run_deck(deck, replacements, names):
    """Run one bench under the signoff rules and return its published scalars."""
    if time.monotonic() - STARTED > DEADLINE_S:
        raise SimulationError(f"not run: verifier deadline of {DEADLINE_S} s reached")
    source = deck.read_text()
    for old, new in replacements.items():
        if source.count(old) != 1:
            raise SimulationError(f"bench substitution not unique: {old!r}")
        source = source.replace(old, str(new))
    with tempfile.TemporaryDirectory(prefix="ctle-") as work:
        run_path = Path(work) / deck.name
        run_path.write_text(source)
        result = ihp_signoff.run(["ngspice", "-b", str(run_path)], cwd=work, text=True,
                                 stdout=ihp_signoff.subprocess.PIPE, stderr=ihp_signoff.subprocess.STDOUT)
    output = result.stdout
    if result.returncode != 0:
        rule = next((line for line in output.splitlines() if line.startswith("SIGNOFF RULE:")), "")
        raise SimulationError(rule or f"ngspice exited {result.returncode}")
    if SIMULATOR_ERROR.search(OPTIONAL_MEASURE_FAILURE.sub("", output)):
        raise SimulationError("ngspice reported an error")
    if [line.strip() for line in output.splitlines()].count(COMPLETION_MARKER) != 1:
        raise SimulationError("missing or duplicate analysis completion marker")
    return parse_scalars(output, names)


def substitutions(pvt, code):
    _pname, corner, supply, temperature, load = pvt
    _cname, gb1, gb0, pb1, pb0 = code
    mos, hbt, res, cap = CORNER_LIBS[corner]
    return {
        f'.lib "{MODELS}/cornerMOSlv.lib" mos_tt\n': f'.lib "{MODELS}/cornerMOSlv.lib" {mos}\n',
        f'.lib "{MODELS}/cornerMOShv.lib" mos_tt\n': f'.lib "{MODELS}/cornerMOShv.lib" {mos}\n',
        f'.lib "{MODELS}/cornerHBT.lib" hbt_typ\n': f'.lib "{MODELS}/cornerHBT.lib" {hbt}\n',
        f'.lib "{MODELS}/cornerRES.lib" res_typ\n': f'.lib "{MODELS}/cornerRES.lib" {res}\n',
        f'.lib "{MODELS}/cornerCAP.lib" cap_typ\n': f'.lib "{MODELS}/cornerCAP.lib" {cap}\n',
        ".param supply=3.3\n": f".param supply={supply:.12g}\n",
        ".param temperature=27\n": f".param temperature={temperature}\n",
        ".param code_g0=0\n": f".param code_g0={gb0}\n",
        ".param code_g1=0\n": f".param code_g1={gb1}\n",
        ".param code_p0=0\n": f".param code_p0={pb0}\n",
        ".param code_p1=0\n": f".param code_p1={pb1}\n",
        ".param cload=200f\n": f".param cload={load:.12g}\n",
    }


def code_by_name(name):
    return next(c for c in CODES if c[0] == name)


def run_case(pvt, code):
    row = {"pvt": pvt[0], "code": code[0], "supply": pvt[2]}
    names = set(METRICS) | set(OPTIONAL_METRICS)
    try:
        values = run_deck(HERE / "benches" / "tb_ac_op.spi", substitutions(pvt, code), names)
        missing = [metric for metric in METRICS if metric not in values]
        if missing:
            raise SimulationError(f"missing measurements {', '.join(missing)}")
        row.update({key: value for key, value in values.items() if key in names})
    except (OSError, SimulationError) as error:
        row["ac_error"] = str(error)
    return row


def run_zin_case(pvt, pin):
    metrics = ZIN_METRICS[pin]
    replacements = substitutions(pvt, ZIN_CODE)
    replacements.update({
        ".param excite_p=1\n": f".param excite_p={int(pin == 'p')}\n",
        ".param excite_n=0\n": f".param excite_n={int(pin == 'n')}\n",
    })
    try:
        values = run_deck(HERE / "benches" / "tb_zin.spi", replacements, set(metrics))
        missing = [metric for metric in metrics if metric not in values]
        if missing:
            raise SimulationError(f"missing measurements {', '.join(missing)}")
        return {metric: values[metric] for metric in metrics}
    except (OSError, SimulationError) as error:
        return {"error": f"{pin} impedance run: {error}"}


def run_kick_case(pvt, kick):
    name, cm, dm, at, cmi, cmt = kick
    replacements = substitutions(pvt, code_by_name(name))
    replacements.update({
        ".param kick_cm=0.1\n": f".param kick_cm={cm:.12g}\n",
        ".param kick_dm=1m\n": f".param kick_dm={dm:.12g}\n",
        ".param kick_t=1u\n": f".param kick_t={at}\n",
        ".param kick_cmi=1m\n": f".param kick_cmi={cmi:.12g}\n",
        ".param kick_cmt=0.5u\n": f".param kick_cmt={cmt}\n",
    })
    try:
        values = run_deck(HERE / "benches" / "tb_kick.spi", replacements, set(KICK_METRICS))
        missing = [metric for metric in KICK_METRICS if metric not in values]
        if missing:
            raise SimulationError(f"missing measurements {', '.join(missing)}")
        return {metric: values[metric] for metric in KICK_METRICS}
    except (OSError, SimulationError) as error:
        return {"error": f"{name} disturbance transient: {error}"}


def merge_into_row(row, values, kind):
    """Add a zin or kick result to its AC row; a failed run is recorded as that kind's error."""
    if row is None:
        return
    if "error" in values:
        key = f"{kind}_error"
        row[key] = "; ".join(filter(None, (row.get(key), values["error"])))
    else:
        row.update(values)


def run_all_cases(pvts):
    jobs = [("ac", pvt, code) for pvt in pvts for code in CODES]
    jobs += [("zin", pvt, pin) for pvt in pvts for pin in ZIN_METRICS]
    jobs += [("kick", pvt, kick) for pvt in pvts for kick in KICKS]

    def one(job):
        kind, pvt, what = job
        if kind == "ac":
            return job, run_case(pvt, what)
        if kind == "zin":
            return job, run_zin_case(pvt, what)
        return job, run_kick_case(pvt, what)

    with ThreadPoolExecutor(JOBS) as pool:
        results = list(pool.map(one, jobs))
    rows = [result for (kind, _pvt, _what), result in results if kind == "ac"]
    by_pvt_code = {(r["pvt"], r["code"]): r for r in rows}
    for (kind, pvt, what), result in results:
        if kind == "ac":
            print(json.dumps(result, sort_keys=True), flush=True)
        elif kind == "zin":
            print(json.dumps({"pvt": pvt[0], "zin_pin": what, **result}, sort_keys=True), flush=True)
            merge_into_row(by_pvt_code.get((pvt[0], ZIN_CODE[0])), result, "zin")
        else:
            print(json.dumps({"pvt": pvt[0], "kick_code": what[0], **result}, sort_keys=True), flush=True)
            merge_into_row(by_pvt_code.get((pvt[0], what[0])), result, "kick")
    return rows, len(jobs)


def finite_number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def rows_by_pvt(rows):
    grouped = {}
    for row in rows:
        grouped.setdefault(row["pvt"], []).append(row)
    return grouped


def missing(rows, kind):
    """Reasons why a measurement group is incomplete over every graded point, or [] when it is complete.

    kind "ac": every (PVT, code) row simulated with finite AC and operating-point metrics; "zin": the impedance runs
    at gain=00/peak=00 of every point; "kick": the disturbance transients of every point, each about a valid
    operating point (its AC run at the same point and code)."""
    reasons = []
    by_key = {(row["pvt"], row["code"]): row for row in rows}
    for pvt in PVT:
        for code in CODES:
            row = by_key.get((pvt[0], code[0]))
            where = f"{pvt[0]}/{code[0]}"
            if row is None:
                reasons.append(f"{where}: no result")
                continue
            if kind == "ac":
                if "ac_error" in row:
                    reasons.append(f"{where}: {row['ac_error']}")
                elif not all(finite_number(row.get(m)) for m in METRICS) or not all(
                        finite_number(row[m]) for m in OPTIONAL_METRICS if m in row):
                    reasons.append(f"{where}: non-finite measurement")
            elif kind == "zin" and code[0] == ZIN_CODE[0]:
                if "zin_error" in row:
                    reasons.append(f"{where}: {row['zin_error']}")
                elif not all(finite_number(row.get(m)) for ms in ZIN_METRICS.values() for m in ms):
                    reasons.append(f"{where}: impedance measurement missing")
            elif kind == "kick" and code[0] in {k[0] for k in KICKS}:
                # A disturbance is only meaningful about a valid operating point of the same point and code;
                # report every cause.
                causes = []
                if "ac_error" in row:
                    causes.append(f"no valid operating point ({row['ac_error']})")
                if "kick_error" in row:
                    causes.append(row["kick_error"])
                if causes:
                    reasons.append(f"{where}: " + "; ".join(causes))
                elif not all(finite_number(row.get(m)) for m in KICK_METRICS):
                    reasons.append(f"{where}: disturbance measurement missing")
    return reasons


def index_by_gain_peak(group, metric):
    """{(gain_code, peak_code): value} for one PVT group's rows."""
    return {(row["code"][1:3], row["code"][5:7]): row[metric] for row in group if metric in row}


def code_table_check(rows, metric, label, targets, varying, tolerance):
    """Tolerance window per code, and the minimum step between adjacent codes of the varying field, checked at
    every value of the other field."""
    failures = []
    observed = {code: [] for code in CODE_ORDER}
    steps = []
    for pvt_name, group in rows_by_pvt(rows).items():
        table = index_by_gain_peak(group, metric)
        for fixed in CODE_ORDER:
            prev = None
            for code in CODE_ORDER:
                key = (code, fixed) if varying == "gain" else (fixed, code)
                value = table.get(key)
                if value is None:
                    continue
                observed[code].append(value)
                target = targets[code]
                where = f"{pvt_name} gain={key[0]} peak={key[1]}"
                if not (target - tolerance <= value <= target + tolerance):
                    failures.append(f"{where} {label}={value:.3f}dB target {target:g}+-{tolerance:g}dB")
                if prev is not None:
                    steps.append(value - prev)
                    if value - prev < MIN_CODE_STEP_DB:
                        failures.append(f"{where} {varying} step {value - prev:.3f}dB < {MIN_CODE_STEP_DB:g}dB")
                prev = value
    if failures:
        return False, f"{len(failures)} failures, e.g. {failures[0]}"
    ranges = ", ".join(f"{code}={min(values):.2f}..{max(values):.2f}dB" for code, values in observed.items())
    return True, f"{label} {ranges}; min {varying} step={min(steps):.2f}dB"


def gain_code_table_check(rows):
    return code_table_check(rows, "g_lf_db", "G_LF", GAIN_TARGET_DB, "gain", GAIN_TOLERANCE_DB)


def peak_code_table_check(rows):
    return code_table_check(rows, "g_14_db", "G_14", PEAK_TARGET_DB, "peak", PEAK_TOLERANCE_DB)


def output_polarity_check(rows):
    """Non-inverting: at 1 MHz the differential output is in phase with the differential input."""
    worst = min(rows, key=lambda row: row["g_lf_re"] / 10 ** (row["g_lf_db"] / 20))
    cosine = worst["g_lf_re"] / 10 ** (worst["g_lf_db"] / 20)
    return cosine > 0, (
        f"lowest cos(phase) of the 1MHz differential gain={cosine:.3f} at {worst['pvt']}/{worst['code']} "
        "(must be positive: voutp-voutn in phase with vinp-vinn)"
    )


def inband_flatness_check(rows):
    """Ceiling over max(G_LF, G_14) and floor under G_LF, both over 1 MHz-14 GHz."""
    def excess(row):
        return row["inband_peak_db"] - max(row["g_lf_db"], row["g_14_db"])

    def droop(row):
        return row["g_lf_db"] - row["inband_min_db"]
    worst_excess = max(rows, key=excess)
    worst_droop = max(rows, key=droop)
    ok = excess(worst_excess) <= INBAND_EXCESS_MAX_DB and droop(worst_droop) <= INBAND_DROOP_MAX_DB
    return ok, (
        f"worst 1MHz-14GHz excess over max(G_LF,G_14)={excess(worst_excess):.3f}dB at "
        f"{worst_excess['pvt']}/{worst_excess['code']} (max {INBAND_EXCESS_MAX_DB:g}dB); "
        f"worst droop below G_LF={droop(worst_droop):.3f}dB at "
        f"{worst_droop['pvt']}/{worst_droop['code']} (max {INBAND_DROOP_MAX_DB:g}dB)"
    )


def above_nyquist_peaking_check(rows):
    def excess(row):
        return row["hf_peak_db"] - row["g_14_db"]
    worst = max(rows, key=excess)
    return excess(worst) <= ABOVE_NYQUIST_EXCESS_MAX_DB, (
        f"worst 14-40GHz excess over G_14={excess(worst):.3f}dB at "
        f"{worst['pvt']}/{worst['code']} (max {ABOVE_NYQUIST_EXCESS_MAX_DB:g}dB)"
    )


def bandwidth_floor_check(rows):
    failed = []
    crossed = []
    for row in rows:
        if "bw_hz" in row:
            crossed.append(row["bw_hz"])
            if row["bw_hz"] < BW_FLOOR_HZ:
                # ngspice's `when ... from=14g` mis-interpolates a crossing in the first sweep interval after 14 GHz
                # (14.19-15.85 GHz) and can return a value below 14 GHz; either way the crossing is below the floor.
                detail = (f"crossing={row['bw_hz'] / 1e9:.2f}GHz" if row["bw_hz"] >= 14e9
                          else "crossing in the first sweep interval above 14 GHz (below 15.9 GHz)")
                failed.append((row, detail))
        elif row["gain_at_sweep_stop"] < row["g_14_db"] - 3:
            failed.append((row, "no crossing found and gain below G_14-3dB at the 40GHz sweep stop"))
    if failed:
        row, detail = failed[0]
        return False, f"{detail} at {row['pvt']}/{row['code']} (floor {BW_FLOOR_HZ / 1e9:.1f}GHz)"
    if crossed:
        return True, f"min crossing={min(crossed) / 1e9:.2f}GHz (floor {BW_FLOOR_HZ / 1e9:.1f}GHz)"
    return True, "still above G_14-3dB at the 40GHz sweep stop on every code"


def input_coupling_corner_check(rows):
    """Low-frequency corner at gain=00/peak=00 with the link's 100 nF coupling capacitors in the source path."""
    relevant = [r for r in rows if r["code"] == ZIN_CODE[0]]
    corners = []
    for row in relevant:
        if "low_bw_hz" in row:
            corners.append(row["low_bw_hz"])
            if row["low_bw_hz"] > LOW_CORNER_MAX_HZ:
                return False, f"corner={row['low_bw_hz']:.0f}Hz at {row['pvt']} (max {LOW_CORNER_MAX_HZ:.0f}Hz)"
        elif row["gain_start_db"] < row["g_lf_db"] - LOW_CORNER_FLOOR_DB:
            return False, (
                f"no rising -3dB crossing below 2MHz, but G(100Hz)={row['gain_start_db']:.2f}dB is more than "
                f"{LOW_CORNER_FLOOR_DB:g}dB below G_LF={row['g_lf_db']:.2f}dB at {row['pvt']}"
            )
    found = f"range={min(corners):.0f}-{max(corners):.0f}Hz" if corners else "no crossing above 100 Hz"
    return True, f"{found} (max {LOW_CORNER_MAX_HZ:.0f}Hz; the link's 100 nF capacitors alone give 15.9 kHz)"


def input_termination_impedance_check(rows):
    """Single-ended 1 MHz input impedance, scored at gain=00/peak=00."""
    relevant = [r for r in rows if r["code"] == ZIN_CODE[0]]
    lo, hi = ZIN_TARGET_OHM - ZIN_TOLERANCE_OHM, ZIN_TARGET_OHM + ZIN_TOLERANCE_OHM
    failed = []
    for row in relevant:
        for pin, value in (("vinp", row["zinp_1m"]), ("vinn", row["zinn_1m"])):
            if not (lo <= value <= hi):
                failed.append(f"{pin} Zin={value:.2f}ohm at {row['pvt']} (want {lo:.0f}-{hi:.0f}ohm)")
    if failed:
        return False, f"{len(failed)} failures, e.g. {failed[0]}"
    all_z = [r["zinp_1m"] for r in relevant] + [r["zinn_1m"] for r in relevant]
    return True, f"range={min(all_z):.2f}-{max(all_z):.2f}ohm (want {lo:.0f}-{hi:.0f}ohm)"


def input_return_loss_check(rows):
    """Single-ended |S11| per pin (gain=00/peak=00 runs) and differential |Sdd11| at every code, 100 MHz-14 GHz."""
    failed = []
    single = []
    for row in rows:
        if row["code"] == ZIN_CODE[0]:
            for pin, value in (("vinp", row["s11p_max_db"]), ("vinn", row["s11n_max_db"])):
                single.append(value)
                if value > RETURN_LOSS_MAX_DB:
                    failed.append(f"{pin} |S11|max={value:.2f}dB at {row['pvt']}")
        if row["sdd11_max_db"] > RETURN_LOSS_MAX_DB:
            failed.append(f"|Sdd11|max={row['sdd11_max_db']:.2f}dB at {row['pvt']}/{row['code']}")
    if failed:
        return False, f"{len(failed)} failures, e.g. {failed[0]} (max {RETURN_LOSS_MAX_DB:g}dB)"
    return True, (
        f"worst single-ended |S11|={max(single):.2f}dB, worst |Sdd11|={max(r['sdd11_max_db'] for r in rows):.2f}dB "
        f"over 100MHz-14GHz (max {RETURN_LOSS_MAX_DB:g}dB)"
    )


def forward_reverse_ratio_check(rows):
    worst = min(rows, key=lambda row: row["fwd_rev_min_db"])
    return worst["fwd_rev_min_db"] >= FORWARD_REVERSE_MIN_DB, (
        f"worst |Z21/Z12| over 100MHz-14GHz={worst['fwd_rev_min_db']:.2f}dB at "
        f"{worst['pvt']}/{worst['code']} (min {FORWARD_REVERSE_MIN_DB:g}dB; any reciprocal network is 0dB)"
    )


def kick_rows(rows):
    return [row for row in rows if row["code"] in {kick[0] for kick in KICKS}]


def common_mode_stability_check(rows):
    """After the disturbances the output common mode must be still and back within CM_ERROR_MAX_V of vdd/2."""
    def peak_to_peak(row):
        return row["cm_kick_max_v"] - row["cm_kick_min_v"]

    def error(row):
        target = row["supply"] / 2
        return max(abs(row["cm_kick_max_v"] - target), abs(row["cm_kick_min_v"] - target))
    relevant = kick_rows(rows)
    worst = max(relevant, key=peak_to_peak)
    worst_error = max(relevant, key=error)
    ok = peak_to_peak(worst) <= CM_KICK_PP_MAX_V + ROUNDOFF and error(worst_error) <= CM_ERROR_MAX_V + ROUNDOFF
    return ok, (
        f"worst output common-mode peak-to-peak over 3.1-4.1us={1e3 * peak_to_peak(worst):.4f}mV at "
        f"{worst['pvt']}/{worst['code']} (max {1e3 * CM_KICK_PP_MAX_V:g}mV); largest distance from vdd/2="
        f"{1e3 * error(worst_error):.2f}mV at {worst_error['pvt']}/{worst_error['code']} "
        f"(max {1e3 * CM_ERROR_MAX_V:g}mV)"
    )


def differential_stability_check(rows):
    """After the disturbances the differential output must be still and back near zero (no oscillation, no latch)."""
    def peak_to_peak(row):
        return row["dm_kick_max_v"] - row["dm_kick_min_v"]

    def largest(row):
        return max(abs(row["dm_kick_max_v"]), abs(row["dm_kick_min_v"]))
    relevant = kick_rows(rows)
    worst_pp = max(relevant, key=peak_to_peak)
    worst_abs = max(relevant, key=largest)
    ok = peak_to_peak(worst_pp) <= DM_KICK_PP_MAX_V + ROUNDOFF and largest(worst_abs) <= DM_KICK_ABS_MAX_V + ROUNDOFF
    return ok, (
        f"worst differential peak-to-peak over 3.1-4.1us={1e3 * peak_to_peak(worst_pp):.4f}mV at "
        f"{worst_pp['pvt']}/{worst_pp['code']} (max {1e3 * DM_KICK_PP_MAX_V:g}mV); largest |voutp-voutn|="
        f"{1e3 * largest(worst_abs):.3f}mV at {worst_abs['pvt']}/{worst_abs['code']} (max {1e3 * DM_KICK_ABS_MAX_V:g}mV)"
    )


def vocm_tracking(row):
    return row["vocm_step_cm_v"] - row["output_common_mode_v"]


def total_power(row):
    return row["power_w"] + row["other_source_power_w"]


def bias_and_power_check(rows):
    failures = []
    for row in rows:
        vocm = row["supply"] / 2
        vdd = row["supply"]
        where = f"{row['pvt']}/{row['code']}"
        if abs(row["output_common_mode_v"] - vocm) > CM_ERROR_MAX_V:
            failures.append(f"{where} CM error={1e3 * abs(row['output_common_mode_v'] - vocm):.1f}mV")
        if row["output_offset_v"] > OFFSET_MAX_V:
            failures.append(f"{where} offset={1e3 * row['output_offset_v']:.2f}mV")
        for pin in ("voutp", "voutn"):
            if not (SWING_MARGIN_V <= row[f"{pin}_v"] <= vdd - SWING_MARGIN_V):
                failures.append(f"{where} {pin}={row[f'{pin}_v']:.3f}V out of the swing range")
        if abs(row["vocm_pin_i"]) > VOCM_PIN_MAX_A + ROUNDOFF:
            failures.append(f"{where} vocm pin current={1e6 * row['vocm_pin_i']:.1f}uA "
                            f"(max {1e6 * VOCM_PIN_MAX_A:g}uA)")
        if vocm_tracking(row) < VOCM_TRACK_MIN_V - ROUNDOFF:
            failures.append(f"{where} vocm tracking: output common mode moved {1e3 * vocm_tracking(row):.3f}mV "
                            f"for +10mV vocm (min {1e3 * VOCM_TRACK_MIN_V:g}mV)")
        if total_power(row) > POWER_MAX_W + ROUNDOFF:
            failures.append(f"{where} total power={1e3 * total_power(row):.2f}mW (vdd {1e3 * row['power_w']:.2f}mW "
                            f"+ other sources {1e3 * row['other_source_power_w']:.2f}mW)")
    if failures:
        return False, f"{len(failures)} failures, e.g. {failures[0]}"
    powers = [total_power(r) for r in rows]
    cm = max(abs(r["output_common_mode_v"] - r["supply"] / 2) for r in rows)
    track = [vocm_tracking(r) for r in rows]
    return True, (
        f"total power={1e3 * min(powers):.2f}-{1e3 * max(powers):.2f}mW (max {1e3 * POWER_MAX_W:.0f}mW); "
        f"max non-vdd source power={1e3 * max(r['other_source_power_w'] for r in rows):.4f}mW; "
        f"max CM error={1e3 * cm:.1f}mV; max vocm pin current={1e6 * max(abs(r['vocm_pin_i']) for r in rows):.1f}uA; "
        f"max offset={1e3 * max(r['output_offset_v'] for r in rows):.3f}mV; "
        f"vocm tracking={1e3 * min(track):.3f}-{1e3 * max(track):.3f}mV for +10mV (min {1e3 * VOCM_TRACK_MIN_V:g}mV)"
    )


# Measurement groups each check needs.
CHECK_GROUPS = {
    "gain_code_table": ("ac",), "peak_code_table": ("ac",), "output_polarity": ("ac",),
    "inband_flatness": ("ac",), "above_nyquist_peaking": ("ac",), "bandwidth_floor": ("ac",),
    "forward_reverse_ratio": ("ac",), "input_coupling_corner": ("ac",),
    "input_termination_impedance": ("zin",), "input_return_loss": ("ac", "zin"),
    "common_mode_stability": ("kick",), "differential_stability": ("kick",), "bias_and_power": ("ac",),
}
CHECK_FUNCTIONS = {
    "gain_code_table": lambda rows: gain_code_table_check(rows),
    "peak_code_table": lambda rows: peak_code_table_check(rows),
    "output_polarity": lambda rows: output_polarity_check(rows),
    "inband_flatness": lambda rows: inband_flatness_check(rows),
    "above_nyquist_peaking": lambda rows: above_nyquist_peaking_check(rows),
    "bandwidth_floor": lambda rows: bandwidth_floor_check(rows),
    "forward_reverse_ratio": lambda rows: forward_reverse_ratio_check(rows),
    "input_coupling_corner": lambda rows: input_coupling_corner_check(rows),
    "input_termination_impedance": lambda rows: input_termination_impedance_check(rows),
    "input_return_loss": lambda rows: input_return_loss_check(rows),
    "common_mode_stability": lambda rows: common_mode_stability_check(rows),
    "differential_stability": lambda rows: differential_stability_check(rows),
    "bias_and_power": lambda rows: bias_and_power_check(rows),
}


def checks(rows):
    """Each check on its own: it fails if its measurements are missing at any point or fail a limit anywhere."""
    gaps = {kind: missing(rows, kind) for kind in ("ac", "zin", "kick")}
    results = []
    for name in CHECK_NAMES:
        reasons = [r for kind in CHECK_GROUPS[name] for r in gaps[kind]]
        if reasons:
            results.append((name, False, f"no valid measurement at {len(reasons)} point(s), e.g. {reasons[0]}"))
            continue
        try:
            results.append((name, *CHECK_FUNCTIONS[name](rows)))
        except (KeyError, TypeError, ValueError) as error:
            results.append((name, False, f"evaluation error: {type(error).__name__}: {error}"))
    return results


def finish(results, points, processes, started):
    write_results([results[name] for name in CHECK_NAMES], OUTPUT)
    print(f"pvt_x_code_points={points} ngspice_processes={processes} "
          f"wall_clock_s={time.monotonic() - started:.3f}", flush=True)


def signoff(started):
    rows, processes = run_all_cases(list(PVT))
    results = {check[0]: check for check in checks(rows)}
    finish(results, len(rows), processes, started)


STARTED = time.monotonic()


def main():
    started = STARTED
    try:
        signoff(started)
    except Exception as error:  # any verifier fault is a zero, never a crash
        traceback.print_exc()
        reason = f"verifier error: {type(error).__name__}: {error}"
        finish({name: (name, False, reason) for name in CHECK_NAMES}, 0, 0, started)


if __name__ == "__main__":
    main()
