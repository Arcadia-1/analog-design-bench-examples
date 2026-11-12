# Design a fully differential sampling-feedback OTA

Design a fully differential OTA with continuous-time common-mode feedback in
Sky130 for a gain-8 capacitive sampling-feedback stage (`Cs = 8 Cf = 1 pF`,
250 fF per output): it must settle a 0.9 V output step to 1 % within 10 ns
with at most 1 mVrms output noise across PVT, and keep its rejection under
local mismatch.

- **Deliverable:** `/app/circuit.spi`, implementing
  `.subckt sampling_feedback_ota vss iref vdd vinn vinp vocm voutn voutp`.
- **Specification:** `/app/spec/README.md` (interface, conditions, limits,
  measurement definitions, eligibility) and `/app/spec/signoff-rules.md`
  (rules every graded simulation obeys). Netlist syntax:
  `/opt/analog-arena/SKY130_NETLIST_GUIDE.md`.
- **Development:** the public benches in `/app/testbench/` run with ngspice.

Only `/app/circuit.spi` is graded, exactly as it is when your time ends; a
missing or empty file scores 0.
