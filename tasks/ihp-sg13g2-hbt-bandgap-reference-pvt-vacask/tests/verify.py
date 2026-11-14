#!/usr/bin/env python3
"""Hidden verifier of the IHP SG13G2 bandgap reference task (VACASK)."""
import json
import math
import os
import sys
import traceback
from concurrent.futures import ThreadPoolExecutor

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import bgr_signoff as bs  # noqa: E402

CIRCUIT = os.environ.get("BGR_CIRCUIT", "/app/circuit.sim")
LOG_DIR = os.environ.get("BGR_LOG_DIR", "/logs/verifier")
BENCHES = os.path.join(HERE, "benches")

# Limits (spec/README.md).
VREF_MIN, VREF_MAX = 1.00, 1.15
TC_MAX_PPM = 100.0
LINE_MAX_MV_PER_V = 1.0
LOAD_MAX_MV = 5.0
PSRR_MIN_DB = 40.0
NOISE_MAX = 1e-6
IQ_MAX = 100e-6
OFF_IQ_MAX = 1e-6
OFF_VREF_MAX = 0.1
NODESET_MAX_MV = 1.0
SETTLE_MAX = 10e-6
OVERSHOOT_MAX = 50e-3
TARGET_MATCH = 1e-3
# A circuit is graded as a reference only if these pass; otherwise the reward is 0.
GATE_CHECKS = ("vref_accuracy", "line_regulation")

# Coverage.
CORNERS = ["tt", "ss", "ff", "sf", "fs"]
SUPPLIES = [3.0, 3.3, 3.6]
DC_TEMPS = sorted(set(range(-40, 126, 5)) | {27})
EDGE_TEMPS = [-40, 27, 125]
STARTUP_MODES = {  # name: (supply rise, load capacitance)
    "ramp-1us": (1e-6, "20p"),
    "ramp-100us": (100e-6, "2p"),
    "enable": (None, "20p"),
}
CHECKS = ["vref_accuracy", "temperature_coefficient", "line_regulation", "load_regulation", "psrr_10khz",
          "noise_10khz", "quiescent_current", "shutdown", "startup_settling", "startup_overshoot"]


def available_cpus():
    """CPUs this process may use: the cgroup quota (v2 cpu.max or v1 cfs quota) and the affinity mask."""
    count = len(os.sched_getaffinity(0)) if hasattr(os, "sched_getaffinity") else (os.cpu_count() or 1)
    quota = None
    try:
        text = open("/sys/fs/cgroup/cpu.max").read().split()
        if text[0] != "max":
            quota = int(text[0]) / int(text[1])
    except (OSError, ValueError, IndexError):
        try:
            q = int(open("/sys/fs/cgroup/cpu/cpu.cfs_quota_us").read())
            p = int(open("/sys/fs/cgroup/cpu/cpu.cfs_period_us").read())
            if q > 0 and p > 0:
                quota = q / p
        except (OSError, ValueError):
            pass
    if quota:
        count = min(count, max(1, int(quota + 0.999)))
    return max(1, count)


def fmt_list(values):
    return ", ".join(f"{v:g}" for v in values)


def template(name):
    with open(os.path.join(BENCHES, name)) as handle:
        return handle.read()


def dc_job(corner):
    deck = bs.render(template("tb_dc.sim"), **bs.corner_tokens(corner), cl="10p", supplies=fmt_list(SUPPLIES),
                     temps=fmt_list(DC_TEMPS), corner_supplies=fmt_list(SUPPLIES),
                     corner_temps=fmt_list(EDGE_TEMPS), load=f"{bs.LOAD_CURRENT:g}")
    return f"dc/{corner}", deck


def ac_job(corner):
    deck = bs.render(template("tb_ac.sim"), **bs.corner_tokens(corner), cl="20p",
                     supplies=fmt_list(SUPPLIES), temps=fmt_list(EDGE_TEMPS))
    return f"ac/{corner}", deck


