# Design a 3-bit 50 MS/s flash ADC in GF180MCU, simulated with Xyce

Design a transistor-level 3-bit flash ADC with the GF180MCU 3.3 V devices: a
reference ladder, a bank of clocked comparators with decision storage, and a
thermometer-to-binary encoder, clocked at 50 MS/s. At a representative set of
process, supply and temperature points it must convert every code correctly
with each threshold within 0.3 LSB, clamp out-of-range inputs to 0 and 7,
settle its outputs in time, and meet SNDR limits at 3 MHz and near Nyquist and
a power limit.
The simulator is **Xyce 7.10**, not ngspice.

- **Deliverable:** `/app/circuit.spi`, implementing
  `.subckt flash_adc_3bit vss vdd vrefn vrefp clk vin b2 b1 b0`.
- **Specification:** `/app/spec/README.md` (interface, conditions, limits,
  measurement definitions, eligibility), `/app/spec/signoff-rules.md` (rules
  every graded simulation obeys) and `/app/spec/netlist-guide.md` (Xyce and
  GF180MCU netlist syntax).
- **Development:** the three public benches in `/app/testbench/` and
  `/app/signoff/run_bench.py`, which simulates a bench the way grading does
  and prints every graded metric.

Only `/app/circuit.spi` is graded, exactly as it is when your time ends; a
missing or empty file scores 0.
