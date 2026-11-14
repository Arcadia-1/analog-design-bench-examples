# Signoff rules

These rules apply to every simulation of your circuit in final grading. Breaking one in any run scores the
task 0, and the report names the run and the device. `/app/signoff/run_bench.py` applies them to the public benches
with the grader's own code.

- **Simulator.** Xyce 7.10 with the GF180MCU models of `/opt/gf180/pdk/gf180mcuD/libs.tech/xyce`. Each run writes
  every node voltage and source current to a raw file, and the rules below are checked on it. A run that fails is
  run once more with gear integration (`.options timeint method=gear`); if that also fails, every check that depends
  on the run fails.
- **Diffusion geometry.** Every MOS gets the source and drain diffusion geometry of the GF180MCU xschem symbols:
  `ad = as = w·0.18u`, `pd = ps = 2·(w + 0.18u)` (the netlist may not set it). A plain Xyce run of your netlist
  has no junction capacitance unless you add these values to a copy yourself.
- **Multipliers.** `m=K` on an instance of a local subcircuit is simulated as K parallel copies; `m=K` on an ideal
  resistor or capacitor as the equivalent single value.
- **Voltage ratings**, at every time point of every run, including the operating point at t = 0: no MOS may see more
  than 4.0 V gate-source, gate-drain or drain-source, or 5.5 V gate-bulk. The 3.3 V devices have a maximum operating
  voltage of 3.63 V; 4.0 V adds 10 % for switching transients such as clock feedthrough onto a precharged node. A
  node driven or bootstrapped above the supply, or a floating node (for example inside a series stack) that coupling
  pumps past a rail, can exceed it.
- **Junctions.** At the operating point (t = 0) no source-bulk or drain-bulk junction may be forward biased by more
  than 0.4 V.
- **Stored energy.** In each power window (50 ns to 1 ns before the end of every bench) the energy stored in your
  circuit may not end the window more than 2 % of the energy the bench's sources deliver in it (plus
  20 nA × supply × window length, plus 0.5 fJ) below its level at the start of the window, so a reservoir charged
  during the unmetered warm-up and spent in the window is caught. The stored
  energy counts every ideal capacitor (½·C·v²), the gate oxide of every MOS (½·Cox·W·L·m·v², v from the gate to the
  mean of source and drain, Cox = 4.3 fF/µm²) and the source and drain junctions of every MOS
  (½·(Cj·A + Cjsw·P)·m·v², v from the diffusion to the bulk, with A and P from the geometry above and the model
  cards' Cj ≈ 0.95 fF/µm² and Cjsw ≈ 0.15 fF/µm). A design may not run on charge stored at the operating point, for
  example a large capacitor or MOS gate charged before the window on an internal rail fed through a high resistance.
