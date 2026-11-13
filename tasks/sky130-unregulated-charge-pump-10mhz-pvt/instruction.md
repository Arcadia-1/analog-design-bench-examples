# Design an unregulated charge pump

Design a transistor-level, voltage-doubling charge pump with an active-high
enable in Sky130. Running from its supply and a clock, it must hold a pumped
output under load with low ripple and low supply current, take its energy
only from its supply, and draw almost nothing when disabled.

- **Deliverable:** `/app/circuit.spi`, implementing
  `.subckt charge_pump_unregulate AVDD AVSS CLK EN VOUT`.
- **Specification:** `/app/spec/README.md` (interface, conditions, limits,
  eligibility) and `/app/spec/signoff-rules.md` (rules every graded
  simulation obeys). Netlist syntax: `/opt/analog-arena/SKY130_NETLIST_GUIDE.md`.
- **Development:** the public benches in `/app/testbench/` run with ngspice.

Only `/app/circuit.spi` is graded, exactly as it is when your time ends; a
missing or empty file scores 0.
