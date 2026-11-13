# Signoff rules

These rules apply to every simulation of your circuit in final grading. Breaking one in any run scores the
task 0, and the report names the run and the device. `/app/signoff/run_bench.py` applies them to the public benches
with the grader's own code.

- **Simulator.** Xyce 7.10 with the GF180MCU models of `/opt/gf180/pdk/gf180mcuD/libs.tech/xyce`. Each run writes
  every node voltage and source current to a raw file, and the rules below are checked on it. A run that fails is
  run once more with gear integration (`.options timeint method=gear`); if that also fails, every check that depends
  on the run fails.
- **Diffusion geometry.** A MOS that does not set them gets the source and drain diffusion geometry of the GF180MCU
  xschem symbols: `ad = as = w·0.18u`, `pd = ps = 2·(w + 0.18u)`. Without it the models have no junction
  capacitance. Write them on your devices if you want plain Xyce runs to match.
- **Multipliers.** `m=K` on an instance of a local subcircuit is simulated as K parallel copies; `m=K` on an ideal
  resistor or capacitor as the equivalent single value.
- **Voltage ratings**, at every time point of every run, including the operating point at t = 0: no MOS may see more
  than 4.0 V gate-source, gate-drain or drain-source, or 5.5 V gate-bulk. The 3.3 V devices have a maximum operating
  voltage of 3.63 V; 4.0 V adds 10 % for switching transients such as clock feedthrough onto a precharged node. A
  node driven or bootstrapped above the supply, or a floating node (for example inside a series stack) that coupling
  pumps past a rail, can exceed it.
- **Junctions.** At the operating point (t = 0) no source-bulk or drain-bulk junction may be forward biased by more
  than 0.4 V.
- **Stored energy.** In each power window (50 ns to 1 ns before the end of both benches) the energy stored in your
  capacitors may not end the window more than 5 % of the energy the bench's sources deliver in it (plus
  20 nA × supply × window length, plus 0.5 fJ) below the lowest level it had since the start of the run. The stored
  energy counts every ideal capacitor (½·C·v²) and the gate-oxide energy of every MOS whose channel carries no
  current (drain and source on one node, joined through resistors, or one of them connected to nothing else; or
  gate on source): ½·Cox·W·L·m·v², with Cox = 4.3 fF/µm². A design may not run on charge stored at the operating
  point, for example a large capacitor on an internal rail fed through a high resistance.
