# Signoff rules

These rules apply to every simulation of your circuit in final grading. Breaking one in any run scores the
task 0, and the report names the run and the device.

- **Simulator setting.** ngspice reads the SKY130 libraries with the PDK's own setting, `set ngbehavior=hsa`, so a
  multi-finger device takes the model bin of its finger width `w/nf`. `/app/.spiceinit` and `/app/testbench/.spiceinit`
  carry the same line for your own runs.
- **Diffusion geometry.** A MOS instance that does not set them gets the source and drain diffusion geometry of xschem's
  SKY130 symbols: with `nf` fingers of width `w/nf` (µm), `ad = int((nf+1)/2)·(w/nf)·0.29`,
  `as = int((nf+2)/2)·(w/nf)·0.29`, `pd = 2·int((nf+1)/2)·(w/nf + 0.29)`, `ps = 2·int((nf+2)/2)·(w/nf + 0.29)`. Write
  them on your instances if you want your own runs to match.
- **Netlist.** An ideal R, C or L is written as its value alone, optionally with `m=`; instance parameters such as
  `noisy`, `dtemp`, `temp` or `tc1` are rejected. `m=K` on an instance of a parameter-free local subcircuit makes K
  parallel copies and must be a whole number. On PDK devices `m=K` sets `mult=K` too, so copies average their local
  mismatch. Every MOS must be one of `nfet_01v8`, `pfet_01v8`, `nfet_01v8_lvt`, `pfet_01v8_lvt`, `pfet_01v8_hvt`,
  `nfet_03v3_nvt`, `nfet_05v0_nvt`, `nfet_g5v0d10v5` or `pfet_g5v0d10v5`. `res_xhigh_po` is simulated with the PDK's
  temperature coefficient.
- **Voltage ratings**, at every operating point (an `op`, or a transient's t = 0) and throughout every transient; DC
  sweeps are exempt, since they drive nodes past normal operation on purpose:

  | devices | gate-source | gate-drain, drain-source | gate-bulk |
  |---|---|---|---|
  | `*_01v8*` | 2.8 V | 2.8 V | 4.0 V |
  | `nfet_03v3_nvt` | 4.0 V | 4.0 V | 6.0 V |
  | `nfet_05v0_nvt` | 6.0 V | 6.0 V | 9.0 V |
  | `*_g5v0d10v5` | 6.0 V | 11 V | 11 V |

  At an operating point no source-bulk or drain-bulk junction may be forward biased by more than 0.4 V.
- **Model check.** A device whose binned model parameters come out invalid at a corner or temperature (ngspice prints
  `Fatal: … is not positive`) fails the run; change its `w`, `nf` or `l`.
- **Stored energy.** In every window where a bench averages or integrates a source current (`meas tran … avg|integ …`),
  the energy stored in your capacitors, inductors and MOS capacitors may not end the window more than 5 % of the energy
  the bench's sources deliver in it (plus 20 nA × supply × window length, plus 0.5 fJ) below the lowest level it had since
  the start of the run: a design may not run on charge stored at the operating point.
- **Convergence.** A run that fails to converge, or reaches its operating point only through ngspice's transient-op
  fallback, runs once more with more gmin and source steps and DC iterations (gear integration after a timestep failure).
