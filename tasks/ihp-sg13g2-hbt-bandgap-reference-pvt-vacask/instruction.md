# Design a bandgap voltage reference in IHP SG13G2, simulated with VACASK

Design a transistor-level bandgap voltage reference with an enable input,
using the SiGe HBTs and the 3.3 V MOS devices of IHP SG13G2. Across process,
supply (3.0 V to 3.6 V) and temperature (-40 C to 125 C) its output must stay
between 1.00 V and 1.15 V with a low temperature coefficient, reject supply
and load changes, keep its noise and current low, shut down when disabled and
start up cleanly. The simulator is **VACASK 0.3.4**, whose netlist language is
not SPICE.

- **Deliverable:** `/app/circuit.sim`, implementing
  `subckt bandgap (vdd vss en vref)`.
- **Specification:** `/app/spec/README.md` (interface, conditions, limits,
  measurement definitions, eligibility), `/app/spec/signoff-rules.md` (rules
  every graded simulation obeys) and `/app/spec/netlist-guide.md` (VACASK and
  IHP SG13G2 netlist syntax).
- **Development:** the public benches in `/app/testbench/` and
  `/app/signoff/run_bench.py`, which simulates a bench the way grading does
  and prints every graded metric.

Only `/app/circuit.sim` is graded, exactly as it is when your time ends; a
missing or empty file scores 0.
