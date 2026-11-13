# Review: sky130-ota-5t-gain40-pm60-noise50uv-pvt

```text
Task: sky130-ota-5t-gain40-pm60-noise50uv-pvt (current-biased five-transistor OTA)
Reviewer: task maintainers, 2026-11-13
Evidence: instruction.md, spec/README.md, task.toml, all public and hidden benches,
  verify.py, test.sh, both Dockerfiles, the reference, 6 mutants, 2 alternatives.
  tools/check_task.sh on eda-f (podman): both images build; agent image runs as
  `agent` with starter, spec, checker, guide and ngspice. Native Harbor 0.25.0 runs
  (docker): oracle reward 1, nop reward 0. Harbor 0.25.0 TaskConfig parses task.toml.
  Reference: reward 1 in 12 s (1 nominal gate run, 6 grid runs, 12 rejection runs).
Novelty: the repository's only five-transistor OTA example; it is the minimal
  amplifier a contributor's op-amp task should be compared against.
```

## Findings

| # | Severity | Finding | Status |
|---|---|---|---|
| 1 | P0 | CMRR/PSRR were measured with the output's DC level left wherever a systematic offset put it. A higher-gain design was pushed toward a rail and read lower (a self-cascode load read 22–35 dB), while the reference read 56 dB at a lucky symmetric point. | Fixed: each copy is DC-biased by unity feedback through a 1 GH inductor (open at 1 kHz). The reference now reads 46.2 dB; the limit is 40 dB. |
| 2 | P1 | Public benches were the hidden benches at named PVT points; all 27 graded points and the 3 rejection points were listed. | Fixed: public benches run only the nominal point; grading adds 45/55 uA bias, an 85 C point and unlisted rejection points. |
| 3 | P1 | Input sources were not metered: a circuit could draw power from the ideal `vinp` source. | Fixed: power adds the `vinp` source's power; mutant `input-leakage` draws 7.2 mW and fails. |
| 4 | P2 | `ibias` was an ideal current source with no source impedance. | Fixed: 1 Mohm output resistance; its current comes from the metered supply. |
| 5 | P2 | Reference UGB margin 1.6 % at the hidden worst point. | Fixed: narrower load mirror; worst UGB 107 MHz. Gain margin stays about 4 % (41.6 dB): longer loads trade UGB for gain, and this is the balance the 0.5 mW budget allows. |

Reference worst cases: gain 41.56 dB (ff/1.62 V/125 C/55 uA), UGB 107 MHz
(ss/1.62 V/125 C/45 uA), phase margin 68.1 deg, power 0.475 mW, noise
39.3 uVrms, CMRR 46.2 dB, PSRR 38.8 dB.

## Reward-hacking checks

| Submission | What the grader did |
|---|---|
| Reference | 1 |
| Empty starter (Harbor nop) | 0 |
| Reference plus an ideal resistor, or plus an independent voltage source | 0: rejected by `check_circuit.py` before simulation |
| `swapped-inputs` (positive feedback) | 0: nominal gate, loop gain −87 dB |
| `mirror-on-output-side` | 0: nominal gate, loop gain −1.5 dB |
| `short-channel-load` | 0: nominal gate, loop gain 20.7 dB |
| `starved-tail` | 0: nominal gate, UGB below 100 MHz |
| `oversized-tail` | 0.43: power 1.87 mW, gain 37.7 dB |
| `input-leakage` | 0.57: power 7.17 mW from the input source |
| Alternative `self-cascode-tail` (different structure) | 1 |
| Alternative `short-pair-narrow-load` (different sizing) | 1 |

A PMOS-input pair cannot meet 40 dB and 100 MHz together within 0.5 mW (best
nominal 33.7 dB at 90 MHz, or 40 dB at 49 MHz), so the contract in effect
needs an NMOS pair.

## Conclusion

pass

## Required next action

None.
