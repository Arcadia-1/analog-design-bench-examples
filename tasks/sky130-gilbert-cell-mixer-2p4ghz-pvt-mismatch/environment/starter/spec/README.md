# 2.4 GHz Gilbert-cell mixer: specification

This folder is the complete public contract. Rules are stated once: the
electrical contract here, the simulation rules in
[`signoff-rules.md`](signoff-rules.md), and the SKY130 netlist syntax in
`/opt/analog-arena/SKY130_NETLIST_GUIDE.md`.

## Interface

`.subckt gilbert_mixer vss iref vdd lop lon rfp rfn ifoutp ifoutn`, with
exactly that pin order. `vdd` and `vss` are the supply rails; `rfp`/`rfn` the
differential RF input, `lop`/`lon` the differential LO input and
`ifoutp`/`ifoutn` the differential IF output. `iref` is the only external bias
port: the bench drives the reference current into it from `vdd`.

The design is a double-balanced active downconversion mixer (a Gilbert cell).
Grading scores only the external electrical behavior below; it does not count
devices or inspect names, dimensions, hierarchy or connectivity.

## Operating conditions

- Supply `vdd` from 1.62 V to 1.98 V; temperature from -40 C to 125 C; Sky130
  corners `tt`, `ss`, `ff`, `sf` and `fs`. Local mismatch uses the PDK's
  `tt_mm` models at 1.80 V and 27 C, a fresh draw for every PDK device in each
  graded run.
- Reference current into `iref`: any value from 45 uA to 55 uA, from a current
  source on `vdd` with 1 Mohm in parallel (which adds about 1 uA).
- RF input: each side is a source at 0.9 V DC through 50 ohm, the two sides in
  opposite phase. RF frequency from 2.30 GHz to 2.50 GHz; graded frequencies
  are multiples of 50 MHz. Single-tone drive is 20 mV peak per side (source
  voltage).
- LO input: each side is a 0.9 V DC source with a sine of 0.30 V to 0.40 V peak
  through 50 ohm, the two sides in opposite phase. The LO is always 200 MHz
  below the RF, so the IF is 200 MHz.
- Imbalance: up to 2 % differential amplitude imbalance on the RF and on the
  LO (one side scaled by 1 + d/2, the other by 1 - d/2, d of either sign) and
  up to 2 degrees of LO phase error of either sign.
- Load: 200 fF from each IF output to `vss`. The IF outputs are DC-coupled.

## Coverage

The single-tone checks are graded at the nominal point (`tt`, 1.80 V, 27 C,
2.40 GHz, 0.35 V LO, 50 uA, no imbalance); at every process corner at both
ends of the supply range and both ends of the temperature range, once with
the smallest LO amplitude and reference current and once with the largest,
at the RF band edges; at five PVT points with RF and LO imbalance and LO phase
error of various signs at interior frequencies; and at twelve mismatch seeds,
each with imbalance. Linearity is graded at the nominal point and at every
corner at both supply and both temperature ends, alternating smallest and
largest LO amplitude and reference current. This is representative coverage,
not the full product of every range. Noise figure, LO-port return loss, IF
bandwidth beyond 250 MHz and supply rejection are not graded.

Grading starts with the nominal single-tone point. If it fails to simulate or
its conversion gain is below 3 dB, every check fails and nothing else runs.

## Scored checks

Each check passes or fails; the reward is the fraction that pass. Every limit
applies at every graded point of that kind.

| Check | Requirement |
|---|---|
| `conversion_gain_3db` | Conversion gain at least 3 dB |
| `isolation_45db` | LO-to-IF, RF-to-IF and LO-to-RF each at most -45 dB |
| `spur_30dbc` | Largest non-IF output tone from 50 MHz to 3 GHz at most -30 dBc |
| `output_operating_point` | Output offset at most 30 mV, output common mode at least 0.45 V and at least 0.20 V below `vdd` |
| `power_2mw` | Average `vdd` power at most 2.0 mW |
| `pin_power_10uw` | Average power into each of `rfp`, `rfn`, `lop`, `lon` at most 10 uW |
| `iip3_m2dbm` | Two-tone IIP3 at least -2 dBm, with a valid extrapolation |
| `compression_1db` | Gain compression at 80 mV peak per side at most 1 dB |

Measurement definitions:

- **Single-tone record.** The first 5 ns of the transient are settling. The
  record is 20 ns, a whole number of RF, LO and IF periods: 1024 samples
  19.53125 ps apart, from 5 ns to 24.98 ns. Spectra are FFTs of these samples
  with no window function, so every tone falls on a 50 MHz bin; tone
  magnitudes are peak volts of the differential voltage at the pins. Averages
  are taken over the same span, 5 ns to 24.98 ns; the last sample interval
  left out changes them by about 0.1 % of their ripple.
