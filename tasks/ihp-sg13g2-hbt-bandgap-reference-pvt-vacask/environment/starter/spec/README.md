# Bandgap voltage reference (IHP SG13G2, VACASK): specification

This folder is the complete public contract. Rules are stated once: the
electrical contract here, the simulation rules in
[`signoff-rules.md`](signoff-rules.md), and the netlist syntax in
[`netlist-guide.md`](netlist-guide.md). Grading simulates with **VACASK 0.3.4**
and the IHP SG13G2 models in `/opt/ihp/pdk/ihp-sg13g2/libs.tech/vacask`.

## Interface

`subckt bandgap (vdd vss en vref)`, with exactly that pin order. `vdd` is the
supply, `vss` the ground and substrate (0 V), `en` the active-high enable and
`vref` the reference output.

The design is a bandgap reference built on the SiGe HBTs of the process: its
output is set by base-emitter voltages and their differences. You choose the
circuit, device sizes and hierarchy; grading scores only the external
electrical behavior below.

## Operating conditions

- Supply `vdd`: 3.0 V, 3.3 V and 3.6 V, from an ideal source. Temperature
  -40 C to 125 C.
- Process corners. Each corner sets the MOS (`cornerMOShv.lib` and
  `cornerMOSlv.lib`), HBT, resistor and MIM-capacitor sections together:

  | Corner | MOS | HBT | Resistors | Capacitors |
  |---|---|---|---|---|
  | `tt` | `mos_tt` | `hbt_typ` | `res_typ` | `cap_typ` |
  | `ss` | `mos_ss` | `hbt_wcs` | `res_wcs` | `cap_wcs` |
  | `ff` | `mos_ff` | `hbt_bcs` | `res_bcs` | `cap_bcs` |
  | `sf` | `mos_sf` | `hbt_wcs` | `res_bcs` | `cap_typ` |
  | `fs` | `mos_fs` | `hbt_bcs` | `res_wcs` | `cap_typ` |

- `en` is driven from an ideal source through 10 kΩ: the source is at `vdd`
  to enable the circuit and at 0 V to disable it. The current the circuit
  takes from it counts as supply current.
- `vref` drives a capacitor to `vss`: 10 pF in the DC bench, 20 pF in the
  small-signal bench, and 2 pF or 20 pF in the startup bench as listed below.
  The load-regulation points also draw a DC current from `vref` to `vss`.

## Coverage

Every bench runs at every corner and every supply. This is the complete
product of the sets below, not a representative subset.

- **DC bench**: operating points at 35 temperatures (-40 C to 125 C in 5 C
  steps, and 27 C) for `vref_accuracy`, `temperature_coefficient`,
  `line_regulation` and `quiescent_current`; at -40 C, 27 C and 125 C for
  `load_regulation`, `shutdown` and the operating-point part of
  `startup_settling`.
- **Small-signal bench**: -40 C, 27 C and 125 C, with 20 pF on `vref`.
- **Startup bench**: -40 C, 27 C and 125 C, each with three stimuli (135 runs):
  - `ramp-1us`: `vdd` and the enable source rise together from 0 V to the
    supply in 1 µs, starting at 1 µs; 20 pF on `vref`.
  - `ramp-100us`: the same in 100 µs; 2 pF on `vref`.
  - `enable`: `vdd` is at the supply from the start; the enable source rises
    from 0 V to the supply in 100 ns, starting at 2 µs; 20 pF on `vref`.

## Scored checks

Each check passes or fails, and must pass at every point it covers; the reward
is the fraction of the 10 checks that pass, with two exceptions that score 0:
a broken rule of [`signoff-rules.md`](signoff-rules.md), and a circuit that is
not a reference, which here means one that fails `vref_accuracy` or
`line_regulation` (an output outside the window, or one that follows the
supply, is not graded on its other properties).

| Check | Requirement |
|---|---|
| `vref_accuracy` | 1.00 V ≤ `vref` ≤ 1.15 V at every DC point, with no load current |
| `temperature_coefficient` | Box-method temperature coefficient at most 100 ppm/C, for every corner and supply |
| `line_regulation` | `vref` changes by at most 1 mV per volt of supply between 3.0 V and 3.6 V, at every corner and temperature |
| `load_regulation` | Drawing 10 µA from `vref` changes it by at most 5 mV |
| `psrr_10khz` | Supply rejection at 10 kHz at least 40 dB |
| `noise_10khz` | Output noise density at 10 kHz at most 1 µV/√Hz |
| `quiescent_current` | \|current of the supply\| + \|current of the enable source\| at most 100 µA at every DC point |
| `shutdown` | With the enable source at 0 V: at most 1 µA through the supply and at most 1 µA through the enable source, each in either direction, and `vref` at most 0.1 V |
| `startup_settling` | After every startup stimulus `vref` settles within ±5 mV of the target in 10 µs; and the operating point does not depend on the initial guess |
| `startup_overshoot` | After every startup stimulus `vref` never exceeds the target by more than 50 mV |

