# 28 Gb/s NRZ tunable CTLE: specification

This folder is the complete public contract. Rules are stated once: the
electrical contract here, the simulation rules in
[`signoff-rules.md`](signoff-rules.md), and the SKY130 netlist syntax in
`/opt/analog-arena/SKY130_NETLIST_GUIDE.md`.

## Interface

`.subckt ctle_28g vss vdd iref vocm vinp vinn gain_b0 gain_b1 peak_b0 peak_b1 voutp voutn`,
with exactly that pin order.

- `vss`, `vdd`: supply rails.
- `iref`: a 100 uA reference current from `vdd` into this pin. It is the only
  bias input; deriving the internal bias currents from it is intended but not
  scored.
- `vocm`: the common-mode reference, `vdd/2`. The input termination may
  return to it, and the output common mode must track it.
- `vinp`, `vinn`: differential input from the channel. `voutp`, `voutn`:
  differential output, non-inverting (`voutp - voutn` in phase with
  `vinp - vinn` at low frequency).
- `gain_b1:gain_b0`, `peak_b1:peak_b0`: static 2-bit codes at `vss` or `vdd`
  levels, fixed during each analysis.

The design is an active, fully differential continuous-time linear equalizer
(CTLE) for the receiver front end of a 28 Gb/s NRZ link: a matched 50 ohm
input, a gain code that sets the low-frequency gain and a peak code that sets
the gain at the 14 GHz Nyquist frequency. Grading scores only the external
electrical behavior below; it does not count devices or inspect names,
dimensions, hierarchy or connectivity.

## Test conditions

- `vocm` is driven from `vdd/2` through 10 ohm. Each control pin is driven
  from its logic level (`vss` or `vdd`) through 1 kohm.
- Each input is driven through 50 ohm from a source whose DC level is the
  `vocm` source voltage. The AC amplitudes are +1 V on the `vinp` side and
  -1 V on the `vinn` side (2 V differential open-circuit), so a matched 100 ohm
  differential input receives exactly 1 V.
- Each output is loaded by 200 fF to `vss`.
- AC analysis runs from 100 Hz to 40 GHz at 20 points per decade (ngspice
  `ac dec 20 100 40g`).
- Gain is `G(f) = 20 log10(abs(v(voutp)-v(voutn)) / 1 V)`. `G_LF = G(1 MHz)`,
  `G_14 = G(14 GHz)`. Values at 1 MHz, 14 GHz and 40 GHz and every -3 dB
  crossing are linearly interpolated between sweep points (ngspice
  `meas ... find`/`when`); maxima and minima are taken over the sweep points.

## Coverage

Grading runs the nominal point (`tt`, 1.80 V, 27 C) and every Sky130 corner
(`tt`, `ss`, `ff`, `sf`, `fs`) at both ends of the supply range (1.62 V and
1.98 V) and both ends of the junction-temperature range of an operating
transceiver (27 C and 125 C): 21 points. All 16 code combinations are graded
at every point; the input impedance at `gain=00`/`peak=00`; the disturbance
transient at `gain=00`/`peak=00` and `gain=11`/`peak=11`. This covers the
vertices of the declared ranges, not interior points. Junction temperatures
below 27 C, local mismatch and Monte Carlo variation, noise, group delay,
large-signal behavior, eye or PRBS performance, interconnect extraction and
package parasitics are not graded.

Grading starts with the nominal point. If any check fails there, every check
fails and the other points are not run.

## Scored checks

Each check passes or fails; the reward is the fraction that pass. Every limit
applies at every graded point and code.

| Check | Requirement |
|---|---|
| `gain_code_table` | `G_LF` within 1.5 dB of -12, -10, -8, -6 dB for gain codes 00, 01, 10, 11; each successive gain code raises `G_LF` by at least 1 dB at every peak code |
| `peak_code_table` | `G_14` within 2 dB of 0, +2, +4, +6 dB for peak codes 00, 01, 10, 11; each successive peak code raises `G_14` by at least 1 dB at every gain code |
| `output_polarity` | The 1 MHz differential gain is non-inverting (positive real part) |
| `inband_flatness` | From 1 MHz to 14 GHz, `G` at most 1 dB above the larger of `G_LF` and `G_14`, and never below `G_LF - 6 dB` |
| `above_nyquist_peaking` | From 14 GHz to 40 GHz, `G` at most 3 dB above `G_14` |
| `bandwidth_floor` | The first falling crossing of `G_14 - 3 dB` above 14 GHz is at 17 GHz or higher; a response still above it at 40 GHz passes |
| `forward_reverse_ratio` | `R(f)` at least 10 dB from 100 MHz to 14 GHz |
| `input_coupling_corner` | DC transfer at most 1 mV at every code; low-frequency corner at most 20 kHz |
| `input_termination_impedance` | Single-ended input impedance at 1 MHz from 40 to 60 ohm at each input |
| `input_return_loss` | Single-ended `abs(S11)` of each input and differential `abs(Sdd11)` at every code at most -10 dB from 100 MHz to 14 GHz |
| `common_mode_stability` | Output common-mode peak-to-peak at most 1 mV from 3.1 to 4.1 us after the disturbances |
| `differential_stability` | Differential output peak-to-peak at most 1 mV, and `abs(voutp-voutn)` at most 5 mV, from 3.1 to 4.1 us after the disturbances |
| `bias_and_power` | Output common-mode error at most 75 mV, differential offset at most 5 mV, each output between `vss + 0.25 V` and `vdd - 0.25 V`, `vocm` tracking at least 9 mV per 10 mV, total power at most 60 mW |

