# Review: sky130-gilbert-cell-mixer-2p4ghz-pvt-mismatch

```text
Task: sky130-gilbert-cell-mixer-2p4ghz-pvt-mismatch (2.4 GHz double-balanced Gilbert-cell mixer)
Reviewer: task maintainers, 2026-11-13
Evidence: instruction.md, spec/README.md, spec/signoff-rules.md, task.toml, all public and
  hidden benches, verify.py, test.sh, both Dockerfiles, environment/starter/signoff/, the
  reference, 8 mutants, 2 alternatives; the V2.1 task it was rebuilt from.
  tools/check_task.sh on eda-f (podman): both images build; the agent image runs as `agent`
  with starter, spec, checker, guide and ngspice; reference 1, starter 0, every mutant below 1,
  every alternative 1 (table below).
  signoff/run_bench.py in the agent image as `agent`: the reference runs clean on all three
  public benches (exit 0) and prints every measurement; the reference with XRFP's bulk on vdd
  exits 3 with "SIGNOFF RULE: junction forward biased: xmix.xrfp ... Vbs=0.798 > 0.4 at t=0 s",
  which plain ngspice does not report; /app is unchanged afterwards (git status clean).
  The stored-energy accounting finds all five averaging windows of tb_rf.spi (vdd and the four
  RF/LO sources).
  Measurement accuracy: rerunning sample points with reltol 1e-5 and a 4.9 ps step moved
  IIP3 by at most 0.06 dB, conversion gain by 0.07 dB and pin power by about 0.4 uW.
  Reference: reward 1 in 12.8 s of verifier time (79 ngspice runs on 4 workers).
  task.toml parses with Harbor 0.25.0 TaskConfig (the pinned release).
  Not run: native Harbor oracle/nop runs.
Novelty: the only mixer example. benchmarks/v3/sky130-gilbert-cell-mixer-pvt is a 20 MHz
  Gilbert cell with no mismatch or input imbalance; this task is a deliberate variant at
  2.3-2.5 GHz RF whose isolation and spur limits must hold under 2 % / 2 degree imbalance and
  local mismatch, with metered RF and LO ports.
```

`tools/check_task.sh tasks/sky130-gilbert-cell-mixer-2p4ghz-pvt-mismatch` on eda-f:

```text
ok     reference                          reward=1.0      (want == 1)   13s
ok     starter                            reward=0        (want < 1)    1s
ok     mutant-lo-output-coupling          reward=0.875    (want < 1)   13s
ok     mutant-lo-pin-bleed                reward=0.875    (want < 1)   14s
ok     mutant-standard-vt-quad            reward=0.875    (want < 1)   13s
ok     mutant-starved-tail                reward=0.875    (want < 1)   13s
ok     mutant-unbalanced-quad             reward=0.75     (want < 1)   14s
ok     mutant-unequal-loads               reward=0.875    (want < 1)   13s
ok     mutant-wasted-bias-branch          reward=0.875    (want < 1)   14s
ok     mutant-weak-degeneration           reward=0.75     (want < 1)   14s
ok     alt-common-tail-source-resistors   reward=1.0      (want == 1)   13s
ok     alt-pmos-current-bleeding          reward=1.0      (want == 1)   15s
```

## Findings

