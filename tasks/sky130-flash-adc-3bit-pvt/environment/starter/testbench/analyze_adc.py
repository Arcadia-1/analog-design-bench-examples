#!/usr/bin/env python3
"""Print the graded metrics of one public bench run.

    cd /app
    python3 signoff/run_bench.py testbench/tb_transfer_tt.spi > transfer.log   # or: ngspice -b testbench/... > ...
    python3 testbench/analyze_adc.py testbench/tb_transfer_tt.spi transfer.log

The bench file gives the supply, the expected codes (transfer bench) or the tone (sine bench); the log holds the
ngspice measurements. The metric definitions are the ones in /app/spec/README.md and match grading.
"""
import math
import re
import sys

WARMUP, LOW_MAX, HIGH_MIN, SAMPLES, FS = 2, 0.2, 0.8, 16, 50e6


def main() -> int:
    if len(sys.argv) != 3:
        print(__doc__)
        return 2
    bench, log = (open(p, errors="replace").read() for p in sys.argv[1:])
    vdd = float(re.search(r"(?m)^VSUP vdd vss (\S+)", bench).group(1))
    m = {}
    for line in log.splitlines():
        hit = re.match(r"^\s*([a-z]\w*)\s*=\s*([-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:e[-+]?\d+)?)", line, re.I)
        if hit:
            m[hit.group(1).lower()] = float(hit.group(2))
    if "SIGNOFF RULE:" in log:
        print("signoff rule broken (grading scores 0):", log.split("SIGNOFF RULE:", 1)[1].splitlines()[0].strip())
    if "power" not in m:
        print("no measurements in the log: did ngspice run?")
        return 1
    expected = re.search(r"(?m)^\* expected codes[^:]*: (.*)$", bench)
    sine = re.search(r"(?m)^VSIG vin vss SIN\(\S+ \S+ (\S+)", bench)
    count = len(expected.group(1).split()) if expected else WARMUP + SAMPLES
    codes, bad = [], []
    for k in range(WARMUP, count):
        code = 0
        for bit, weight in ((2, 4), (1, 2), (0, 1)):
            read, low, high = (m[f"s{k}_b{bit}{s}"] for s in ("", "_min", "_max"))
            if read > 0.5 * vdd:
                code += weight
                if low < HIGH_MIN * vdd:
                    bad.append(f"conversion {k} b{bit} fell to {low:.3f} V between 9.5 ns and 19 ns")
            elif high > LOW_MAX * vdd:
                bad.append(f"conversion {k} b{bit} rose to {high:.3f} V between 9.5 ns and 19 ns")
        codes.append(code)
    print("codes:", " ".join(map(str, codes)))
    print("output_timing:", "ok" if not bad else f"{len(bad)} violations: " + "; ".join(bad[:6]))
    if expected:
        want = [int(c) for c in expected.group(1).split()]
        vin = [float(v) for v in re.findall(r"\d+(?:\.\d+)?n \S+ \d+\.2n ([-\d.]+)", bench)]
        vin = [float(re.search(r"PWL\(0 (\S+)", bench).group(1))] + vin
        for kind, is_center in (("code_centers", True), ("thresholds_0p3lsb", False)):
            wrong = [f"conversion {k} vin={vin[k]:.2f} V read {codes[k - WARMUP]}, want {want[k]}"
                     for k in range(WARMUP, count)
                     if codes[k - WARMUP] != want[k] and (abs((vin[k] - 0.6) / 0.1 % 1 - 0.5) < 1e-6) == is_center]
            print(f"{kind}:", "ok" if not wrong else "; ".join(wrong))
    if sine:
        freq = float(sine.group(1).lower().replace("meg", "e6"))
        tone = round(freq * SAMPLES / FS)
        mean = sum(codes) / len(codes)
        power = []
        for b in range(1, SAMPLES // 2 + 1):
            re_ = sum((c - mean) * math.cos(2 * math.pi * b * i / SAMPLES) for i, c in enumerate(codes))
            im_ = sum((c - mean) * math.sin(2 * math.pi * b * i / SAMPLES) for i, c in enumerate(codes))
            power.append((re_ ** 2 + im_ ** 2) * (0.5 if b == SAMPLES // 2 else 1.0))
        others = [p for b, p in enumerate(power, start=1) if b != tone]
        signal = power[tone - 1]
        if signal > 0 and sum(others) > 0:
            print(f"SNDR {10 * math.log10(signal / sum(others)):.2f} dB, SFDR {10 * math.log10(signal / max(others)):.2f} dB"
                  f" (tone bin {tone}; an ideal quantizer gives 22.89 dB and 25.64 dB on these records)")
        else:
            print("SNDR/SFDR undefined: no tone or no error in the record")
    pins = ", ".join(f"{p} {m.get('power_' + p, math.nan) * 1e3:.3f}" for p in ("vdd", "vrefp", "vrefn", "clk", "vin"))
    print(f"power {m['power'] * 1e3:.3f} mW (by pin, mW: {pins})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
