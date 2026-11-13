# Review: sky130-flash-adc-3bit-pvt

```text
Task: sky130-flash-adc-3bit-pvt (3-bit 50 MS/s flash ADC, single-ended input, 0.6-1.4 V references)
Reviewer: task maintainers, 2026-11-13
Evidence: instruction.md, spec/README.md, spec/signoff-rules.md, task.toml, both public and both
  hidden benches, testbench/analyze_adc.py, verify.py, test.sh, both Dockerfiles,
  environment/starter/signoff/, the reference, 9 mutants, 2 alternatives.
  tools/check_task.sh on eda-f (podman): both images build; the agent image runs as `agent`
  with starter, spec, checker, guide and ngspice; signoff copies identical to tests/; reference 1,
  starter 0, every mutant below 1, every alternative 1 (table below).
  signoff/run_bench.py in the agent image as `agent`: the reference runs clean on both public
  benches (exit 0) and analyze_adc.py prints every graded metric (transfer: all codes right,
  0.414 mW; sine: 22.89 dB SNDR, 25.64 dB SFDR, 0.405 mW); the reservoir mutant exits 3 with
  "SIGNOFF RULE: stored energy ..."; the reference with one inverter's NMOS bulk on vdd exits 3
  with "SIGNOFF RULE: junction forward biased: xadc.xe11.xn ... Vbs=1.20546 > 0.4", which plain
  ngspice does not report; /app is left unchanged (git status clean).
  Reference: reward 1 in 32 s (12 ngspice runs, 4 workers; the V2.1 verifier took about 59 s).
  task.toml parses with Harbor 0.25.0 TaskConfig (the pinned release).
  Diagnostics (plain ngspice, not shipped): the transfer bench with inputs 0.15 LSB from each
  threshold, and a ramp-through-threshold aperture bench, at 9 PVT points.
  Not run: native Harbor oracle/nop runs.
Novelty: the repository's only data-converter example; a
  single-ended 3-bit converter with a static (threshold-window) and a dynamic (coherent sine)
  contract, the smallest complete ADC a contributor's converter task should be compared with.
```

## Findings

