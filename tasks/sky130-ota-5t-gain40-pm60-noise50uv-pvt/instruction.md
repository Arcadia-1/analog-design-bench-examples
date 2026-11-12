# Design a five-transistor OTA

Design a transistor-level, current-biased five-transistor OTA in Sky130: a
differential pair, an active current-mirror load and a tail current source.
In unity feedback with a 1 pF load it must reach at least 40 dB loop gain,
100 MHz unity-gain bandwidth and 60 degrees phase margin, with at most
50 uVrms input-referred noise and 0.5 mW, across PVT and a reference current
anywhere from 45 uA to 55 uA.

- **Deliverable:** `/app/circuit.spi`, implementing
  `.subckt ota_5t vss ibias vdd vinn vinp vout`.
- **Specification:** `/app/spec/README.md` (interface, conditions, limits,
  eligibility) and `/app/spec/signoff-rules.md` (rules every graded
  simulation obeys). Netlist syntax: `/opt/analog-arena/SKY130_NETLIST_GUIDE.md`.
- **Development:** the public benches in `/app/testbench/` run with ngspice.

Only `/app/circuit.spi` is graded, exactly as it is when your time ends; a
missing or empty file scores 0.
