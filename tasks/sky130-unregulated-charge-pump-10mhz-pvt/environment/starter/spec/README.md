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

- `AVDD` is fixed at 1.8 V, measured through an ideal sense source.
- `CLK` is a 10 MHz rail-to-rail square wave with 1 ps edges and a duty cycle
  anywhere from 45 % to 55 %. `CLK` and `EN` are each driven through 50 ohm
  source resistance, and the current into each pin is measured.
- Enabled: `EN` high, `VOUT` drives 1 nF and a constant load current to ground
  of any value from 40 uA to 50 uA. A 200 us transient starts from power-up;
  metrics use 190–200 us.
- Disabled: `EN` held low from time zero with the clock running, `VOUT` drives
  1 nF only; metrics use 9–10 us of a 10 us transient.

## Coverage

Grading runs representative process–temperature points at 1.8 V that span all
five Sky130 corners and -10 C to 125 C, including the low-output, high-output
and ripple/leakage stresses. At each point it runs several enabled conditions
from the ranges above and one disabled run. This is representative coverage,
not the complete Cartesian product. Other supplies, clock rates and loads,
startup beyond 200 us, efficiency and mismatch are not graded. The `tt`/40 C
point runs first; if it fails, every check fails.

## Scored checks

Each check passes or fails; the reward is the fraction that pass. Every limit
applies at every graded point and condition.

| Check | Requirement |
|---|---|
| `pvt_loaded_output_voltage` | Enabled mean `VOUT` from 2.2 V through 2.8 V |
| `pvt_output_ripple` | Enabled `max(VOUT) - min(VOUT)` at most 5 mVpp |
| `pvt_enabled_current` | Enabled average `AVDD` current at most 200 uA |
| `pvt_disabled_current` | Disabled average `AVDD` current at most 100 nA |
| `pvt_clock_enable_power` | Average power delivered into `CLK` and into `EN`, each at most 1 uW, enabled and disabled |

`AVDD` is the only supply. The clock and enable sources only drive logic: a
circuit that draws its pump energy through `CLK` or `EN` fails
`pvt_clock_enable_power`.

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

From `/app`, `ngspice -b testbench/tb_charge_pump_tt.spi` (enabled, 45 uA, 50 %
duty) and `ngspice -b testbench/tb_charge_pump_pd_tt.spi` (disabled) run at
`tt`/1.8 V/27 C with the same circuit and metric definitions as grading.
Grading uses other conditions inside the ranges above. The public benches do
not apply the signoff rules.
