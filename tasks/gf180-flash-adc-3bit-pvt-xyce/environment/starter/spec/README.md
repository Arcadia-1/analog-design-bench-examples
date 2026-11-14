# 3-bit 50 MS/s flash ADC (GF180MCU, Xyce): specification

This folder is the complete public contract. Each rule is stated once: the
electrical contract here, the simulation rules in
[`signoff-rules.md`](signoff-rules.md), and what the netlist may contain in
[`netlist-guide.md`](netlist-guide.md). Grading simulates with **Xyce 7.10**
and the GF180MCU models in `/opt/gf180/pdk/gf180mcuD/libs.tech/xyce`.

## Interface

`.subckt flash_adc_3bit vss vdd vrefn vrefp clk vin b2 b1 b0`, with exactly
that pin order. `vdd` and `vss` are the supply, `vrefn` and `vrefp` the
conversion references, `clk` the only clock, `vin` the single-ended analog
input, and `b2` (MSB) to `b0` the unsigned binary output code. Each output
drives 50 fF to `vss`.

The design is a flash ADC: a reference ladder, a bank of clocked comparators,
storage that holds each decision, and a transistor-level
thermometer-to-binary encoder. You choose the circuits, device sizes and
hierarchy. Grading scores only the external electrical behavior below.

## Operating conditions

- GF180MCU 3.3 V devices. Supply `vdd` from 3.0 V to 3.6 V; temperature from
  -40 C to 125 C; process corners `typical`, `ss`, `ff`, `sf` and `fs`.
- References: `vrefp` = 2.6 V and `vrefn` = 1.0 V, each from an ideal source
  through 10 ohm. One LSB is 0.2 V: code k (0 to 7) is ideal for `vin` between
  1.0 + 0.2·k V and 1.2 + 0.2·k V, so the seven thresholds lie at 1.2 V to
  2.4 V. An input below 1.0 V converts to 0 and one above 2.6 V to 7.
- Clock: 50 MHz, from an ideal source that steps between 0 V and `vdd` with
  0.3 ns edges and 9.4 ns high time, through 50 ohm into `clk`. Its first
  rising edge starts at 10 ns. Each rising edge starts a conversion.
- Input: an ideal source through 50 ohm into `vin`, so whatever current the
  circuit draws at `vin` (comparator kickback included) moves the pin.
- The code of each conversion is read 19 ns after the clock source starts the
  rising edge, comparing each output with `vdd`/2.

## Coverage

Every point below runs at the stated corner, supply and temperature:

- Transfer bench: `typical`/3.30 V/27 C, `ss`/3.00 V/-40 C, `ss`/3.00 V/125 C,
  `ff`/3.60 V/-40 C, `ff`/3.60 V/125 C, `sf`/3.00 V/-40 C and
  `fs`/3.60 V/125 C.
- Both sine benches: `typical`/3.30 V/27 C, `ss`/3.00 V/-40 C,
  `ff`/3.60 V/125 C, `sf`/3.60 V/125 C and `fs`/3.00 V/-40 C.

This is representative coverage of the slow, cold, low-supply and fast, hot,
high-supply extremes and the skewed corners, not the complete Cartesian
product.

## Scored checks

Each check passes or fails, and must pass in every run it uses; a run that
does not complete fails only the checks that use it. `power` is scored only
when `transfer_codes` passes, so a circuit that does not convert earns no
credit for drawing little. The reward is the fraction of checks that pass.

| Check | Runs | Requirement |
|---|---|---|
| `transfer_codes` | transfer | Every code-center input converts to its code, and every input 0.3 LSB below or above a threshold converts to the code on its side, so every threshold is within 0.3 LSB of ideal |
| `overrange_codes` | transfer | Inputs of 0.4 V (below `vrefn`) read 0 and inputs of 3.0 V (above `vrefp`) read 7 |
| `output_timing` | all | In every scored conversion each output is at a valid level from 3 ns to 19 ns after the clock edge, and across the run every output toggles (a stuck output is not converting) |
| `sndr_3mhz` | 3.125 MHz sine | SNDR at least 22 dB |
| `sndr_23mhz` | 23.4 MHz sine | SNDR at least 19 dB |
| `power` | all | Average power into the circuit through all its pins at most 2.5 mW |

Measurement definitions:

- **Transfer bench.** One input value per conversion, applied 10 ns before
  the rising edge (as the clock falls) and held until 10 ns after it: the
  eight code centers (1.1 V to 2.5 V), the fourteen values 0.06 V (0.3 LSB)
  below and above each threshold, and out-of-range inputs of 0.4 V and 3.0 V.
  The 0.3 LSB window is the usual integral-linearity bound for a flash ADC:
  the threshold may drift by up to that before its code band is less than half
  an LSB wide, and the sine checks catch errors between 0.15 and 0.3 LSB.
  The first two conversions warm up the circuit and are not scored. Grading
  uses its own order: every value 0.3 LSB from a threshold is reached from a
  code on the other side of that threshold, most from two or more codes away,
  and full-scale jumps and the code 3 / code 4 transition occur in both
  directions. Ladder loading and comparator kickback are part of what is
  measured.
