# 4-bit 50 MS/s flash ADC: specification

This folder is the complete public contract. Rules are stated once: the
electrical contract here, the simulation rules in
[`signoff-rules.md`](signoff-rules.md), and the SKY130 netlist syntax in
`/opt/analog-arena/SKY130_NETLIST_GUIDE.md`.

## Interface

`.subckt flash_adc_4bit clk dout3 dout2 dout1 dout0 vss vdd vinn vinp vrefn vrefp`,
with exactly that pin order. `vdd` is the supply, `vss` ground, `vrefp`
(1.8 V, from a source with 10 ohm output resistance) and `vrefn` (0 V) the
conversion references, `vinp`/`vinn` the
differential input, `clk` the only clock, `dout3` (MSB) to `dout0` the output
code. Each output drives 10 fF.

The design is a flash ADC: a parallel comparator bank and a transistor-level
thermometer-to-binary encoder. You choose the ladder, sampling, comparator,
latch and encoder circuits, device sizes and hierarchy. Grading scores only
the external electrical behavior below.

## Operating conditions

- Supply `vdd` from 1.71 V to 1.89 V (1.8 V ± 5 %); temperature from -40 C to
  125 C; Sky130 corners `tt`, `ss`, `ff`, `sf` and `fs`. The references stay
  at 1.8 V and 0 V. Mismatch and noise are not graded.
- 50 MS/s: `clk` is a rail-to-rail (0 to `vdd`), 50 % square wave with 20 ps
  edges, driven through 50 ohm. `clk = 0` is acquisition; the rising edge ends sampling and
  `clk = 1` is conversion. Generate any complementary clock inside the
  circuit.
- The inputs are ideal voltage sources around a 0.9 V common mode, spanning
  ±1.7 V differential (`vinp − vinn`) between `vrefn` and `vrefp`.
- The output code is read 9 ns after each rising edge, with a 0.9 V
  threshold.

## Coverage

Grading runs every bench below at representative points of the supply and
temperature ranges at all five process corners, including the slow, cold,
low-supply and fast, hot, high-supply extremes. This is representative
coverage, not the complete Cartesian product. The nominal `tt`/1.8 V/27 C point
runs first; if its transfer check fails, nothing else runs.

## Scored checks

Each check passes or fails, and must pass at every graded point; the reward is
1 only when all pass.

| Check | Requirement |
|---|---|
| `transfer` | A full-rate ascending sequence through all 16 code centers converts with zero errors and no missing code |
| `inl` | Static INL at most 0.5 LSB |
| `dnl` | Static DNL at most 0.5 LSB, monotonic, no missing code |
| `sndr` | SNDR of a full-scale coherent sine at least 22 dB |
| `sfdr` | SFDR of the same record at least 26 dB |
| `power` | Average power from all external sources at most 5 mW |

Measurement definitions:

- **Transfer.** Code `k` (0–15) is applied as `vinp = 0.05625 + 0.1125·k`,
  `vinn = 1.74375 − 0.1125·k` for one conversion each, after three warm-up
  conversions. The transfer run comes first; if it fails, nothing else runs.
- **INL and DNL.** A slow differential ramp across the full ±1.7 V range,
  sampled at the conversion rate, must pass through all 16 codes in order with
  exactly 15 adjacent transitions. The first and last transition levels define
  the endpoint LSB; INL is the deviation of each transition from that line,
  DNL the deviation of each code width from one LSB.
- **Why 22 dB and 26 dB.** An ideal 4-bit quantizer gives 6.02·4 + 1.76 =
  25.8 dB SNDR at full scale; on this record (1.7 V of the 1.8 V range, 32
  samples) it gives 25.3 dB SNDR and 30.0 dB SFDR. 22 dB allows about half a
  bit of loss (ENOB at least 3.4 bits) across PVT; 26 dB allows 4 dB of spur
  growth, so a code-dependent error that adds one large spur fails even
  when the total SNDR passes.
- **SNDR and SFDR.** A full-scale (1.7 V differential peak) coherent sine is
  converted for 32 samples after three warm-up conversions. SNDR is the
  signal-bin power over all other non-DC power below Nyquist; SFDR is the
  signal-bin power over the largest other bin.
- **Power** is averaged over the sine record after warm-up. It is the sum of
  the power delivered by `vdd` and `vrefp`, by the clock source into the `clk`
  pin, and by the input sources into `vinp` and `vinn`. Energy the circuit
  takes from the clock or the signal inputs therefore counts the same as
  supply power.

## Deliverable and eligibility

- Edit `/app/circuit.spi`. Use only `sky130_fd_pr__nfet_01v8`,
  `sky130_fd_pr__pfet_01v8`, positive ideal resistors and positive ideal
  capacitors; local hierarchy is allowed. Every MOS needs numeric `l`, `w` and
  an integer `nf`. Sources, behavioral elements, switches, Verilog-A, models,
  includes, parameters and simulator directives are not allowed.
- Before simulation the grader runs the check below and requires every pin of
  `flash_adc_4bit` to be connected and at least one MOS device to be
  reachable; a failure scores 0.

  ```bash
  check_circuit.py /app/circuit.spi --allow-ideal R C
  ```

## Public benches

From `/app/testbench`, run each bench and analyze its raw file:

```bash
ngspice -b -r transfer.raw tb_transfer_tt.spi && python3 analyze_flash_adc.py transfer transfer.raw
ngspice -b -r linearity.raw tb_linearity_tt.spi && python3 analyze_flash_adc.py linearity linearity.raw
ngspice -b -r dynamic.raw tb_dynamic_tt.spi && python3 analyze_flash_adc.py dynamic dynamic.raw
```

They run at `tt`/1.8 V/27 C with the same circuit and metric definitions as
grading, and shorter, different stimuli: an eight-code subset, a 64-sample ramp, and a
16-point sine at a different frequency. Grading uses its own sequences. The
public benches do not apply the signoff rules.
