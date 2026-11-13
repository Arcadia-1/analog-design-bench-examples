# Review: sky130-unregulated-charge-pump-10mhz-pvt

```text
Task: sky130-unregulated-charge-pump-10mhz-pvt (voltage-doubling charge pump with enable)
Reviewer: task maintainers, 2026-11-13
Evidence: instruction.md, spec/README.md, task.toml, all public and hidden benches,
  verify.py, test.sh, both Dockerfiles, the reference, 5 mutants, 1 alternative.
  tools/check_task.sh on eda-f (podman): both images build; agent image runs as
  `agent` with starter, spec, checker, guide and ngspice. Native Harbor 0.25.0 runs
  (docker): oracle reward 1, nop reward 0. Harbor 0.25.0 TaskConfig parses task.toml.
  Reference: reward 1 in about 4 min (8 PVT points x 2 enabled conditions + 1 disabled run).
Novelty: the repository's only clocked power-conversion example.
```

## Findings

| # | Severity | Finding | Status |
|---|---|---|---|
| 1 | P0 | Power into `CLK` and `EN` was not measured; the enable rail sat outside the supply sense source. A pump could run on its clock or enable and still pass the 200 uA / 100 nA supply limits. | Fixed: both pins are metered and limited to 1 uW each (`pvt_clock_enable_power`); mutants `clock-powered-drivers` and `enable-powered-drivers` fail. |
| 2 | P1 | Public benches were the hidden decks at ff/125 C, and the 8 graded points were listed. | Fixed: public benches run tt/27 C at 45 uA; grading uses 50 uA/45 % and 40 uA/55 % at unlisted points. |
| 3 | P2 | A first mutant (one output device removed) still met every limit, so it was not a defect under the contract. | Replaced by `same-phase-capacitors`. |

Reference worst cases: output 2.354–2.733 V, ripple 3.35 mVpp, enabled
current 136 uA, disabled current 9.0 nA, clock/enable power 76 nW.

## Reward-hacking checks

| Submission | What the grader did |
|---|---|
| Reference | 1 |
| Empty starter (Harbor nop) | 0 |
| Reference plus an ideal resistor, or plus an independent voltage source | 0: rejected by `check_circuit.py` before simulation |
| `clock-powered-drivers` | 0: drivers starve, output 1.93 V |
| `enable-powered-drivers` | 0: 143 uW drawn through `EN` |
| `enable-ignored` | 0: 99.6 uA when disabled |
| `same-phase-capacitors` | 0: output 0.37 V |
| `small-pump-capacitors` | 0: output collapses |
| Alternative `diode-precharged-pump` (different structure) | 1 |

A cross-coupled doubler without a diode drop was tried as a second
alternative. Its output, 2·VDD − I/(f·C), cannot be held inside 2.2–2.8 V over
the load and PVT range (it spanned 1.2–2.9 V), so the contract in effect needs
a diode-type drop to set the level.

## Conclusion

pass

## Required next action

None.
