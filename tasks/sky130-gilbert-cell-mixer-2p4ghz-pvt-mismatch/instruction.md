# Design a 2.4 GHz Gilbert-cell mixer

Design a transistor-level double-balanced Gilbert-cell downconversion mixer in
Sky130 for a 2.4 GHz receiver: RF from 2.3 to 2.5 GHz, LO 200 MHz below it,
differential 200 MHz IF output, biased from one reference current. It must
meet conversion-gain, isolation, spur, linearity, output operating-point and
power limits across process, supply, temperature, drive level, input
imbalance and local mismatch.

- **Deliverable:** `/app/circuit.spi`, implementing
  `.subckt gilbert_mixer vss iref vdd lop lon rfp rfn ifoutp ifoutn`.
- **Specification:** `/app/spec/README.md` (interface, conditions, limits,
  measurement definitions, eligibility) and `/app/spec/signoff-rules.md`
  (rules every graded simulation obeys). Netlist syntax:
  `/opt/analog-arena/SKY130_NETLIST_GUIDE.md`.
- **Development:** the public benches in `/app/testbench/` run with ngspice.

Only `/app/circuit.spi` is graded, exactly as it is when your time ends; a
missing or empty file scores 0.
