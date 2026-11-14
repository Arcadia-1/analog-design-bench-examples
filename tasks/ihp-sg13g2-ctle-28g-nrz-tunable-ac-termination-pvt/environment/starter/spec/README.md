# 28 Gb/s NRZ tunable CTLE (IHP SG13G2): specification

This folder is the complete public contract. Each rule is stated once: the
electrical contract here, the simulation and netlist rules in
[`signoff-rules.md`](signoff-rules.md), and the device syntax in
[`netlist-guide.md`](netlist-guide.md).

## Interface

`.subckt ctle_28g vss vdd iref vocm vinp vinn gain_b0 gain_b1 peak_b0 peak_b1 voutp voutn`,
with exactly that pin order.

- `vss`, `vdd`: supply rails, 3.3 V nominal.
- `iref`: a 100 uA reference current from `vdd` into this pin. It is the only
  bias input; deriving the internal bias currents from it is intended but not
  scored.
- `vocm`: the common-mode reference, `vdd/2`. The output common mode must
  track it, and the input termination may return to it.
- `vinp`, `vinn`: differential input from the channel. As in a real link, the
  channel reaches the pins through AC-coupling capacitors on the board
  (100 nF, part of the test bench, not of the circuit), so the circuit sets
  its own input common mode, for example by returning its termination to
  `vocm`. `voutp`, `voutn`: differential output, non-inverting
  (`voutp - voutn` in phase with `vinp - vinn` at low frequency).
- `gain_b1:gain_b0`, `peak_b1:peak_b0`: static 2-bit codes at `vss` or `vdd`
  levels, fixed during each analysis.

The design is an active, fully differential continuous-time linear equalizer
(CTLE) for the receiver front end of a 28 Gb/s NRZ link: a matched 50 ohm
input, a gain code that sets the low-frequency gain and a peak code that sets
the gain at the 14 GHz Nyquist frequency. SiGe HBTs, MOS devices and MOS
varactors are all available. Grading scores only the external electrical
behavior below; it does not count devices or inspect names, dimensions,
hierarchy or connectivity.

## Test conditions

- `vocm` is driven from `vdd/2` through 10 ohm. Each control pin is driven
  from its logic level (`vss` or `vdd`) through 1 kohm.
