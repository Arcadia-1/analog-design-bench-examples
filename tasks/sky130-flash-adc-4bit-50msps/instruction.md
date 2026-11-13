# Design a 4-bit 50 MS/s flash ADC

Design a transistor-level, fully differential 4-bit flash ADC in Sky130: a
comparator bank on a reference ladder and a thermometer-to-binary encoder,
clocked by a single clock. Across process, supply and temperature it must
convert every code correctly and meet dynamic-range and power limits.

- **Deliverable:** `/app/circuit.spi`, implementing
  `.subckt flash_adc_4bit clk dout3 dout2 dout1 dout0 vss vdd vinn vinp vrefn vrefp`.
- **Specification:** `/app/spec/README.md` (interface, conditions, limits,
  measurement definitions, eligibility) and `/app/spec/signoff-rules.md`
  (rules every graded simulation obeys). Netlist syntax:
  `/opt/analog-arena/SKY130_NETLIST_GUIDE.md`.
- **Development:** the public benches and `analyze_flash_adc.py` in
  `/app/testbench/`.

Only `/app/circuit.spi` is graded, exactly as it is when your time ends; a
missing or empty file scores 0.
