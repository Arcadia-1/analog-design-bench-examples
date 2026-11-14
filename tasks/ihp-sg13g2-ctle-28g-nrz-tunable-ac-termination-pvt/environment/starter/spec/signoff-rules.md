# Signoff rules

These rules apply to every simulation of your circuit in final grading. Breaking one in any run scores the task 0,
and the report names the run and the device. `/app/signoff/run_bench.py` applies them to a public bench.

- **Simulator setting.** ngspice 46 reads the IHP SG13G2 libraries with the PDK's own `.spiceinit`, which loads
  the OSDI models (PSP 103 MOS, R3_CMC resistors, the MOS varactor). The grader writes that file into every run
  directory, plus `set num_threads=1`. The HBT model keeps the PDK's self-heating; ngspice prints "The
  temperature limiting function received NaN" once per run, a harmless warning from that model.
- **Netlist.** The file holds only `.subckt` blocks (an optional `.end` is ignored). Inside them: ideal `R`, `C`
  and `L` written as `name node node value`; `K name L1 L2 k` coupling two different `L` of the same subcircuit with
  `0 < k < 1`; and `X` instances of a local subcircuit (parameter `m=` only, a whole number of parallel copies) or of
  an allowed PDK device with its allowed parameters ([`netlist-guide.md`](netlist-guide.md)). Every value is a
  numeric literal with an optional SPICE scale suffix (`f p n u m k meg g t`). No `.param`, `.include`, `.lib`,
  `.model`, analyses or other control lines, no `{...}`, quotes or inline comments, and no node named `0` or `gnd`
  (use `vss`). Ideal elements stand for on-die passives: an ideal `L` is at most 5 nH and an ideal `C` at most
  50 pF. The p substrate is `vss`: every NMOS bulk and the `bn` terminal of every HBT, resistor and varactor
  must connect to the `vss` pin (this device set has no isolated p-well). Ideal elements are allowed because the PDK
  has no simulation model for inductors and transformers; keep supplies, sources and loads out of the circuit.
  `python3 /app/signoff/check_circuit.py /app/circuit.spi` applies this rule.
- **Voltage ratings**, at every operating point and at every time point of every transient the benches write out
  (every 0.5 ns or finer):

  | devices | limit |
  |---|---|
  | `sg13_lv_nmos`, `sg13_lv_pmos` | 1.32 V on gate-source, gate-drain, drain-source and gate-bulk |
  | `sg13_hv_nmos`, `sg13_hv_pmos` | 3.63 V on gate-source, gate-drain, drain-source and gate-bulk |
  | `sg13_hv_svaricap` | 3.63 V between each gate and the well |
  | `npn13G2` | collector-emitter at most 1.6 V; base-emitter within ±1.6 V; base-collector within ±5.1 V |

  The MOS and varactor limits are the nominal operating voltage (1.2 V or 3.3 V) plus 10 %; the HBT limits are the
  `vce_max`, `vbe_max` and `vbc_max` of the PDK's own VBIC model card. Readings within 1 mV of a limit pass
  (numerical noise of a node held at a rail).
- **Junctions.** At every operating point no MOS source-bulk or drain-bulk junction, no HBT collector-substrate
  junction and no varactor well-substrate junction may be forward biased by more than 0.4 V.
- **Convergence.** A run that does not converge (a timestep too small, an aborted analysis, or an operating point
  that could not be found) runs once more with
  `.options gminsteps=50 srcsteps=50 itl1=1000 itl2=500 itl4=200`. A run that still fails scores as missing
  measurements. A run still going after 300 s is stopped, not retried, and scores as missing measurements; runs that
  would start after 3000 s of grading are not run and score the same way.
