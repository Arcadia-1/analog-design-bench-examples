# Unregulated charge pump: specification

This folder is the complete public contract. Rules are stated once: the
electrical contract here, the simulation rules in
[`signoff-rules.md`](signoff-rules.md), and the SKY130 netlist syntax in
`/opt/analog-arena/SKY130_NETLIST_GUIDE.md`.

## Interface

`.subckt charge_pump_unregulate AVDD AVSS CLK EN VOUT`, with exactly that pin
order. `AVDD` is the 1.8 V supply, `AVSS` ground, `CLK` the pump clock, `EN`
an active-high enable, `VOUT` the pumped output.

## Operating conditions

- `AVDD` is fixed at 1.8 V.
- `CLK` is a 10 MHz rail-to-rail square wave with 1 ps edges and 50 % duty
  cycle. `CLK` and `EN` are each driven through 50 ohm source resistance.
- Enabled bench: `EN` high, `VOUT` drives 1 nF and a constant 50 uA to ground.
  A 200 us transient starts from power-up; metrics use 190–200 us.
- Disabled bench: `EN` held low from time zero with the clock running, `VOUT`
  drives 1 nF only; supply current is averaged over 9–10 us.
- Enabled and disabled supply currents come from separate runs through ideal
  supply sense sources.

## Coverage

Final grading uses eight representative process–temperature points at 1.8 V:
`tt` 40 C; `ss` -10 C and 125 C; `ff` -10 C and 125 C; `sf` -10 C and 125 C;
`fs` 40 C. They span all five Sky130 corners and the three temperatures and
include the low-output, high-output and ripple/leakage stresses. This is a
representative set, not the complete Cartesian product. Other supplies,
clock rates and loads, startup beyond 200 us, efficiency and mismatch are not
graded. The nominal `tt`/40 C point runs first; if it fails, every check fails.

## Scored checks

Each check passes or fails; the reward is the fraction that pass. Every limit
applies at every graded point.

| Check | Requirement |
|---|---|
| `pvt_loaded_output_voltage` | Enabled mean `VOUT` from 2.2 V through 2.8 V |
| `pvt_output_ripple` | Enabled `max(VOUT) - min(VOUT)` at most 5 mVpp |
| `pvt_enabled_current` | Enabled average supply current at most 200 uA |
| `pvt_disabled_current` | Disabled average supply current at most 100 nA |

## Deliverable and eligibility

- Edit `/app/circuit.spi`. Use official Sky130 PDK subcircuits; parameter-free
  local hierarchy is allowed. Ideal passives, independent or controlled
  sources, behavioral devices, ideal switches and XSPICE devices are not
  allowed. Keep models, supplies, clock, load and sense sources out of the
  circuit.
- Before simulation the grader runs the check below; a failure scores 0.

  ```bash
  check_circuit.py /app/circuit.spi --require-subcircuit charge_pump_unregulate AVDD AVSS CLK EN VOUT --enforce-sky130-geometry
  ```

  With `--enforce-sky130-geometry`, `nfet_01v8`, `pfet_01v8` and
  `nfet_01v8_lvt` need `l` in [0.15, 8] um and `pfet_01v8_lvt` `l` in
  [0.35, 8] um; all four need `w/nf` in [0.42, 7] um with a positive
  integer `nf` (default 1). The benches load the continuous library: use
  `mult`, not `mf`, on its MIM wrapper.

## Public benches

From `/app`, `ngspice -b testbench/tb_charge_pump_ff_hot.spi` and
`ngspice -b testbench/tb_charge_pump_pd_ff_hot.spi` run the enabled and
disabled benches at `ff`/1.8 V/125 C with the same metric definitions as
grading. They do not apply the signoff rules.
