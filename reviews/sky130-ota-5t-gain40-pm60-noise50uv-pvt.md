# Review: sky130-ota-5t-gain40-pm60-noise50uv-pvt

```text
Task: sky130-ota-5t-gain40-pm60-noise50uv-pvt (current-biased five-transistor OTA)
Reviewer: task maintainers, 2026-11-13
Evidence: instruction.md, spec/README.md, spec/signoff-rules.md, task.toml, all public and
  hidden benches, verify.py, test.sh, both Dockerfiles, environment/starter/signoff/, the
  reference, 9 mutants, 3 alternatives.
  tools/check_task.sh on eda-f (podman): both images build; the agent image runs as `agent`
  with starter, spec, checker, guide and ngspice; reference 1, starter 0, every mutant below 1,
  every alternative 1 (table below).
  signoff/run_bench.py in the agent image as `agent`: the reference runs clean on all four
  public benches (exit 0); the reference with the bias diode's bulk on vdd exits 3 with
  "SIGNOFF RULE: junction forward biased at an operating point ... Vbs=1.43 > 0.4", which
  plain ngspice does not report; the run leaves /app unchanged (git status clean).
  Reference: reward 1, verifier wall clock 18 s (26 ngspice processes: 1 nominal gate run,
  10 grid runs, and one run per rejection bench and corner covering 123 rejection measurements).
  Not rerun for this revision: native Harbor oracle/nop runs and the Harbor TaskConfig parse
  (task.toml is unchanged).
Novelty: the repository's only five-transistor OTA example; it is the minimal amplifier a
  contributor's op-amp task should be compared against.
```

## Findings

