#!/usr/bin/env python3
"""Simulate a public bench the way grading does, apply the signoff rules and print every metric.

Usage, from /app:  python3 signoff/run_bench.py testbench/tb_dc_tt.sim [--circuit circuit.sim] [--keep DIR]

Exit codes: 0 clean run, 2 the netlist check or the simulation failed, 3 a signoff rule is broken.
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bgr_signoff as bs  # noqa: E402


def show(name, values, unit, scale=1.0, fmt="{:.4f}", keys=("V", "C")):
    for key in sorted(values):
        value = values[key]
        where = " ".join(f"{k:g}" + u for k, u in zip(key if isinstance(key, tuple) else (key,), keys))
        print(f"  {name:28s} {where:14s} {fmt.format(value * scale)} {unit}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("bench")
    parser.add_argument("--circuit", default="circuit.sim")
    parser.add_argument("--keep", help="copy the simulation directory (decks and raw files) here")
    args = parser.parse_args()
    deck = open(args.bench).read().replace('include "../circuit.sim"', 'include "circuit.sim"')
    try:
        devices = bs.dut_devices(args.circuit)
        plots = bs.run_deck(deck, args.circuit, keep=args.keep)
    except bs.RunError as exc:
        print(f"RUN FAILED: {exc}")
        return 2
    violations = []
    for name, plot in plots.items():
        if name in bs.OP_ANALYSES or name == "tr":
            violations += bs.rule_violations(devices, plot, name, dc=name != "tr")
    print(f"{args.bench}: analyses {', '.join(sorted(plots))}")
    if "dc" in plots:
        m = bs.dc_metrics(plots)
        print("DC metrics")
        show("vref", m["vref"], "V")
        show("quiescent current", m["iq"], "uA", 1e6, "{:.2f}")
        show("temperature coefficient", m["tc_ppm"], "ppm/C", 1.0, "{:.1f}", ("V",))
        show("line regulation", m["line_mv_per_v"], "mV/V", 1.0, "{:.3f}", ("C",))
        show("load regulation", m["load_mv"], "mV", 1.0, "{:.3f}")
        show("disabled supply current", {k: v[0] for k, v in m["off"].items()}, "nA", 1e9, "{:.2f}")
        show("disabled enable current", {k: v[1] for k, v in m["off"].items()}, "nA", 1e9, "{:.2f}")
        show("disabled vref", {k: v[2] for k, v in m["off"].items()}, "V")
        show("op spread (nodesets)", m["nodeset_mv"], "mV", 1.0, "{:.3f}")
    if "ac" in plots and "nz" in plots:
        m = bs.ac_metrics(plots)
        print("10 kHz metrics")
        show("PSRR", m["psrr_db"], "dB", 1.0, "{:.1f}")
        show("output noise", m["noise"], "nV/sqrt(Hz)", 1e9, "{:.0f}")
    if "tr" in plots and "target" in plots:
        m = bs.startup_metrics(plots, bs.edge_end(deck))
        print("Startup metrics (from the end of the last edge)")
        print(f"  target operating point       {m['target']:.4f} V")
        print(f"  settling to +-{bs.SETTLE_BAND * 1e3:.0f} mV           {m['settle_s'] * 1e6:.2f} us")
        print(f"  overshoot above the target   {m['overshoot_v'] * 1e3:.2f} mV")
    for line in violations:
        print(f"SIGNOFF RULE: {line}")
    return 3 if violations else 0


if __name__ == "__main__":
    sys.exit(main())