- **Output timing.** A valid level is at most 0.2·`vdd` (logic 0) or at least
  0.8·`vdd` (logic 1). Each output must stay on the side it is read at, from
  3 ns to 19 ns after the start of the clock edge, so the code settles within
  3 ns and holds through the read. Each of `b2`, `b1` and `b0` must also take
  both levels at some point in the run; an output tied to a rail fails.
- **3.125 MHz sine.** `vin` source = 1.8 V + 0.74 V·sin(2π·3.125 MHz·t + φ),
  coherent with the clock (bin 1 of 16). After two warm-up conversions, 16
  codes are read. φ is chosen so every ideal sample lies at least 0.15 LSB
  from a threshold for any sampling instant from the start of the clock edge
  to 1.5 ns after it, so threshold and sampling errors below 0.15 LSB do not
  change any code. An ideal 3-bit quantizer gives 22.89 dB on this record. The
  22 dB limit allows a little loss; it does not catch every single-code error
  (one near a peak can cost as little as about 0.5 dB), but a threshold more
  than 0.3 LSB off, or several errors, costs more. Static threshold accuracy is
  held by `transfer_codes`; this check adds the 0.15-0.3 LSB band.
- **23.4 MHz sine.** `vin` source = 1.8 V + 0.72 V·sin(2π·23.4375 MHz·t + φ),
  bin 15 of 32. After two warm-up conversions, 32 codes are read. The 32
  samples fall on 32 evenly spaced phases of the tone, so an ideal quantizer
  gives about 20.3 dB (20.25-20.32 dB, depending on the phase and sampling
  instant) whatever the instant. Near Nyquist some samples always lie close to
  a threshold, and a sample read on the wrong side of a threshold it nearly
  touches costs little; a code error of a whole step, from comparators sampling
  at different instants, input kickback or a slow decision, typically costs 1
  to 3 dB. The limit is 19 dB.
- **SNDR** of a record: the record mean is removed; SNDR is the power in the
  tone bin over the summed power of all other bins up to Nyquist (the Nyquist
  bin at half weight).
- **Power** is the average over the scored conversions (from the third rising
  edge, 50 ns, to 1 ns before the end of the bench) of the net power into the
  circuit at its pins: `v(vdd)·i(vdd) + v(vrefp)·i(vrefp) + v(vrefn)·i(vrefn)
  + v(clk)·i(clk) + v(vin)·i(vin)`, each pin voltage times the current flowing
  into that pin. Power taken from the clock or the input counts the same as
  supply power, and power returned into a pin (for example ladder current into
  `vrefn`) counts negative. The worst run of all three benches is graded. The
  stored-energy rule in `signoff-rules.md` applies to the same window.

Not scored: INL and DNL beyond the 0.3 LSB threshold window and the sine
records, device noise, mismatch and Monte Carlo, metastability, and supplies,
temperatures or corners outside the ranges above.

## Eligibility

Edit `/app/circuit.spi` as [`netlist-guide.md`](netlist-guide.md) describes.
Keep supplies, references, clock, input source and loads out of the circuit.
Before simulation the grader runs the check below; a failure scores 0.

```bash
python3 /app/signoff/check_circuit.py /app/circuit.spi --require-subcircuit flash_adc_3bit vss vdd vrefn vrefp clk vin b2 b1 b0
```

## Public benches

`testbench/tb_transfer_tt.cir`, `testbench/tb_sine_tt.cir` and
`testbench/tb_nyquist_tt.cir` run the three benches at
`typical`/3.30 V/27 C with the same circuit, sources, loads and metric
definitions as grading. Their stimuli differ: the public transfer bench visits
the code centers in ascending order, then each threshold's two 0.3 LSB values
back to back in ascending order of threshold, then 0.4 V and 3.0 V; the public
3.125 MHz sine uses 0.72 V amplitude and another phase (ideal 22.89 dB); the
public 23.4 MHz sine uses another phase.

`Xyce testbench/tb_transfer_tt.cir` runs a bench as it is and writes
`testbench/tb_transfer_tt.cir.csv` with the clock, input and outputs; it does
not apply the rules in [`signoff-rules.md`](signoff-rules.md). To run a bench
the way grading simulates it and print every graded metric:

```bash
cd /app
python3 signoff/run_bench.py testbench/tb_transfer_tt.cir
python3 signoff/run_bench.py testbench/tb_sine_tt.cir
python3 signoff/run_bench.py testbench/tb_nyquist_tt.cir
```

A broken rule prints a `SIGNOFF RULE:` line and exits with code 3. A clean run
covers only the nominal point; grading checks the rules in every run. To try
another corner, supply or temperature, copy a bench and change its `.lib`
section, `VSUP`, the clock `PULSE` high level and `.options device temp=`.