| # | Severity | Owner | Finding | Status |
|---|---|---|---|---|
| 1 | P0 | Task author | The spectra were not coherent. The single-tone bench linearized 10.02 ns into 334 samples (99.8 MHz bins) and the linearity bench 20 ns into 2001 samples, so tones fell between bins. On a pure test signal the 334-sample record leaked a 200 MHz tone into the 100 and 300 MHz bins at -47 dB and read a 2.2 GHz tone 0.9 dB low. In the two-tone bench the IF fundamentals leaked into the 150/300 MHz IM3 bins at about the IM3 level, so IIP3 read about 4 dB low: the V2.1 reference was reported at -2.69 dBm, and the same circuit measures +1.5 to +2.0 dBm at tt/1.80 V/27 C with a coherent record. The -4 dBm limit had been calibrated against the wrong number. | Fixed: both benches take exactly 1024 samples 19.53125 ps apart over a 20 ns record (50 MHz bins; every RF, LO, IF, IM3 and spur frequency is on a bin). Leakage on the test signal is now -139 dB. The IIP3 formula, slopes and tone flatness are computed in the bench, so the public bench prints what the grader scores. |
| 2 | P1 | Task author | Power metered only `vdd`. The RF and LO inputs are 0.9 V sources through 50 ohm, so a design could draw bias current from them unmetered. | Fixed: the bench averages each pin's voltage times the current driven into it (`meas tran ... avg` of the source current, so the stored-energy rule covers these windows), and `pin_power_10uw` limits each of `rfp`, `rfn`, `lop`, `lon` to 10 uW. A separate pin limit, not a share of `power_2mw`: current drawn at 0.9 V costs less than from `vdd`, so a combined limit rewards moving current onto the pins. Reference worst: rfp -0.05 uW, lop -0.42 uW (capacitive drive nets nothing; LO pins slightly negative). Mutant `lo-pin-bleed` (LO-port current bleeding into the RF-pair drains) fails only this check, at 76 uW per LO pin. |
| 3 | P1 | Task author | The public benches were the hidden benches: `tb_rf_tt_2p4ghz.spi` was `tb_rf.spi` at 1.98 V, and the public linearity bench was the hidden one at `ss`. | Fixed: public benches run only the nominal point (single tone and linearity) and one `tt_mm` seed with imbalance at 2.35 GHz. Hidden grading adds the `sf`/`fs` corners, both supply and temperature extremes at every corner, a 0.30-0.40 V LO amplitude range and a 45-55 uA reference-current range (both new, stated in the spec), RF at 2.30/2.35/2.45/2.50 GHz, imbalance of either sign and 12 unseen mismatch seeds. The spec states this coverage and that it is representative. |
| 4 | P1 | Task author | Coverage stopped at 10 representative tt/ss/ff points and three linearity points. On the wider grid the V2.1 reference failed: conversion gain -0.87 dB at sf/1.62 V/125 C/2.5 GHz/0.30 V LO/55 uA, where its 3.3 kohm loads pulled the output common mode to 0.49 V and squeezed the quad; 1.5-1.7 dB at tt/1.62 V/125 C/55 uA. | Fixed: 1 nominal + 40 grid + 5 imbalance + 12 mismatch single-tone runs and 21 linearity runs (nominal plus every corner at both supply and both temperature extremes). The reference was re-sized: loads 3.3 -> 2.4 kohm, tails 130 -> 140 um, RDEG 650 -> 550 ohm, output capacitors 75 -> 50 fF. |
| 5 | P1 | Task author | No mutants or alternative designs. | Fixed: 8 mutants (one per scored check, each failing for its intended reason, table below) and 2 structurally different alternatives that score 1. |
| 6 | P2 | Task author | Limits not calibrated to the corrected measurements. | IIP3 -4 -> -2 dBm (worst -0.43 dBm, 1.6 dB margin) and compression 2 -> 1 dB (worst 0.67 dB). Kept: gain 3 dB (4.11), spur -30 dBc (-34.0), power 2 mW (1.585 mW). Isolation stays -45 dB (worst -51.9 dB): it is set by mismatch, -50 dB would leave 1.9 dB over 12 seeds, and an exploratory 20-seed run already reached -50.9 dB. |
| 7 | P2 | Task author | The IIP3 validity window required IM3 to grow at least 1.8 dB/dB, which could fail a more linear design whose IM3 grows slower. Since the lower of the two extrapolations is scored, slower growth only makes the score more pessimistic. | Fixed: only the upper IM3 slope (4 dB/dB) and the fundamental slope (0.8-1.2) remain; the spec says why. |
| 8 | P2 | Task author | `test.sh` ran `signoff_ngspice.py` as a second program after `verify.py` to rewrite the reward, and the preflight did not check the subcircuit interface. | Fixed: OTA-style fail-closed `test.sh`, one `verify.py` entrypoint writing results through `utils.write_results` (which zeroes the reward on a signoff-rule violation); the checker runs with `--allow-ideal R C --require-subcircuit gilbert_mixer ...`, the same command the spec prints. |
| 9 | P2 | Task author | `task.toml` carried repository keys (`benchmark_release`, `checker_allow_ideal`, `revision`, ...) and no agent user, timeouts or network settings; `instruction.md` was a long datasheet with agent-behaviour boilerplate and the signoff rules inline. | Fixed: `task.toml` in the standard's shape; a short `instruction.md`; contract in `spec/README.md`, rules in `spec/signoff-rules.md`, each stated once. |
| 10 | P2 | Task author | The reference current was an ideal current source with no finite impedance, at exactly 50 uA. | Fixed: 1 Mohm in parallel (as in the OTA example), current anywhere in 45-55 uA. Its current comes from `vdd`, so `power_2mw` includes it. |
| 11 | P2 | Task author | The signoff rules, inline in `instruction.md`, included rules that cannot apply here (ideal inductors, the DC-sweep exemption). | Fixed: trimmed to R/C syntax, multipliers and mismatch averaging, device list, ratings at t = 0 and through every transient, junctions at the operating point, model check, stored energy (the single-tone averaging windows) and convergence. |
| 12 | P2 | Task author | The averaging windows end one sample before 25 ns because the record must be exactly 1024 samples for the FFT; the last 19.5 ps of the 20 ns period is left out. | Accepted and stated in the spec: it moves the averages by about 0.1 % of their ripple (measured: 0.1 uW of pin power, 0.01 mV of offset, 1e-5 of supply power). |
| 13 | P2 | Task collector | The V2.1 task copied its own `SKY130_NETLIST_GUIDE.md` (with the geometry-preflight section) over `/opt/analog-arena/`; this example uses the shared `analog-arena/` copy as the OTA example does. The geometry section only applies to tasks run with `--enforce-sky130-geometry`, which this one is not. | Open: keep the shared guide in step with the V2.1 one if that section is wanted everywhere. |

