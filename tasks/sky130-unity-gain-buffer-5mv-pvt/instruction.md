# Design a unity-gain buffer with 5 mV tracking error

Design a transistor-level, current-biased unity-gain buffer in Sky130 that
tracks its input within 5 mV, reaches at least 100 MHz closed-loop bandwidth,
settles 100 mV steps within 10 ns and loads its source by at most 0.5 pF,
across the complete 45-point PVT matrix.

- **Deliverable:** `/app/circuit.spi`, implementing
  `.subckt unity_gain_buffer vss ibias vdd vin vout`.
- **Specification:** `/app/spec/README.md` (interface, conditions, limits,
  measurement definitions, eligibility). Netlist syntax:
  `/opt/analog-arena/SKY130_NETLIST_GUIDE.md`.
- **Development:** the public benches in `/app/testbench/` run with ngspice.

Only `/app/circuit.spi` is graded, exactly as it is when your time ends; a
missing or empty file scores 0.