| # | Severity | Owner | Finding | Status |
|---|---|---|---|---|
| 1 | P0 | Task author | CMRR/PSRR were measured with the output's DC level left wherever a systematic offset put it, so a higher-gain design read lower rejection. | Fixed earlier: each rejection copy is DC-biased by unity feedback through a 1 GH inductor. The new `self-cascode-load` alternative (higher loop gain than the reference) scores 1 and locks this in. |
| 2 | P1 | Task author | Public benches were the hidden benches at named PVT points. | Fixed earlier: public benches run only the nominal point. |
| 3 | P1 | Task author | Input sources were not metered. | Fixed earlier: power adds the `vinp` source's power; mutant `input-leakage` fails at 7.2 mW. |
| 4 | P1 | Task author | Only `tt`/`ss`/`ff` were graded; the skewed corners were missing. Adding `sf` and `fs` showed the old reference failing: CMRR 31.5 dB and PSRR 29.5 dB at sf/1.62 V/-40 C (its narrow load's source-gate drop and the tail left the pair and tail almost no drain headroom). It was also at 39.3 dB CMRR at ss/1.62 V/-40 C, a point the old rejection set did not grade. | Fixed: the grid has all five corners (120 points); rejection is graded at the nominal point and at every corner × {1.62, 1.98 V} × {-40, 125 C} × {45, 55 uA} (41 points per bench). The reference was re-sized (limits unchanged): load mirror W 40 → 100 um at L 0.9 um, pair L 1.0 → 0.9 um, tail mirror L 3 → 1.5 um. |
| 5 | P1 | Task author | Mutants only exercised gain, UGB and power; nothing showed that the phase-margin, noise or rejection checks can fail a design. | Fixed: `narrow-load` passes the whole grid and fails CMRR and PSRR; `feedback-gate-resistor` fails only phase margin; `input-series-resistor` fails noise. Added alternative `self-cascode-load`. |
| 6 | P2 | Task author | The public benches were documented as not applying the signoff rules, with no way to apply them. | Fixed: `spec/README.md` documents `python3 /app/signoff/run_bench.py testbench/<bench>.spi`. Two defects in `run_bench.py` were fixed: it passed a relative deck path, so `signoff_ngspice` did not recognize the deck and ran plain ngspice with no rules (a forward-biased junction was not reported); and it left its rewritten copies and `__pycache__` in `/app`. |
| 7 | P2 | Task author | `spec/signoff-rules.md` carried rules that cannot apply here: ideal R/C/L syntax (ideal passives are forbidden), stored-energy accounting and transient rating clauses (no transient bench), the DC-sweep exemption (no sweeps). | Fixed: trimmed to simulator setting, diffusion geometry, multipliers and device list, ratings at operating points, model check and convergence retry. |
| 8 | P2 | Task author | `spec/README.md` stated power metering three times and the 1 Mohm source twice. | Fixed: the 1 Mohm source is in Operating conditions only, metering in the Power definition only. |
| 9 | P2 | Task author | `tests/alts/short-pair-narrow-load.spi` header described a load it did not have. | Fixed with the re-sized alternative; the header matches the netlist. |
| 10 | P2 | Task author | With the rejection grid extended to 41 points per bench, one ngspice process per point took the reference's verifier run from 12 s to about 70 s. | Fixed: each rejection bench loops over its points like the grid bench, one process per bench and corner (15 instead of 123). Results are identical to one process per point (checked on all 123 points of the reference: 0 dB difference); the run takes 18 s with the full rejection coverage. |
| 11 | P2 | Task collector | The other two example tasks ship the same `run_bench.py`, with the relative-path defect of finding 6. | Open: outside this task. |

Reference worst cases over the full grid (limit in brackets):

| Check | Worst | Where |
|---|---|---|
| gain | 41.15 dB (≥ 40) | ff/1.62 V/125 C/55 uA |
| UGB | 106.2 MHz (≥ 100) | fs/1.62 V/125 C/45 uA |
| phase margin | 66.8 deg (≥ 60) | sf/1.98 V/-40 C/55 uA |
| power | 0.476 mW (≤ 0.5) | sf/1.98 V/125 C/55 uA |
| noise | 40.5 uVrms (≤ 50) | fs/1.62 V/125 C/45 uA |
| CMRR | 44.8 dB (≥ 40) | sf/1.62 V/-40 C/55 uA |
| PSRR | 37.3 dB (≥ 30) | sf/1.62 V/-40 C/55 uA |

Gain and UGB trade against each other through the load length and the pair size at a fixed
0.5 mW budget; the chosen sizing keeps both about 1.2 dB and 6 % clear while giving rejection
4.8 dB of margin. A wider pair lowers UGB (its capacitance dominates), and LVT loads restore
rejection but cost 3–6 dB of gain, so neither was used.

## Reward-hacking checks

| Submission | What the grader did |
|---|---|
| Reference | 1 |
| Empty starter | 0 |
| Reference with the bias diode's bulk on `vdd` | 0: signoff rule, junction forward biased (Vbs 1.43 V) |
| `swapped-inputs` (positive feedback) | 0: nominal gate, loop gain −129 dB |
| `mirror-on-output-side` | 0: nominal gate, loop gain −7.4 dB |
| `short-channel-load` | 0: nominal gate, loop gain 19.6 dB |
| `starved-tail` | 0: nominal gate, UGB 20.5 MHz |
| `oversized-tail` | 0.43: power 1.89 mW, gain 38.6 dB |
| `input-leakage` | 0.57: power 7.17 mW from the input source |
| `feedback-gate-resistor` (pole inside the loop) | 0.57: phase margin 40.4 deg; gain, UGB, power, noise pass |
| `narrow-load` (load a third of the reference's width) | 0.71: whole grid passes; CMRR 28.0 dB, PSRR 25.9 dB at sf/1.62 V/-40 C |
| `input-series-resistor` (7 kohm on the `vinp` gate) | 0.29: noise 54.2 uVrms; UGB 84.8 MHz and phase margin 54.9 deg also fail |
| Alternative `self-cascode-tail` (different tail structure) | 1 |
| Alternative `self-cascode-load` (different load structure, gain 41.4 dB) | 1 |
| Alternative `short-pair-narrow-load` (different sizing) | 1 |

No defect was found that fails only the noise check. Input-referred thermal noise and UGB both
follow the pair's transconductance, a smaller pair loses gain before its flicker noise exceeds
50 uVrms, and a series input resistor also adds a pole with the gate capacitance. The
`input-series-resistor` mutant is kept because it shows the noise check failing.

A PMOS-input pair cannot meet 40 dB and 100 MHz together within 0.5 mW (best nominal 33.7 dB
at 90 MHz, or 40 dB at 49 MHz), so the contract in effect needs an NMOS pair.

## Conclusion

pass (after the fixes above; finding 11 is for the other two tasks)

## Required next action

Copy the `run_bench.py` fix to the other two example tasks (task collector). Rerun the native
Harbor oracle and nop runs when the examples are next released.
