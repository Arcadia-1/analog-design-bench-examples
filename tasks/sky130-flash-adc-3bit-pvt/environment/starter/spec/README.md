# 3-bit 50 MS/s flash ADC: specification

This folder is the complete public contract. Rules are stated once: the
electrical contract here, the simulation rules in
[`signoff-rules.md`](signoff-rules.md), and the SKY130 netlist syntax in
`/opt/analog-arena/SKY130_NETLIST_GUIDE.md`.

## Interface

`.subckt flash_adc_3bit vss vdd vrefn vrefp clk vin b2 b1 b0`, with exactly
that pin order. `vdd` and `vss` are the supply, `vrefn` and `vrefp` the
conversion references, `clk` the only clock, `vin` the single-ended analog
input, and `b2` (MSB) to `b0` the unsigned binary output code. Each output
drives 15 fF to `vss`.

The design is a flash ADC: a reference ladder, a bank of clocked comparators,
storage that holds each decision, and a transistor-level
thermometer-to-binary encoder. You choose the circuits, device sizes and
hierarchy. Grading scores only the external electrical behavior below.

## Operating conditions

- Supply `vdd` from 1.62 V to 1.98 V; temperature from -40 C to 125 C; Sky130
  corners `tt`, `ss`, `ff`, `sf` and `fs`.
- References: `vrefp` = 1.4 V and `vrefn` = 0.6 V, each from an ideal source
  through 10 ohm. One LSB is 0.1 V: code k (0 to 7) is ideal for `vin` between
  0.6 + 0.1·k V and 0.7 + 0.1·k V, so the seven thresholds lie at 0.7 V to
  1.3 V.
- Clock: 50 MHz, from an ideal source that steps between 0 V and `vdd` with
  0.3 ns edges and 9.4 ns high time, through 50 ohm into `clk`. Its first
  rising edge starts at 10 ns. Each rising edge starts a conversion.
- `vin` comes from an ideal voltage source.
- The code of each conversion is read 19 ns after the clock source starts the
  rising edge, comparing each output with `vdd`/2.

## Coverage

Every point below runs at the stated corner, supply and temperature:

- Transfer bench: `tt`/1.80 V/27 C, `ss`/1.62 V/-40 C, `ss`/1.62 V/125 C,
  `ff`/1.98 V/-40 C, `ff`/1.98 V/125 C, `sf`/1.62 V/-40 C and
  `fs`/1.98 V/125 C.
- Sine bench: `tt`/1.80 V/27 C, `ss`/1.62 V/-40 C, `ff`/1.98 V/125 C,
  `sf`/1.98 V/125 C and `fs`/1.62 V/-40 C.

This is representative coverage of the slow, cold, low-supply and fast, hot,
high-supply extremes and the skewed corners, not the complete Cartesian
product. The `tt`/1.80 V/27 C transfer run is the gate: if any of its codes is
wrong, every check fails.

## Scored checks

Each check passes or fails, and must pass in every run it covers; the reward
is the fraction that pass.

| Check | Requirement |
|---|---|
| `code_centers` | Every code-center input of the transfer bench converts to its code |
| `thresholds_0p3lsb` | Every input 0.3 LSB below or above a threshold in the transfer bench converts to the code on its side, so every threshold is within 0.3 LSB of ideal |
| `output_timing` | In every scored conversion of both benches, each output is at a valid level from 3 ns to 19 ns after the clock edge |
| `sndr_22db` | SNDR of the coherent sine record at least 22 dB |
| `sfdr_24p5db` | SFDR of the same record at least 24.5 dB |
| `power_1mw` | Average power into the circuit through all its pins at most 1 mW, in every run of both benches |

Measurement definitions:

- **Transfer bench.** One input value per conversion, applied 10 ns before
  the rising edge (as the clock falls) and held until 10 ns after it: the
  eight code centers (0.65 V to 1.35 V) and the fourteen values 0.03 V
  (0.3 LSB) below and above each threshold. The first two conversions warm up
  the circuit and are not scored; 31 are scored. Grading uses its own order,
  with each value 0.3 LSB from a threshold reached from a code on the other
  side of that threshold, and with full-scale jumps and the code 3 / code 4
  transition in both directions. Ladder loading and comparator kickback are part
  of what is measured.
