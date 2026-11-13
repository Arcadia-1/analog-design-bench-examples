# Design a fully differential three-stage op amp

Design a transistor-level fully differential three-stage op amp in Sky130,
with nested Miller compensation and continuous-time common-mode feedback,
biased from a 10 uA reference. Across process, supply and temperature it must
reach 70 dB open-loop gain and 5 MHz unity-gain frequency with 60 degrees of
phase margin, and meet op-amp signoff limits on swing, rejection, noise,
distortion, slew, settling, offset, supply current and stability.

- **Deliverable:** `/app/circuit.spi`, implementing
  `.subckt fd3_nmc_opamp vss ibias vdd vcm vinp vinn voutp voutn`.
- **Specification:** `/app/spec/README.md` (interface, conditions, limits,
  measurement definitions, eligibility). Netlist syntax:
  `/opt/analog-arena/SKY130_NETLIST_GUIDE.md`.
- **Development:** the public benches in `/app/testbench/` run with ngspice.

Only `/app/circuit.spi` is graded, exactly as it is when your time ends; a
missing or empty file scores 0.