def startup_job(corner, vdd, temp, mode):
    rise, cl = STARTUP_MODES[mode]
    if rise is None:
        alter = f'  alter instance("vensrc") type="pulse" val0=0 val1={vdd} rise=100n delay=2u'
    else:
        alter = "\n".join(f'  alter instance("{src}") type="pulse" val0=0 val1={vdd} rise={rise:g} delay=1u'
                          for src in ("vdd", "vensrc"))
    stop = (2.1e-6 if rise is None else 1e-6 + rise) + 30e-6
    deck = bs.render(template("tb_startup.sim"), **bs.corner_tokens(corner), alter=alter, cl=cl, vdd=vdd,
                     temp=temp, stop=f"{stop:g}")
    return f"startup/{mode}/{corner}/{vdd:g}V/{temp}C", deck


def simulate(job, devices):
    label, deck = job[0], job[1]
    try:
        plots = bs.run_deck(deck, CIRCUIT)
    except bs.RunError as exc:
        return label, None, [], str(exc)
    violations = []
    try:
        for name, plot in plots.items():
            if name in bs.OP_ANALYSES or name == "tr":
                violations += bs.rule_violations(devices, plot, f"{label}/{name}", dc=name != "tr")
    except bs.RunError as exc:
        return label, None, [], str(exc)
    return label, plots, violations, None