| # | Severity | Owner | Finding | Status |
|---|---|---|---|---|
| 1 | P1 | Task author | Supply and reference power were computed by post-processing raw waveforms, so no bench had a `meas tran ... avg` window and the stored-energy rule never ran; the input pin was not metered; the clock was a separate 0.25 mW limit. A charged reservoir could hide power. | Fixed: both benches meter `vdd`, `vrefp`, `vrefn`, `clk` and `vin` at the pins with `meas tran ... avg` windows (references and clock through 10 ohm and 50 ohm sources); `power_1mw` grades the net sum. The reservoir mutant now scores 0 on the stored-energy rule; the clock- and input-pin mutants fail power through their pin share (metering proof below). |
| 2 | P1 | Task author | INL/DNL from a 2.2 us slow ramp made up most of the run time. | Removed (owner decision): `pvt_dnl`, `pvt_inl` and the ramp bench are gone. Static accuracy is graded by inputs 0.3 LSB either side of every threshold inside the full-rate transfer bench (`thresholds_0p3lsb`), which also replaces the ramp's absolute-threshold-error check at no extra simulation. The spec lists what is not scored. |
| 3 | P1 | Task author | Sine samples were only 10 mV (0.1 LSB) from thresholds, limits were 17 dB SNDR / 21 dB SFDR (far below the 3-bit ideal) plus a redundant normalized-ENOB check. Measured aperture: the reference resolves its input 0.36-0.92 ns after the clock source edge across PVT, and a 16-sample near-Nyquist record cannot keep 0.15 LSB of margin over such a spread at a higher tone (best 0.07 LSB at 21.9 MHz and 0.11 LSB at 15.6 MHz over that band; 0.11 LSB at 9.4 MHz over 0-1 ns). | Fixed: one hidden 16-sample record at 3.125 MHz, 0.37 V, phase 52 degrees; every ideal sample is at least 0.159 LSB from a threshold for any sampling instant 0-1.5 ns after the edge, so the ideal 22.89 dB / 25.64 dB is stable. Limits 22 dB and 24.5 dB (0.89 dB and 1.14 dB below the ideal and the reference). Full-rate steps are graded by the transfer bench; the spec says no tone above 3.125 MHz is graded. ENOB dropped. |
| 4 | P1 | Task author | Coverage claimed PVT but ran three transfer, three ramp and two sine points, none at `ss` or slow/cold/low supply. Adding `ss`/1.62 V/-40 C showed the reference's standard-Vt NMOS StrongARM failing there: at the 0.7 V threshold it took about 5 ns to resolve while the input kept moving, so a sine sample that started at 0.64 V read code 1. | Fixed: 7 transfer points and 5 sine points over all five corners, including `ss`/1.62 V/-40 C and `ff`/1.98 V/125 C, listed in the spec as representative coverage. The reference uses LVT input pairs. |
| 5 | P1 | Task author | Reference plausibility and hidden flaws: the clock pin drove all seven comparators directly; each comparator output fed a NAND latch input whose load depended on the latch state (hysteresis: at `tt` inputs 0.3 LSB below thresholds 2 to 7 read high when reached from above); 300 fF tap capacitors let kickback move thresholds more than 0.3 LSB at `ff`/`fs` 1.98 V/125 C. The old ramp, always rising, hid the last two. | Fixed: a two-inverter clock buffer (the pin sees one small gate, 0.1 uW), equal inverters before a NOR latch, 2 pF taps. With inputs 0.15 LSB from each threshold the reference converts every code at 9 PVT points, so its thresholds are within 0.15 LSB. No unused cells. |
| 6 | P1 | Task author | The public transition bench and the hidden one used the same kind of sequence (code centers only) and the public sine was the hidden one at another phase. | Fixed: the hidden transfer bench reaches every 0.3 LSB input from a code on the other side of that threshold, with full-scale jumps and the 3/4 transition both ways; the public one is ascending with each threshold's pair back to back. The public sine uses 0.36 V and another phase. The spec states both differences. |
| 7 | P2 | Task author | `test.sh` ran `signoff_ngspice.py` as a second program after the verifier. | Fixed: `test.sh` runs `check_circuit.py` and then `verify.py` once; rule violations are scored inside the verifier. |
| 8 | P2 | Task author | Old packaging: long instruction with the rules inline, `task.toml` with release, revision and checker keys and no agent user. | Fixed: short instruction, contract in `spec/README.md`, rules trimmed in `spec/signoff-rules.md` (ideal R and C syntax, transient ratings and stored energy apply here; DC-sweep exemption and inductors removed), `task.toml` in the standard shape. Limits match in instruction, spec, `task.toml` and `verify.py`. |
| 9 | P2 | Task author | The output-timing limit (outputs valid from 9.5 ns after the edge) was loose against the reference, whose latest 0.5·vdd crossing is 1.62 ns at `ss`/1.62 V/-40 C, and no mutant showed the check failing. | Fixed: outputs must be valid from 3 ns to 19 ns after the edge. Both alternatives pass; the new `slow-outputs` mutant (outputs settling about 6 ns after the edge) fails only `output_timing`. |
| 10 | P2 | Task collector | The shared stored-energy message leaves the point label empty (`bench tb_transfer.spi at , window ...`): `signoff_ngspice.run` passes `label: ""`. | Open: cosmetic, outside this task. |

Reference worst cases (limit in brackets):

| Check | Worst | Where |
|---|---|---|
| `code_centers` | 0 wrong of 15 centers per run | all 7 transfer points |
| `thresholds_0p3lsb` | 0 wrong of 14 per run (also 0 wrong at 0.15 LSB, diagnostic) | all 7 transfer points |
| `output_timing` | no level violation; latest crossing 1.62 ns (valid from 3 ns) | `ss`/1.62 V/-40 C |
| `sndr_22db` | 22.89 dB (>= 22), equal to the ideal quantizer | all 5 sine points |
| `sfdr_24p5db` | 25.64 dB (>= 24.5), equal to the ideal quantizer | all 5 sine points |
| `power_1mw` | 0.633 mW (<= 1): vdd 0.474, vrefp 0.278, vrefn -0.120, clk 0.000, vin 0.000 | transfer `ff`/1.98 V/125 C |

