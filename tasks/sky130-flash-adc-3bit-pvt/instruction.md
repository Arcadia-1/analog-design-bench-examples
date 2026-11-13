# Design a 3-bit 50 MS/s flash ADC

Design a transistor-level 3-bit flash ADC in Sky130: a reference ladder, a
bank of clocked comparators with decision storage, and a
thermometer-to-binary encoder, clocked at 50 MS/s. Across process, supply and
temperature it must convert every code correctly with each threshold within
0.3 LSB, settle its outputs in time, and meet SNDR, SFDR and power limits.

- **Deliverable:** `/app/circuit.spi`, implementing
  `.subckt flash_adc_3bit vss vdd vrefn vrefp clk vin b2 b1 b0`.
- **Specification:** `/app/spec/README.md` (interface, conditions, limits,
  measurement definitions, eligibility) and `/app/spec/signoff-rules.md`
  (rules every graded simulation obeys). Netlist syntax:
  `/opt/analog-arena/SKY130_NETLIST_GUIDE.md`.
- **Development:** the public benches and `analyze_adc.py` in
  `/app/testbench/`, and `/app/signoff/run_bench.py`.

Only `/app/circuit.spi` is graded, exactly as it is when your time ends; a
missing or empty file scores 0.
