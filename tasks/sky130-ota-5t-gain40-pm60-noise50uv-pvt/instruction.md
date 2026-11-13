# Design a five-transistor OTA

Design a transistor-level, current-biased five-transistor OTA in Sky130: a
differential pair, an active current-mirror load and a tail current source.
In unity feedback it must meet loop-gain, bandwidth, stability, noise,
power and rejection limits across process, supply, temperature and the
reference-current range.

- **Deliverable:** `/app/circuit.spi`, implementing
  `.subckt ota_5t vss ibias vdd vinn vinp vout`.
- **Specification:** `/app/spec/README.md` (interface, conditions, limits,
  measurement definitions, eligibility) and `/app/spec/signoff-rules.md`
  (rules every graded simulation obeys). Netlist syntax:
  `/opt/analog-arena/SKY130_NETLIST_GUIDE.md`.
- **Development:** the public benches in `/app/testbench/` run with ngspice.

Only `/app/circuit.spi` is graded, exactly as it is when your time ends; a
missing or empty file scores 0.
