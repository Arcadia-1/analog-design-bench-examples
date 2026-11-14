# Design a fully differential op amp with common-mode feedback

Design a transistor-level fully differential op amp in Sky130 with
continuous-time common-mode feedback, biased from a 10 uA reference. Across
process, supply, temperature, load and mismatch it must meet the op-amp
signoff limits on gain, bandwidth, stability, slew, settling, swing,
rejection, noise, distortion, offset and supply current given in the
specification. The topology and the number of stages are your choice.

- **Deliverable:** `/app/circuit.spi`, implementing
  `.subckt fd_opamp vss ibias vdd vcm vinp vinn voutp voutn`.
- **Specification:** `/app/spec/README.md` (interface, conditions, limits,
  measurement definitions, eligibility). Netlist syntax:
  `/opt/analog-arena/SKY130_NETLIST_GUIDE.md`.
- **Development:** the public benches in `/app/testbench/` run with ngspice.

Only `/app/circuit.spi` is graded, exactly as it is when your time ends; a
missing or empty file scores 0.