## Measurement definitions

- **Code tables.** Both controls use uniform 2 dB steps. The low-frequency
  gain is set by resistor ratios and holds 1.5 dB; the Nyquist gain depends
  on device transconductance, which moves about 1.5 dB with temperature alone
  across the graded range, so its window is 2 dB. The 1 dB minimum step keeps
  every code distinct at every point. The Nyquist boost `G_14 - G_LF` spans
  6 to 18 dB, the range a 28 Gb/s NRZ receiver typically asks of its CTLE.
- **Polarity.** The real part of `v(voutp)-v(voutn)` at 1 MHz must be
  positive, so an inverted output fails.
- **Return loss.** Single-ended: with `gain=00`/`peak=00`, a 1 A AC current is
  injected into one input while the other is terminated in 50 ohm to the
  `vocm` pin; `Z = v(pin) - v(vocm)` and `S11 = (Z - 50)/(Z + 50)`, each pin in
  its own run. Differential: `Sdd11 = (v(vinp) - v(vinn)) - 1 V` from the AC
  run, the reflected fraction of the 1 V incident differential wave. -10 dB
  is the usual input return-loss requirement up to Nyquist for 25-28 Gb/s
  links.
- **Input coupling.** The signal path must be AC-coupled; the termination may
  be DC- or AC-coupled to `vocm`. DC transfer: stepping the input sources' DC
  levels by +50 mV and -50 mV must change `v(voutp)-v(voutn)` by at most 1 mV
  (a DC-coupled path at the lowest gain changes it by at least 10.6 mV).
  Corner: with `gain=00`/`peak=00`, a rising crossing of `G_LF - 3 dB` below
  2 MHz must be at most 20 kHz (a 100 nF coupling capacitor in a 50 + 50 ohm
  path gives 16 kHz); if there is none, `G(100 Hz)` must be within 3 dB of
  `G_LF`.
- **Forward/reverse ratio.** In the same run as the gain, the input sources
  are set to zero (their 50 ohm resistances stay) and 1 A AC test currents are
  driven differentially into `voutp` and `voutn`.
  `R(f) = G(f) + 20 log10(50) - 20 log10(abs(v(vinp)-v(vinn)))`. Any network of
  R, L, C, K and linear switches has `R(f) = 0 dB` by reciprocity, so 10 dB
  means the signal must reach the output through a unidirectional active path.
  A passive equalizer followed by an active buffer is acceptable.
- **Disturbances.** Inputs are held at the `vocm` source through their 50 ohm
  with no signal, and both 200 fF loads return to a node that is 0 V at the
  operating point. That node steps with a 10 ns edge at 100 ns, and later a
  5 ns differential current pulse of about 1 mA enters one output and leaves
  the other (ngspice `tran 0.5n 4.1u 0 0.5n`). The common mode
  `(voutp+voutn)/2` and the differential output `voutp-voutn` are read from
  3.1 us to 4.1 us: oscillation, very slow decay or an output that latches to
  one side fails.
- **Operating point.** At zero differential input, for every point and code.
  `vocm` tracking: raising the `vocm` source by 10 mV (the input sources
  follow it) must raise the DC output common mode by at least 9 mV. Total
  power is the power delivered by `vdd` (`-V(vdd)*I(vdd)`, including the
  100 uA reference branch) plus the magnitude of the DC power exchanged with
  each other source: the `vocm` source, both input sources and the four
  control sources. Power drawn from or returned into them is counted, never
  excluded. The 60 mW budget covers about 9 mA per side of high-speed
  transconductance from 1.98 V plus bias and common-mode control.

## Deliverable and eligibility

- Edit `/app/circuit.spi`. Use Sky130 PDK devices for every active device.
  Ideal R, C and L elements are allowed, and so is SPICE `K` coupling between
  two ideal L (for example an input T-coil); Sky130 has no physical
  coupled-inductor model. Every value must be a finite numeric literal:
  `.param` lines and `{...}` expressions are rejected. Independent or
  controlled sources, behavioral devices, ideal switches, models, analyses
  and includes are not allowed. Keep supplies, sources and loads out of the
  circuit.
- Before simulation the grader runs the check below; a failure scores 0.

  ```bash
  check_circuit.py /app/circuit.spi --allow-ideal R C L K --require-subcircuit ctle_28g vss vdd iref vocm vinp vinn gain_b0 gain_b1 peak_b0 peak_b1 voutp voutn --enforce-sky130-geometry
  ```

## Public benches

From `/app`, `ngspice -b testbench/tb_ac_tt.spi`, `testbench/tb_zin_tt.spi`
and `testbench/tb_kick_tt.spi` run the AC and operating-point measurement,
the input impedance and the disturbance transient at the nominal point with
the same circuit and metric definitions as grading. Edit their `code_*` and
`excite_*` parameters for other codes and the other input. Grading runs them
at the other points and codes, with other disturbance signs and times.

Plain `ngspice` does not apply the rules in [`signoff-rules.md`](signoff-rules.md).
To run a public bench the way grading simulates it, use

```bash
python3 /app/signoff/run_bench.py testbench/tb_ac_tt.spi
```

It applies the same rewrite as grading (model binning, diffusion geometry,
multipliers, voltage ratings) and prints the ngspice output. A broken rule
prints a `SIGNOFF RULE:` line and exits with code 3. A clean run covers only
that bench's point; grading checks the rules in every graded run.