- **Conversion gain** is the differential IF output at 200 MHz over the
  differential RF voltage at `rfp`/`rfn` at the RF frequency.
- **Isolation.** LO-to-IF: the differential output at the LO frequency over
  the differential LO voltage at `lop`/`lon`. RF-to-IF: the differential output
  at the RF frequency over the differential RF voltage at the pins. LO-to-RF:
  the differential voltage at `rfp`/`rfn` at the LO frequency over the
  differential LO voltage at the pins.
- **Spur** is the largest bin of the differential output from 50 MHz to 3 GHz,
  other than the 200 MHz IF, relative to the IF tone. It includes LO and RF
  feedthrough and IF harmonics; the RF+LO sum products above 3 GHz are not
  counted.
- **Output operating point.** Each output's average over the window. Offset is
  the difference of the two averages, common mode their mean, and headroom
  `vdd` minus the common mode.
- **Power** is the average over the window of `vdd` times the `vdd` source
  current, which includes the reference current.
- **Pin power** is the average over the window of each pin's voltage times the
  current the bench drives into that pin. The RF and LO ports stand for an
  on-chip LNA output and LO buffer, which supply signal but no bias current.
  Driving gate capacitance nets almost no power (well under 1 uW, often
  negative), while 10 uW is about 11 uA of DC current at the 0.9 V common
  mode: a design that takes bias current from these ports fails. The limit is
  separate from `power_2mw` because current drawn at 0.9 V costs less than the
  same current from `vdd`, so a combined limit would reward moving current
  onto these pins.
- **IIP3.** Two RF tones at 2.40 and 2.45 GHz with the LO at 2.20 GHz (0.9 V
  DC, LO amplitude and reference current as graded), at 10 mV and at 20 mV peak
  per tone per side, each level in its own copy of the circuit. After 10 ns
  of settling, spectra are FFTs of a 20 ns record (1024 samples 19.53125 ps
  apart, 50 MHz bins), so every tone and product falls on a bin. The IF tones are at 200 and 250 MHz, the IM3 products at 150
  and 300 MHz. At each level, with `Vin` the mean of the two input tones at the
  pins (differential peak volts), `Vfund` the mean of the two IF tones and `VIM3`
  the larger IM3 product:
  `IIP3 = 20*log10(Vin/1 V) + 0.5*20*log10(Vfund/VIM3) + 10*log10(1/(2*100 ohm*1 mW))`
  (dBm in the 100 ohm differential source). The lower of the two levels is
  scored. The extrapolation is valid only if, from 10 mV to 20 mV, the
  fundamental grows 0.8 to 1.2 dB and the IM3 at most 4 dB per dB of input,
  and the two IF tones differ by at most 1.5 dB at each level; otherwise the
  check fails. Slower IM3 growth is not penalized: it only makes the
  10 mV extrapolation, and so the scored value, lower.
- **Compression** is the conversion gain of a 2.40 GHz tone at 20 mV peak per
  side minus that at 80 mV peak per side, each in its own copy of the circuit,
  from the same record.

## Deliverable and eligibility

- Edit `/app/circuit.spi`. Use official Sky130 PDK subcircuits, plus ideal
  positive resistors and capacitors; parameter-free local hierarchy is allowed.
  Ideal resistors and capacitors have no process, temperature or mismatch
  variation. Inductors, independent or controlled sources, behavioral devices,
  ideal switches and XSPICE devices are not allowed. Keep models, supplies,
  bias and loads out of the circuit.
- Before simulation the grader runs the check below; a failure scores 0.

  ```bash
  check_circuit.py /app/circuit.spi --allow-ideal R C --require-subcircuit gilbert_mixer vss iref vdd lop lon rfp rfn ifoutp ifoutn
  ```

## Public benches

From `/app`, `ngspice -b testbench/tb_rf_tt.spi`,
`testbench/tb_mismatch_tt_mm.spi` and `testbench/tb_linearity_tt.spi` run
every measurement with the same circuit and metric definitions as grading:
the single-tone bench at the nominal point, the mismatch bench at one
`tt_mm` seed with imbalance at 2.35 GHz, and the linearity bench at the
nominal point. Grading runs them at other conditions inside the ranges above.

Plain `ngspice` does not apply the rules in [`signoff-rules.md`](signoff-rules.md).
To run a public bench the way grading simulates it, use

```bash
python3 /app/signoff/run_bench.py testbench/tb_rf_tt.spi
```

It applies the same rewrite as grading (model binning, diffusion geometry,
multipliers, voltage ratings, stored-energy accounting) and prints the ngspice
output. A broken rule prints a `SIGNOFF RULE:` line and exits with code 3. A
clean run covers only that bench's point; grading checks the rules in every
graded run.