def main():
    os.makedirs(LOG_DIR, exist_ok=True)
    report = {"checks": {}, "metrics": {}, "failed_runs": {}, "violations": []}
    checks = {name: [True, []] for name in CHECKS}

    def fail(check, message):
        checks[check][0] = False
        if len(checks[check][1]) < 12:
            checks[check][1].append(message)

    devices = bs.dut_devices(CIRCUIT)
    jobs = [dc_job(c) for c in CORNERS] + [ac_job(c) for c in CORNERS]
    jobs += [startup_job(c, v, t, m) for c in CORNERS for v in SUPPLIES for t in EDGE_TEMPS
             for m in STARTUP_MODES]
    workers = max(1, min(len(jobs), available_cpus()))
    with ThreadPoolExecutor(workers) as pool:
        results = list(pool.map(lambda j: simulate(j, devices), jobs))
    decks = dict(jobs)

    for label, plots, violations, error in results:
        report["violations"] += violations
        if error:
            report["failed_runs"][label] = error

    dc_vref = {}
    for label, plots, _, error in results:
        kind = label.split("/")[0]
        if error:
            dependent = {"dc": CHECKS[:4] + ["quiescent_current", "shutdown", "startup_settling"],
                         "ac": ["psrr_10khz", "noise_10khz"],
                         "startup": ["startup_settling", "startup_overshoot"]}[kind]
            for check in dependent:
                fail(check, f"{label}: simulation failed")
            continue
        if kind == "dc":
            m = bs.dc_metrics(plots)
            dc_vref[label.split("/")[1]] = m["vref"]
            report["metrics"][label] = {
                "vref_min": min(m["vref"].values()), "vref_max": max(m["vref"].values()),
                "tc_ppm_max": max(m["tc_ppm"].values()), "line_mv_per_v_max": max(m["line_mv_per_v"].values()),
                "load_mv_max": max(m["load_mv"].values()), "iq_max_ua": max(m["iq"].values()) * 1e6,
                "off_iq_max_na": max(max(abs(a), abs(b)) for a, b, _ in m["off"].values()) * 1e9,
                "off_vref_max": max(v for _, _, v in m["off"].values()),
                "nodeset_spread_mv_max": max(m["nodeset_mv"].values())}
            for (vs, t), v in m["vref"].items():
                if not VREF_MIN <= v <= VREF_MAX or math.isnan(v):
                    fail("vref_accuracy", f"{label} {vs:g} V {t:g} C: vref {v:.4f} V")
            for vs, tc in m["tc_ppm"].items():
                if not tc <= TC_MAX_PPM:
                    fail("temperature_coefficient", f"{label} {vs:g} V: {tc:.1f} ppm/C")
            for t, line in m["line_mv_per_v"].items():
                if not line <= LINE_MAX_MV_PER_V:
                    fail("line_regulation", f"{label} {t:g} C: {line:.2f} mV/V")
            for (vs, t), load in m["load_mv"].items():
                if not load <= LOAD_MAX_MV:
                    fail("load_regulation", f"{label} {vs:g} V {t:g} C: {load:.2f} mV")
            for (vs, t), iq in m["iq"].items():
                if not iq <= IQ_MAX:
                    fail("quiescent_current", f"{label} {vs:g} V {t:g} C: {iq * 1e6:.1f} uA")
            for (vs, t), (i_vdd, i_en, v) in m["off"].items():
                if not (abs(i_vdd) <= OFF_IQ_MAX and abs(i_en) <= OFF_IQ_MAX and v <= OFF_VREF_MAX):
                    fail("shutdown", f"{label} {vs:g} V {t:g} C: supply {i_vdd * 1e9:.1f} nA, enable source "
                                     f"{i_en * 1e9:.1f} nA, vref {v:.3f} V")
            for (vs, t), spread in m["nodeset_mv"].items():
                if not spread <= NODESET_MAX_MV:
                    fail("startup_settling", f"{label} {vs:g} V {t:g} C: operating points from different "
                                             f"initial guesses differ by {spread:.1f} mV")
        elif kind == "ac":
            m = bs.ac_metrics(plots)
            report["metrics"][label] = {"psrr_db_min": min(m["psrr_db"].values()),
                                        "noise_nv_max": max(m["noise"].values()) * 1e9}
            for (vs, t), p in m["psrr_db"].items():
                if not p >= PSRR_MIN_DB:
                    fail("psrr_10khz", f"{label} {vs:g} V {t:g} C: {p:.1f} dB")
            for (vs, t), n in m["noise"].items():
                if not n <= NOISE_MAX:
                    fail("noise_10khz", f"{label} {vs:g} V {t:g} C: {n * 1e9:.0f} nV/rtHz")
        else:
            m = bs.startup_metrics(plots, bs.edge_end(decks[label]))
            _, _, corner, vdd, temp = label.split("/")
            reference = dc_vref.get(corner, {}).get((float(vdd[:-1]), float(temp[:-1])))
            if not (VREF_MIN <= m["target"] <= VREF_MAX) or reference is None \
                    or abs(m["target"] - reference) > TARGET_MATCH:
                fail("startup_settling", f"{label}: target operating point {m['target']:.4f} V is outside the "
                                         f"window or differs from the DC bench ({reference})")
            report["metrics"][label] = {"settle_us": m["settle_s"] * 1e6, "overshoot_mv": m["overshoot_v"] * 1e3,
                                        "target": m["target"]}
            if not m["settle_s"] <= SETTLE_MAX:
                fail("startup_settling", f"{label}: settles in {m['settle_s'] * 1e6:.2f} us")
            if not m["overshoot_v"] <= OVERSHOOT_MAX:
                fail("startup_overshoot", f"{label}: overshoot {m['overshoot_v'] * 1e3:.1f} mV")

    passed = sum(ok for ok, _ in checks.values())
    reward = passed / len(CHECKS)
    gate_failed = [name for name in GATE_CHECKS if not checks[name][0]]
    if gate_failed:
        reward = 0.0
        print(f"NOT A REFERENCE (reward 0): {', '.join(gate_failed)} failed")
    if report["violations"]:
        reward = 0.0
        print("SIGNOFF RULE VIOLATIONS (reward 0):")
        for line in report["violations"][:20]:
            print("  " + line)
    report["checks"] = {name: {"passed": ok, "failures": msgs} for name, (ok, msgs) in checks.items()}
    report["gate_failed"] = gate_failed
    for name, (ok, msgs) in checks.items():
        print(f"{'PASS' if ok else 'FAIL'} {name}" + ("" if ok else ": " + "; ".join(msgs[:3])))
    for label, error in report["failed_runs"].items():
        print(f"RUN FAILED {label}: {error.splitlines()[0] if error else ''}")
    print(f"reward {reward:.4f}")
    with open(os.path.join(LOG_DIR, "report.json"), "w") as handle:
        json.dump(report, handle, indent=1, default=str)
    with open(os.path.join(LOG_DIR, "reward.json"), "w") as handle:
        json.dump({"reward": reward, "tests_total": len(CHECKS), "tests_passed": passed,
                   "partial": reward}, handle)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        traceback.print_exc()
        sys.exit(1)