Measurement definitions:

- **Temperature coefficient** = (max − min) / mean / 165 C of `vref` over the
  35 temperatures of one corner and supply, in ppm/C.
- **Line regulation** = |`vref`(3.6 V) − `vref`(3.0 V)| / 0.6 V at one corner
  and temperature.
- **Load regulation** compares the operating points with 0 and 10 µA drawn
  from `vref` to `vss`, at the same corner, supply and temperature.
- **Supply rejection**: AC analysis with the same 1 V AC excitation on the
  `vdd` source and on the enable source (the enable pin sits at the supply
  when enabled, so its ripple is supply ripple); PSRR = −20·log10 |v(`vref`)|
  at 10 kHz.
- **Output noise**: VACASK noise analysis, the square root of the total
  output noise power spectral density at `vref` at 10 kHz, from the noise
  sources of every device.
- **Quiescent current** = |current of the supply source| + |current of the
  enable source|, at each DC operating point. Each pin is metered on its own
  magnitude, so current that leaves through one source cannot cancel current
  drawn from the other.
- **Startup target**: the operating point of the circuit with the supply and
  the enable source at their final DC values, at the same corner, supply,
  temperature and load capacitance, computed in the same bench before the
  transient (analysis `target`). The transient starts from 0 V on `vdd` (or
  on the enable source) and runs until 30 µs after the end of the stimulus
  edge. Settling time is the last time `vref` is more than 5 mV from the
  target, measured from the end of the edge (the end of the supply ramp or of
  the 100 ns enable edge); a run that ends outside the band fails. The target
  must itself lie in the 1.00-1.15 V window and agree within 1 mV with the DC
  bench's `vref` at the same corner, supply and temperature, or
  `startup_settling` fails. Overshoot
  is the largest `vref` − target from the end of the edge on. During a supply
  ramp, before it ends, `vref` is not graded.
- **Initial guess**: at -40 C, 27 C and 125 C the DC bench solves the
  operating point twice more, with the initial guess `vref` = 0 V and
  `vref` = `vdd`. The two results and the DC sweep's result must agree within
  1 mV, or `startup_settling` fails.
- **Why these limits.** The 1.00-1.15 V window brackets the zero-TC output of
  these SiGe HBTs: their VBIC cards extrapolate Vbe to about 1.05-1.06 V at
  0 K (`ea` = 1.056 eV for `npn13G2`), which puts the first-order bandgap
  output near 1.04-1.10 V depending on current density and topology; the
  window allows that spread over all corners without asking for a scaled
  output. 100 ppm/C is the usual class of an untrimmed
  first-order bandgap over -40 C to 125 C; the spread of the HBT saturation
  current and of the resistor sheet resistance across the corners moves the
  zero-TC point, and no trim is available. 1 mV/V is 0.1 %/V. 40 dB at 10 kHz,
  1 µV/√Hz, 100 µA and 1 µA are round targets for a reference that feeds an
  on-chip regulator or converter; 10 µs settling and 50 mV (about 5 %)
  overshoot are what such a load can tolerate at power-up.

Not scored: device mismatch and Monte Carlo, trimming, noise and supply
rejection at other frequencies, integrated noise, line and load transients,
area, behavior below 3.0 V supply (except that startup must succeed), and
supplies, temperatures or corners outside the sets above.

## Deliverable and eligibility

Edit `/app/circuit.sim`. What it may contain, and the eligibility check the
grader runs before any simulation (a failure scores 0), are in
[`netlist-guide.md`](netlist-guide.md). Keep the supply, the enable source and
the loads out of the circuit.

## Public benches

The benches in `/app/testbench/` run at `tt`, 3.3 V and 27 C (the DC bench also
at 3.0 V and 3.6 V and at ten temperatures) with the same sources, metric
definitions and rules as grading. They differ from the hidden ones in
coverage and stimuli: 10 pF on `vref` everywhere, a 5 µA load step, a 10 µs
supply ramp (`tb_startup_tt.sim`) and a 1 µs enable edge
(`tb_enable_tt.sim`).

`vacask` runs a bench as it is and writes one raw file per analysis; run it in
the folder of the bench (`.vacaskrc.toml` there points VACASK at the PDK):

```bash
cd /app/testbench && vacask tb_dc_tt.sim
```

To run a bench the way grading does, apply the rules of
[`signoff-rules.md`](signoff-rules.md) and print every metric:

```bash
cd /app
python3 signoff/run_bench.py testbench/tb_dc_tt.sim
python3 signoff/run_bench.py testbench/tb_ac_tt.sim
python3 signoff/run_bench.py testbench/tb_startup_tt.sim
python3 signoff/run_bench.py testbench/tb_enable_tt.sim
```

A broken rule prints a `SIGNOFF RULE:` line and exits with code 3. A clean run
covers only the points of that bench. To try another corner, supply,
temperature or load, copy a bench and change its `include ... section=`
lines, its sweep values or `options temp=`, or its sources.