1 mW is a round budget for this converter class; the reference uses 63 % of it and the
track-and-hold alternative 0.73 mW, so a design with a different front end still fits.

## Metering proof

| Submission | Metered power, worst run | What the grader did |
|---|---|---|
| `dummy-clock-load` (two extra drivers switching 4 pF each from vdd) | 2.248 mW (vdd 2.090) | 0.83: fails only `power_1mw` |
| `reservoir-hidden-power` (the same drivers on a 20 nF rail fed through 100 kohm) | 0.620 mW without the signoff rules (plain ngspice, `ff`/1.98 V/125 C), so it would pass power | 0: stored-energy rule, e.g. 6.49e5 fJ released against 5 % of 3.23e5 fJ delivered, in all 7 runs before the gate |
| `clock-pin-termination` (500 ohm from clk to vss) | 3.748 mW (clk 3.111) | 0.83: fails only `power_1mw` |
| `input-pin-termination` (1 kohm from vin to vss) | 1.684 mW (vin 1.051) | 0.83: fails only `power_1mw` |

The track-and-hold alternative draws 0.040 mW through `vin` (its 12 pF hold capacitor), which
the metering counts as it should.

## Reward-hacking checks

`tools/check_task.sh tasks/sky130-flash-adc-3bit-pvt` on eda-f:

```text
ok     reference                          reward=1.0      (want == 1)   33s
ok     starter                            reward=0        (want < 1)    1s
ok     mutant-clock-pin-termination       reward=0.833333333333 (want < 1)   32s
ok     mutant-comparator-inputs-swapped   reward=0.0      (want < 1)   26s
ok     mutant-dummy-clock-load            reward=0.833333333333 (want < 1)   34s
ok     mutant-gray-code-encoder           reward=0.0      (want < 1)   24s
ok     mutant-input-pin-termination       reward=0.833333333333 (want < 1)   32s
ok     mutant-low-ohm-ladder              reward=0.833333333333 (want < 1)   34s
ok     mutant-reservoir-hidden-power      reward=0        (want < 1)   33s
ok     mutant-slow-outputs                reward=0.833333333333 (want < 1)   32s
ok     mutant-tap3-low-0p24lsb            reward=0.833333333333 (want < 1)   32s
ok     alt-double-tail-one-hot-encoder    reward=1.0      (want == 1)   38s
ok     alt-track-and-hold-front-end       reward=1.0      (want == 1)   39s
```

| Submission | What the grader did |
|---|---|
| Reference | 1 |
| Empty starter | 0: `check_circuit.py` rejects it ("subcircuit flash_adc_3bit has no approved leaf"), no simulation |
| Reference with an NMOS bulk on `vdd` (public bench, `run_bench.py`) | exit 3: junction forward biased (Vbs 1.21 V) |
| `gray-code-encoder` (wrong code mapping) | 0: nominal gate, 23 wrong codes |
| `comparator-inputs-swapped` | 0: nominal gate, 31 wrong codes |
| `low-ohm-ladder` (8 x 75 ohm) | 0.83: power 1.473 mW, everything else passes |
| `dummy-clock-load`, `reservoir-hidden-power`, `clock-pin-termination`, `input-pin-termination` | see the metering proof |
| `slow-outputs` (200 kohm in series with each output) | 0.83: outputs valid only about 6 ns after the edge, fails `output_timing`; every code still reads correctly at 19 ns, SNDR, SFDR and power pass |
| `tap3-low-0p24lsb` (threshold 3 trimmed 24 mV low) | 0.83: SNDR 20.49 dB at every sine point; SFDR 25.52 dB, transfer and power pass |
| Alternative `double-tail-one-hot-encoder` (double-tail comparators, NAND latches, one-hot encoder, 8 x 400 ohm ladder) | 1 (worst power 0.529 mW) |
| Alternative `track-and-hold-front-end` (LVT transmission-gate track-and-hold on 12 pF ahead of the comparators) | 1 (worst power 0.732 mW) |


## Conclusion

pass (after the fixes above; finding 10 is for the task collector)

## Required next action

Fill in the empty point label in the shared stored-energy message (task collector). Run the
native Harbor oracle and nop runs when the examples are next released.