- Each input is driven from a 50 ohm source through a 100 nF series
  capacitor (the link's AC coupling). The AC amplitudes are +1 V on the
  `vinp` side and -1 V on the `vinn` side (2 V differential open-circuit), so
  a matched 100 ohm differential input receives exactly 1 V.
- Each output is loaded by a capacitor to `vss`: 200 fF at the points listed
  under Coverage. The load of a real receiver varies, so grading also uses
  other loads between 180 and 220 fF (below).
- AC analysis runs from 100 Hz to 40 GHz at 20 points per decade (ngspice
  `ac dec 20 100 40g`).
- Gain is `G(f) = 20 log10(abs(v(voutp)-v(voutn)) / 1 V)`. `G_LF = G(1 MHz)`,
  `G_14 = G(14 GHz)`. Values at 1 MHz, 14 GHz and 40 GHz and every -3 dB
  crossing are linearly interpolated between sweep points (ngspice
  `meas ... find`/`when`); maxima and minima are taken over the sweep points.

## Coverage

Grading runs:

- the nominal point (`tt`, 3.30 V, 27 C);
- every corner (`tt`, `ss`, `ff`, `sf`, `fs`) at both ends of the supply
  range (2.97 V and 3.63 V) and both ends of the junction-temperature range
  of a commercial transceiver (0 C and 125 C);
- two output loads in 180-220 fF at the nominal corner, supply and
  temperature. Their values are not published.

That is 23 points. A corner sets the lv and hv MOS and varactor models to that
MOS corner; `ss` also takes the slow HBT, high resistor and high capacitor
corners (`hbt_wcs`, `res_wcs`, `cap_wcs`), `ff` the fast ones (`hbt_bcs`,
`res_bcs`, `cap_bcs`), and `tt`, `sf` and `fs` the typical ones.

All 16 code combinations are graded at every point; the input impedance at
`gain=00`/`peak=00`; the disturbance transient at `gain=00`/`peak=00` and
`gain=11`/`peak=11`. This covers the vertices of the declared supply,
temperature and process ranges, not interior points. Local mismatch and Monte
Carlo variation, noise, group delay, large-signal behavior, eye or PRBS
performance, interconnect extraction and package parasitics are not graded.

## Scored checks

Each check passes or fails on its own, over every graded point and code; the
reward is the fraction that pass. A check fails if its limit is broken
anywhere, or if a simulation it needs did not produce its measurements
(non-convergence, or a broken signoff rule, which also scores the task 0).
The two stability checks also need a valid operating point (the AC run) at
the same point and code.

| Check | Requirement |
|---|---|
| `gain_code_table` | `G_LF` within 1.5 dB of -12, -10, -8, -6 dB for gain codes 00, 01, 10, 11; each successive gain code raises `G_LF` by at least 1 dB at every peak code |
| `peak_code_table` | `G_14` within 1.5 dB of 0, +2, +4, +6 dB for peak codes 00, 01, 10, 11; each successive peak code raises `G_14` by at least 1 dB at every gain code |
| `output_polarity` | The 1 MHz differential gain is non-inverting (positive real part) |
| `inband_flatness` | From 1 MHz to 14 GHz, `G` at most 1 dB above the larger of `G_LF` and `G_14`, and never below `G_LF - 3 dB` |
| `above_nyquist_peaking` | From 14 GHz to 40 GHz, `G` at most 3 dB above `G_14` |
| `bandwidth_floor` | The first falling crossing of `G_14 - 3 dB` above 14 GHz is at 25 GHz or higher; a response still above it at 40 GHz passes |
| `forward_reverse_ratio` | `R(f)` at least 15 dB from 100 MHz to 14 GHz |
| `input_coupling_corner` | Low-frequency corner, with the link's 100 nF capacitors, at most 20 kHz |
| `input_termination_impedance` | Single-ended input impedance at 1 MHz from 40 to 60 ohm at each input |
| `input_return_loss` | Single-ended `abs(S11)` of each input and differential `abs(Sdd11)` at every code at most -15 dB from 100 MHz to 14 GHz |
| `common_mode_stability` | Output common mode within 75 mV of `vdd/2` and its peak-to-peak at most 1 mV, from 3.1 to 4.1 us after the disturbances |
| `differential_stability` | Differential output peak-to-peak at most 1 mV, and `abs(voutp-voutn)` at most 5 mV, from 3.1 to 4.1 us after the disturbances |
| `bias_and_power` | Output common mode within 75 mV of `vdd/2`, differential offset at most 5 mV, each output between `vss + 0.25 V` and `vdd - 0.25 V`, `vocm` tracking at least 9 mV per 10 mV, DC current into or out of the `vocm` pin at most 100 uA, total power at most 60 mW |

## Measurement definitions

- **Code tables.** The receiver's adaptation loop searches the gain and peak
  codes in order and expects each one on a 2 dB grid. Both controls are held
  to the same accuracy:
  - every code within ±1.5 dB of its grid value, less than one step, so a
    code is always nearer its own target than its neighbour's;
  - each code at least 1 dB above the previous one, so the search stays
    monotonic.

  Over the four codes, the Nyquist boost `G_14 - G_LF` spans 6 to 18 dB, the
  range a 28 Gb/s NRZ receiver typically asks of its CTLE.
- **Bandwidth.** 25 GHz, about 0.9 times the 28 GBd symbol rate, keeps the
  equalizer's own roll-off above Nyquist from adding intersymbol interference
  that the receiver would have to equalize again.
- **Polarity.** The real part of `v(voutp)-v(voutn)` at 1 MHz must be
  positive, so an inverted output fails.
- **Return loss.** Single-ended, at `gain=00`/`peak=00`:
  - A 1 A AC current is injected straight into one input pin, without the
    link capacitor, so this is the impedance looking into the die.
  - The other input is terminated in 50 ohm to the `vocm` pin, and 1 Gohm
    resistors from each input to `vocm` keep the DC operating point defined.
  - `Z = v(pin) - v(vocm)` and `S11 = (Z - 50)/(Z + 50)`; each pin is
    measured in its own run.

  Differential: `Sdd11 = (v(vinp) - v(vinn)) - 1 V` from the AC run, the
  reflected fraction of the 1 V incident differential wave. -15 dB up to
  Nyquist at the die leaves room for the package and ESD parasitics that the
  receiver's pin-level return-loss budget must also cover, and a SiGe input
  with its small device capacitance reaches it without a T-coil.
- **Input coupling.** With `gain=00`/`peak=00`, a rising crossing of
  `G_LF - 3 dB` below 2 MHz must be at most 20 kHz; if there is none,
  `G(100 Hz)` must be within 3 dB of `G_LF`. The link's 100 nF capacitors
  in the 50 + 50 ohm path alone give 15.9 kHz, so a DC-coupled circuit
  passes and an extra on-die high-pass raises the corner.
- **Forward/reverse ratio.** In the same run as the gain, the input sources
  are set to zero (their 50 ohm resistances stay) and 1 A AC test currents are
  driven differentially into `voutp` and `voutn`.
  `R(f) = G(f) + 20 log10(50) - 20 log10(abs(v(vinp)-v(vinn)))`. Any network of
  R, L, C, K and linear switches has `R(f) = 0 dB` by reciprocity, so 15 dB
  means the signal must reach the output through a unidirectional active path
  that also keeps output disturbances off the channel.
  A passive equalizer followed by an active buffer is acceptable.
- **Disturbances.** The setup:
  - the inputs are held through their 50 ohm sources and link capacitors,
    with no signal;
  - both output loads return to a node that is 0 V at the operating point;
  - that node steps with a 10 ns edge at 100 ns;
  - before 1 us, a 5 ns common-mode current pulse of about 1 mA enters (or
    leaves) each output;
  - later, a 5 ns differential current pulse of about 1 mA enters one output
    and leaves the other (ngspice `tran 0.5n 4.1u 0 0.5n`).

  The common mode `(voutp+voutn)/2` and the differential output
  `voutp-voutn` are read from 3.1 us to 4.1 us. Oscillation, very slow decay,
  a common mode left away from `vdd/2` or an output that latches to one side
  fails.
- **Operating point.** Measured at zero differential input, for every point
  and code.
  - The output common-mode error is measured against `vdd/2`, the value of
    the `vocm` source.
  - `vocm` tracking: raising the `vocm` source by 10 mV must raise the DC
    output common mode by at least 9 mV.
  - `vocm` is a reference, not a supply: the DC current the circuit draws
    from or pushes into the `vocm` pin is at most 100 uA (input base current
    through a termination to `vocm` is a few tens of uA).
  - Total power is the power delivered by `vdd` (`-V(vdd)*I(vdd)`, including
    the 100 uA reference branch) plus the magnitude of the DC power exchanged
    with the `vocm` source and the four control sources. Power drawn from or
    returned into them is counted, never excluded. The input sources carry
    no DC current through the link capacitors.
  - The 60 mW budget covers about 7 mA per side of high-speed
    transconductance from 3.63 V, plus bias and common-mode control.

## Deliverable and eligibility

Edit `/app/circuit.spi`. What it may contain (IHP SG13G2 devices, ideal R, C,
L and K, literal values, no sources) is the Netlist rule in
[`signoff-rules.md`](signoff-rules.md). Before simulation the grader runs the
check below; a failure scores 0.

```bash
python3 /app/signoff/check_circuit.py /app/circuit.spi
```

## Public benches

`/app/testbench/tb_ac_tt.spi`, `tb_zin_tt.spi` and `tb_kick_tt.spi` run three
measurements at the nominal point with the same circuit and metric
definitions as grading:

- the AC and operating-point measurement;
- the input impedance;
- the disturbance transient.

Edit their `code_*`, `excite_*` and `cload` parameters for other codes, the
other input and other loads. Grading runs them at the other points and codes,
with other disturbance signs and times and with the unpublished loads.

Run a bench the way grading simulates it:

```bash
python3 /app/signoff/run_bench.py /app/testbench/tb_ac_tt.spi
```

It applies the rules in [`signoff-rules.md`](signoff-rules.md) (netlist check,
device voltage ratings, one convergence retry) and prints the ngspice output.
A broken rule prints a `SIGNOFF RULE:` line and exits with code 3. A clean run
covers only that bench's point; grading checks the rules in every graded run.
Plain `cd /app/testbench && ngspice -b tb_ac_tt.spi` also works (the
`.spiceinit` there loads the PDK's OSDI models) but checks no rule.