Reference worst cases (limit in brackets):

| Check | Worst | Where |
|---|---|---|
| conversion gain | 4.11 dB (≥ 3) | sf/1.62 V/125 C/2.50 GHz/LO 0.30 V/45 uA |
| LO-to-IF | -52.7 dB (≤ -45) | tt_mm seed 52001, 2.35 GHz, +2 %/+2 %/+2 deg |
| RF-to-IF | -51.9 dB (≤ -45) | tt_mm seed 52005, 2.40 GHz, +2 %/+2 %/+2 deg |
| LO-to-RF | -87 dB (≤ -45) | tt_mm seed 52005 |
| spur | -34.0 dBc (≤ -30) | tt_mm seed 52001 (LO feedthrough) |
| output offset | 5.7 mV (≤ 30) | tt_mm seed 52005 |
| output common mode | 0.733 V (≥ 0.45) | sf/1.62 V/125 C/55 uA |
| headroom to vdd | 0.697 V (≥ 0.20) | fs/1.62 V/-40 C/45 uA |
| power | 1.585 mW (≤ 2) | sf/1.98 V/125 C/55 uA |
| pin power | -0.05 uW RF, -0.38 uW LO (≤ 10 each) | ss/sf, 1.62 V |
| IIP3 | -0.43 dBm (≥ -2); slopes 0.98-0.99, IM3 2.72-3.23; tone difference 0.86 dB | fs/1.62 V/-40 C/LO 0.30 V/45 uA |
| compression | 0.67 dB (≤ 1) | fs/1.98 V/-40 C/LO 0.30 V/45 uA |

The operating-point limits (offset, common mode, headroom) are loose guards on the IF
interface, not performance targets; a squeezed quad fails conversion gain first.

## Reward-hacking checks

| Submission | What the grader did |
|---|---|
| Reference | 1 |
| Empty starter | 0 |
| Reference with XRFP's bulk on `vdd` (via `run_bench.py`) | exit 3: signoff rule, junction forward biased (Vbs 0.80 V) |
| `lo-pin-bleed` (RF-stage current bled from the LO port) | 0.875: only `pin_power_10uw` fails, 76 uW into `lop`; vdd power and every RF metric pass |
| `unbalanced-quad` (one switch 30 um instead of 40) | 0.75: isolation (RF-IF -40.7 dB) and spur (-26.5 dBc) |
| `standard-vt-quad` (nfet_01v8 switches) | 0.875: conversion gain 1.46 dB at sf/1.62 V/125 C |
| `starved-tail` (tails 90 um) | 0.875: conversion gain 2.13 dB at ss/1.62 V/125 C |
| `weak-degeneration` (RDEG 50 ohm) | 0.75: IIP3 -3.3 dBm and compression 1.63 dB at ff/1.98 V/-40 C |
| `lo-output-coupling` (2.4 fF from `lop` to `ifoutp`) | 0.875: only spur fails (-26.9 dBc); LO-IF isolation still passes at -46.4 dB |
| `wasted-bias-branch` (extra 350 uA mirror from vdd) | 0.875: only power fails, 2.42 mW |
| `unequal-loads` (2.6 / 2.2 kohm) | 0.875: only the output operating point fails, 133 mV offset |
| Alternative `common-tail-source-resistors` (one tail, resistive source degeneration, 80 um RF pair) | 1 |
| Alternative `pmos-current-bleeding` (PMOS bleed sources, 3.6 kohm loads, smaller quad) | 1 |

The spur check is in practice a tighter bound on LO feedthrough than `isolation_45db`: the
largest non-IF output tone of the reference and every mutant is the LO, and -30 dBc of a
roughly 80 mV IF is about -49 dB of the 0.7 V LO. The two checks normalize differently (to the
IF and to the LO drive), and `lo-output-coupling` shows a design that passes isolation and
fails spur, so both are kept.

A 7.5 % switch imbalance (XQ1 37 um) still scores 1; the grader is not sensitive to imbalance
that the mismatch and imbalance cases themselves absorb.

## Conclusion

pass (after the fixes above; finding 13 is a repository question)

## Required next action

Run the native Harbor oracle and nop runs and the Harbor TaskConfig parse when the examples
are next released (task collector).