- **Output timing.** A valid level is at most 0.2·`vdd` (logic 0) or at least
  0.8·`vdd` (logic 1). Each output must stay on the side it is read at, from
  3 ns to 19 ns after the start of the clock edge, so the code settles
  within 3 ns and holds through the read.
- **Sine record.** `vin` = 1.0 V + 0.37 V·sin(2π·3.125 MHz·t + φ), coherent
  with the clock (bin 1 of 16). After two warm-up conversions, 16 codes are
  read. φ is chosen so every ideal sample lies at least 0.15 LSB from a
  threshold for any sampling instant from the start of the clock edge to
  1.5 ns after it, so threshold errors below 0.15 LSB do not change any code.
  The record mean is removed; SNDR is the power in bin 1 over the summed
  power of bins 2 to 8 (bin 8, Nyquist, at half weight); SFDR is the power in
  bin 1 over the largest of those bins.
- **Why 22 dB and 24.5 dB.** An ideal 3-bit quantizer gives 22.89 dB SNDR and
  25.64 dB SFDR on this record. The limits allow about 1 dB of loss: a code
  error in the record usually costs more.
- **Power** is the average over the scored conversions (from the third rising
  edge to the end of the bench) of the net power into the circuit at its
  pins: `v(vdd)·i(vdd) + v(vrefp)·i(vrefp) + v(vrefn)·i(vrefn) + v(clk)·i(clk)
  + v(vin)·i(vin)`, each current flowing into the pin. Power taken from the
  clock or the input counts the same as supply power, and power returned
  into a pin (for example ladder current into `vrefn`) counts negative. It is
  measured in every transfer and sine run and the worst run is graded. Each
  window is a `meas tran ... avg` window, so the stored-energy rule in
  `signoff-rules.md` applies to it.

Not scored: INL and DNL (there is no slow ramp; static accuracy is graded by
the 0.3 LSB threshold window), threshold errors smaller than 0.3 LSB, device
noise, mismatch and Monte Carlo, metastability, input impedance other than
through power, input tones above 3.125 MHz (full-rate steps are graded by the
transfer bench), and supplies, temperatures or corners outside the ranges
above.

## Deliverable and eligibility

- Edit `/app/circuit.spi`. Use Sky130 PDK devices, positive ideal resistors
  and positive ideal capacitors; parameter-free local subcircuits are
  allowed. Independent or controlled sources, behavioral elements, switches,
  inductors, models, includes, parameters and simulator directives are not
  allowed. Keep supplies, references, clock, input and loads out of the
  circuit.
- Before simulation the grader runs the check below; a failure scores 0.

  ```bash
  check_circuit.py /app/circuit.spi --allow-ideal R C --require-subcircuit flash_adc_3bit vss vdd vrefn vrefp clk vin b2 b1 b0
  ```

## Public benches

`testbench/tb_transfer_tt.spi` and `testbench/tb_sine_tt.spi` run both benches
at `tt`/1.80 V/27 C with the same circuit, sources, measurements and metric
definitions as grading. Their stimuli differ: the public transfer bench visits
the code centers in ascending order, then each threshold's two 0.3 LSB values
back to back in ascending order of threshold, and the public sine record uses 0.36 V amplitude and
another phase (ideal 22.89 dB SNDR, 25.64 dB SFDR).

Plain `ngspice` does not apply the rules in [`signoff-rules.md`](signoff-rules.md).
To run a bench the way grading simulates it and print the graded metrics:

```bash
cd /app
python3 signoff/run_bench.py testbench/tb_transfer_tt.spi > transfer.log
python3 testbench/analyze_adc.py testbench/tb_transfer_tt.spi transfer.log
python3 signoff/run_bench.py testbench/tb_sine_tt.spi > sine.log
python3 testbench/analyze_adc.py testbench/tb_sine_tt.spi sine.log
```

`run_bench.py` applies the same rewrite as grading (model binning, diffusion
geometry, multipliers, voltage ratings, stored-energy accounting). A broken
rule prints a `SIGNOFF RULE:` line and exits with code 3. A clean run covers
only the nominal point; grading checks the rules in every run.
