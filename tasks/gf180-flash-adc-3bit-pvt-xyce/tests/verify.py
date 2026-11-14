#!/usr/bin/env python3
"""Grade the GF180MCU 3-bit flash ADC: transfer and coherent-sine benches across PVT, simulated with Xyce under the
signoff rules (xyce_signoff.py)."""

from __future__ import annotations

import concurrent.futures
import json
import os
import re
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import adc_metrics  # noqa: E402
import xyce_signoff  # noqa: E402

TASK_ID = "gf180-flash-adc-3bit-pvt-xyce"
OUTPUT = Path("/logs/verifier")
DESIGN = Path("/app/circuit.spi")
BENCHES = HERE / "benches"

# ---- public contract (spec/README.md) ----
SNDR_3MHZ_MIN_DB = 22.0
SNDR_23MHZ_MIN_DB = 19.0
POWER_MAX_W = 2.5e-3

TRANSFER_POINTS = (  # (corner, vdd, temperature)
    ("typical", 3.30, 27), ("ss", 3.00, -40), ("ss", 3.00, 125), ("ff", 3.60, -40), ("ff", 3.60, 125),
    ("sf", 3.00, -40), ("fs", 3.60, 125),
)
SINE_POINTS = (
    ("typical", 3.30, 27), ("ss", 3.00, -40), ("ff", 3.60, 125), ("sf", 3.60, 125), ("fs", 3.00, -40),
)
CASES = [{"bench": "transfer", "corner": c, "vdd": v, "temp": t} for c, v, t in TRANSFER_POINTS] + \
        [{"bench": b, "corner": c, "vdd": v, "temp": t} for b in ("sine", "nyquist") for c, v, t in SINE_POINTS]
CHECKS = ("transfer_codes", "overrange_codes", "output_timing", "sndr_3mhz", "sndr_23mhz", "power")


def label(case: dict) -> str:
    return f'{case["bench"]} {case["corner"]}/{case["vdd"]:.2f} V/{case["temp"]} C'


def render(text: str, case: dict) -> str:
    vdd = f'{case["vdd"]:.2f}'
    text = text.replace('sm141064.xyce" typical', f'sm141064.xyce" {case["corner"]}', 1)
    text = text.replace('.include "/app/circuit.spi"', f'.include "{DESIGN}"', 1)
    text = re.sub(r"(?m)^\.options device temp=27$", f'.options device temp={case["temp"]}', text, count=1)
    text = re.sub(r"(?m)^VSUP vdd vss 3\.3$", f"VSUP vdd vss {vdd}", text, count=1)
    return text.replace("PULSE(0 3.3 ", f"PULSE(0 {vdd} ", 1)


def run_case(case: dict) -> dict:
    """Simulate one bench at one point and reduce it to its metrics; a failed run carries an `error`, a broken
    signoff rule a `rule`."""
    row = {**case, "label": label(case)}
    with tempfile.TemporaryDirectory(prefix="flash3-") as work:
        text = render((BENCHES / f'tb_{case["bench"]}.cir').read_text(), case)
        deck = Path(work) / f'tb_{case["bench"]}.cir'
        deck.write_text(text)
        info = adc_metrics.bench_info(text)
        started = time.monotonic()
        result = xyce_signoff.run(deck, [(adc_metrics.POWER_FROM, info["power_to"])])
        row["seconds"] = round(time.monotonic() - started, 1)
    if result.get("rule"):
        return {**row, "rule": result["rule"], "error": f'signoff rule: {result["rule"]}'}
    if result.get("error"):
        return {**row, "error": result["error"]}
    try:
        row.update(adc_metrics.evaluate(result["names"], result["data"], info))
    except (ValueError, KeyError, IndexError) as error:
        return {**row, "error": f"missing waveform: {error}"}
    if case["bench"] == "transfer":
        inputs = adc_metrics.transfer_inputs(text)
        row["errors"] = [(k, inputs[k], info["expected"][k], code, adc_metrics.input_kind(inputs[k]))
                         for k, code in zip(range(adc_metrics.WARMUP, info["conversions"]), row["codes"])
                         if code != info["expected"][k]]
    else:
        row["sndr_db"], row["sfdr_db"] = adc_metrics.spectrum(row["codes"], info["tone_bin"])
    return row


