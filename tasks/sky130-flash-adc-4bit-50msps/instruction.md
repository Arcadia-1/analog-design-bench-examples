# Design a 4-bit 50 MS/s flash ADC

Design a transistor-level, fully differential 4-bit flash ADC in Sky130: a
comparator bank on a reference ladder and a thermometer-to-binary encoder,
clocked by a single 50 MHz clock from a 1.71–1.89 V supply. Across process,
supply and -40 C to 125 C it must convert every code without error or
missing codes, keep INL and DNL within 0.5 LSB, reach 22 dB SNDR and 26 dB
SFDR, and take at most 5 mW from all external sources.

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
