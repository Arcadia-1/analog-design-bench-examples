# Design a 28 Gb/s NRZ tunable CTLE

Design an active, fully differential continuous-time linear equalizer in Sky130
for the receiver front end of a 28 Gb/s NRZ link: a 50 ohm input matched to
14 GHz, a 2-bit gain code for the low-frequency gain and a 2-bit peak code for
the 14 GHz gain, with flat in-band response, enough bandwidth, a stable bias
that tracks `vocm`, and limited power, across process, supply and temperature.

- **Deliverable:** `/app/circuit.spi`, implementing
  `.subckt ctle_28g vss vdd iref vocm vinp vinn gain_b0 gain_b1 peak_b0 peak_b1 voutp voutn`.
- **Specification:** `/app/spec/README.md` (interface, conditions, limits,
  measurement definitions, eligibility) and `/app/spec/signoff-rules.md`
  (rules every graded simulation obeys). Netlist syntax:
  `/opt/analog-arena/SKY130_NETLIST_GUIDE.md`.
- **Development:** the public benches in `/app/testbench/` run with ngspice.

Only `/app/circuit.spi` is graded, exactly as it is when your time ends; a
missing or empty file scores 0.
