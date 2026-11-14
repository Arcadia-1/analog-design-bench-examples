#!/usr/bin/env python3
"""Run a public bench the way grading simulates it, and print the graded metrics.

    cd /app
    python3 signoff/run_bench.py testbench/tb_transfer_tt.cir
    python3 signoff/run_bench.py testbench/tb_sine_tt.cir
    python3 signoff/run_bench.py testbench/tb_nyquist_tt.cir
    python3 signoff/run_bench.py testbench/tb_sine_tt.cir --keep /tmp/sine   # keep the deck, circuit copy and raw file

First the eligibility check of /app/spec/README.md runs on /app/circuit.spi (check_circuit.py, the grader's own copy).
Then Xyce simulates the bench under the rules of /app/spec/signoff-rules.md (xyce_signoff.py, the grader's own copy):
the circuit is rewritten as in grading, every node voltage and source current goes to a raw file in a scratch folder,
and the device ratings and stored-energy rule are checked. A broken rule prints a `SIGNOFF RULE:` line and the script
exits with code 3; a broken rule scores the task 0 in grading. The metrics are computed exactly as grading computes
them (adc_metrics.py). The public benches cover only the nominal point, so a clean run here is necessary, not
sufficient.
"""
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.dont_write_bytecode = True  # leave no __pycache__ in /app

import adc_metrics  # noqa: E402
import check_circuit  # noqa: E402
import xyce_signoff  # noqa: E402

CIRCUIT = Path("/app/circuit.spi")
REQUIRED = ["flash_adc_3bit", "vss", "vdd", "vrefn", "vrefp", "clk", "vin", "b2", "b1", "b0"]
xyce_signoff.NOT_THE_CIRCUIT.append(f"{HERE}/")


def main() -> int:
    args = sys.argv[1:]
    keep = None
    if "--keep" in args:
        at = args.index("--keep")
        keep = Path(args[at + 1]).resolve()
        args = args[:at] + args[at + 2:]
    if len(args) != 1 or args[0] in ("-h", "--help"):
        print(__doc__)
        return 2
    bench = Path(args[0]).resolve()
    text = bench.read_text()
    info = adc_metrics.bench_info(text)
    found = check_circuit.violations(CIRCUIT.read_text(errors="replace"), REQUIRED) if CIRCUIT.exists() else ["missing"]
    if found:
        print(f"{CIRCUIT}: eligibility check failed (grading scores 0):")
        for reason in found:
            print(f"  {reason}")
        return 1
    result = xyce_signoff.run(bench, [(adc_metrics.POWER_FROM, info["power_to"])], keep=keep)
    if keep:
        print(f"kept the rewritten deck, circuit and raw file in {keep}")
    if result.get("rule"):
        print(f"SIGNOFF RULE: {result['rule']}")
        return 3
    if result.get("error"):
        print(f"simulation failed: {result['error']}")
        return 1
    m = adc_metrics.evaluate(result["names"], result["data"], info)
    codes = m["codes"]
    print("codes:", " ".join(map(str, codes)))
    print("output_timing:", "ok" if not m["bad_levels"] else
          f"{len(m['bad_levels'])} violations: " + "; ".join(m["bad_levels"][:6]))
    if info["expected"]:
        want, vin = info["expected"], adc_metrics.transfer_inputs(text)
        for name, kinds in (("transfer_codes", ("center", "edge")), ("overrange_codes", ("overrange",))):
            wrong = [f"conversion {k} vin={vin[k]:.2f} V read {codes[k - adc_metrics.WARMUP]}, want {want[k]}"
                     for k in range(adc_metrics.WARMUP, info["conversions"])
                     if codes[k - adc_metrics.WARMUP] != want[k] and adc_metrics.input_kind(vin[k]) in kinds]
            print(f"{name}:", "ok" if not wrong else "; ".join(wrong))
    if info["tone_bin"]:
        sndr, sfdr = adc_metrics.spectrum(codes, info["tone_bin"])
        ideal = "22.89 dB" if info["tone_bin"] == 1 else "20.3 dB"
        print(f"SNDR {sndr:.2f} dB (tone bin {info['tone_bin']}; an ideal quantizer gives {ideal} on this record)")
    pins = ", ".join(f"{p} {m[f'power_{p}_w'] * 1e3:.3f}" for p in adc_metrics.PINS)
    print(f"power {m['power_w'] * 1e3:.3f} mW (by pin, mW: {pins})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
