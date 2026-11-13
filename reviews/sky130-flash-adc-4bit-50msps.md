# Review: sky130-flash-adc-4bit-50msps

```text
Task: sky130-flash-adc-4bit-50msps (fully differential 4-bit 50 MS/s flash ADC)
Reviewer: task maintainers, 2026-11-13
Evidence: instruction.md, spec/README.md, task.toml, all public and hidden benches,
  analyze_flash_adc.py, verify.py, test.sh, both Dockerfiles, the reference,
  5 mutants, 2 alternatives. tools/check_task.sh on eda-f (podman): both images
  build; agent image runs as `agent` with starter, spec, checker, guide and ngspice.
  Native Harbor 0.25.0 runs (docker): oracle reward 1, nop reward 0. Harbor 0.25.0
  TaskConfig parses task.toml. Reference: reward 1; a full verifier run takes about
  7 min alone (5 PVT points x 3 benches, 4 in parallel), up to 11 min when the host
  is shared.
Novelty: the repository's only data-converter example.
```

## Findings

| # | Severity | Finding | Status |
|---|---|---|---|
| 1 | P1 | Graded at tt/1.8 V/27 C only. | Fixed: 1.71–1.89 V, −40 to 125 C, five corners; five unlisted points. |
| 2 | P0 | The old reference failed at the slow, cold, low-supply corner: 20 pF hold capacitors behind small switches tracked only 72 % of a one-code step in the 4.9 ns of acquisition. | Fixed: switches four times wider, 5 pF hold capacitors. At ±10 % supply it still misses one code at sf/1.62 V/−40 C, so the contract is ±5 %. |
| 3 | P1 | Clock-pin and signal-input power were diagnostics only. | Fixed: power from `vdd`, `vrefp`, the clock pin and both inputs all count toward 5 mW. |
| 4 | P2 | `vrefp` was an ideal source with no output impedance. | Fixed: 10 ohm output resistance; its current counts toward power. |
| 5 | P2 | The SNDR and SFDR limits had no stated derivation. | Fixed: the spec gives the ideal 4-bit values for this record (25.3 dB SNDR, 30.0 dB SFDR) and what 22 dB and 26 dB allow. |

Reference worst cases: SNDR 25.04 dB and SFDR 30.05 dB (sf/1.71 V/−40 C),
|INL| 0.146 LSB, |DNL| 0.146 LSB, power 2.58 mW; every code correct at every
point.

## Reward-hacking checks

| Submission | What the grader did |
|---|---|
| Reference | 1 |
| Empty starter (Harbor nop) | 0 |
| Reference plus an independent voltage source | 0: rejected by `check_circuit.py` before simulation |
| Reference plus an ideal resistor | 1: correct, the contract allows ideal R and C |
| `comparator-inputs-swapped` | 0: 16 transfer errors at nominal |
| `ladder-tap-off-by-one` | 0: code 4 missing at nominal |
| `output-bits-swapped` | 0: 8 transfer errors at nominal |
| `low-ohm-ladder` | 0: code error at ss/1.71 V/125 C and at one more point |
| `weak-sample-switch` (the old switch width) | 0: passes at nominal, misses code 9 only at sf/1.71 V/−40 C |
| Alternative `double-tail-one-hot-encoder` (different structure) | 1 |
| Alternative `wider-comparators-200-ohm-ladder` (different sizing) | 1 |

## Conclusion

pass

## Required next action

None.