def score(rows: list[dict]) -> list[tuple[str, bool, str]]:
    """Each check passes only when every run it uses completed and met the limit."""
    def runs(*benches):
        used = [r for r in rows if r["bench"] in benches]
        failed = [r for r in used if "error" in r]
        done = [r for r in used if "error" not in r]
        note = "" if not failed and len(used) == sum(c["bench"] in benches for c in CASES) else \
            f" [incomplete: {'; '.join(r['label'] + ': ' + r['error'] for r in failed) or 'runs missing'}]"
        return done, note

    def wrong(kinds):
        done, note = runs("transfer")
        out = [f"{r['label']}: conversion {k} vin={v:.2f} V read {got}, want {want}"
               for r in done for k, v, want, got, kind in r["errors"] if kind in kinds]
        return out, note

    checks = []
    err, note = wrong(("center", "edge"))
    transfer_ok = not err and not note
    checks.append(("transfer_codes", transfer_ok, (f"{len(err)} wrong: " + "; ".join(err[:4]) if err else
                   "every code center and every input 0.3 LSB from a threshold converts correctly") + note))
    err, note = wrong(("overrange",))
    checks.append(("overrange_codes", not err and not note, (f"{len(err)} wrong: " + "; ".join(err[:4]) if err else
                   "inputs below vrefn read 0 and above vrefp read 7") + note))
    done, note = runs("transfer", "sine", "nyquist")
    lv = [f"{r['label']}: {m}" for r in done for m in r["bad_levels"]]
    lv += [f"{r['label']}: output {bit} never toggles" for r in done for bit in r["stuck_bits"]]
    checks.append(("output_timing", not lv and not note, (f"{len(lv)} violations: " + "; ".join(lv[:4]) if lv else
                   "every output toggles and is valid from 3 ns to 19 ns after every edge") + note))
    for name, bench, limit in (("sndr_3mhz", "sine", SNDR_3MHZ_MIN_DB), ("sndr_23mhz", "nyquist", SNDR_23MHZ_MIN_DB)):
        done, note = runs(bench)
        worst = min(done, key=lambda r: r["sndr_db"], default=None)
        msg = f"worst {worst['sndr_db']:.2f} dB at {worst['label']} (>= {limit:g})" if worst else "no result"
        checks.append((name, bool(worst) and worst["sndr_db"] >= limit and not note, msg + note))
    done, note = runs("transfer", "sine", "nyquist")
    power = max(done, key=lambda r: r["power_w"], default=None)
    within = bool(power) and power["power_w"] <= POWER_MAX_W
    msg = "no result" if not power else (
        f"worst {power['power_w'] * 1e3:.3f} mW at {power['label']} (<= {POWER_MAX_W * 1e3:g}); by pin: "
        + ", ".join(f"{p} {power[f'power_{p}_w'] * 1e3:.3f}" for p in adc_metrics.PINS))
    if not transfer_ok:
        msg += "; not scored because transfer_codes failed (a non-converting design earns no power credit)"
    checks.append(("power", within and not note and transfer_ok, msg + note))
    return checks


def write_results(checks: list[tuple[str, bool, str]], rules: list[str]) -> float:
    """reward.json and new-ctrf.json; a broken signoff rule in any run scores the task 0."""
    tests = [{"name": n, "status": "passed" if ok else "failed", "message": m} for n, ok, m in checks]
    passed = sum(ok for _, ok, _ in checks)
    reward = passed / len(checks)
    if rules:
        unique = list(dict.fromkeys(rules))
        tests.append({"name": "signoff_rules", "status": "failed",
                      "message": f"{len(unique)} run(s) broke a signoff rule; the task scores 0: " + " | ".join(unique[:5])})
        reward = 0.0
    OUTPUT.mkdir(parents=True, exist_ok=True)
    (OUTPUT / "reward.json").write_text(json.dumps({
        "reward": reward, "tests_total": len(checks), "tests_passed": passed if not rules else 0, "partial": reward,
    }) + "\n")
    (OUTPUT / "new-ctrf.json").write_text(json.dumps({"results": {
        "summary": {"tests": len(tests), "passed": sum(t["status"] == "passed" for t in tests),
                    "failed": sum(t["status"] == "failed" for t in tests)},
        "tests": tests}}, indent=2) + "\n")
    for t in tests:
        print(f'{t["status"].upper()} {t["name"]}: {t["message"]}')
    return reward


def main() -> int:
    started = time.monotonic()
    cpus = len(os.sched_getaffinity(0)) if hasattr(os, "sched_getaffinity") else (os.cpu_count() or 1)
    workers = max(1, min(4, cpus))
    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
        rows = list(pool.map(run_case, CASES))
    rules = [f"{r['label']}: {r['rule']}" for r in rows if r.get("rule")]
    write_results(score(rows), rules)
    elapsed = time.monotonic() - started
    (OUTPUT / "summary.json").write_text(json.dumps({"task": TASK_ID, "wall_clock_s": round(elapsed, 1),
                                                     "workers": workers, "runs": rows}, indent=1, default=str) + "\n")
    print(f"{len(rows)} Xyce runs, {workers} workers, {elapsed:.1f} s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
