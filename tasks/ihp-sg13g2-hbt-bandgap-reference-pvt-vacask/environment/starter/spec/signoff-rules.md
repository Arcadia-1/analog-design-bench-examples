# Signoff rules

These rules apply to every simulation of your circuit in final grading. Breaking one in any run scores the task 0,
and the report names the run and the device. `/app/signoff/run_bench.py` applies them to the public benches with the
grader's own code (`/app/signoff/bgr_signoff.py`).

- **Simulator.** VACASK 0.3.4 with the IHP SG13G2 models of `/opt/ihp/pdk/ihp-sg13g2/libs.tech/vacask`, including
  the self-heating of the HBTs. Each run writes every node voltage to a raw file, and the rules below are checked on
  it. A run that fails is not repeated: every check that depends on it fails.
- **Voltage ratings**, at every DC operating point and at every time point of every startup transient, including
  t = 0:
  - HV MOS (`sg13_hv_nmos`, `sg13_hv_pmos`): |Vgs|, |Vgd|, |Vds| and |Vgb| at most 3.63 V, the 3.3 V rating plus
    10 %.
  - LV MOS (`sg13_lv_nmos`, `sg13_lv_pmos`): the same at most 1.32 V, the 1.2 V rating plus 10 %.
  - HBTs: while the base-emitter junction conducts (Vbe ≥ 0.5 V), |Vce| at most 1.6 V for `npn13G2` and
    `npn13G2l` and at most 2.5 V for `npn13G2v`, the maximum collector-emitter voltages of the PDK model cards;
    base-emitter at most 1.0 V forward and 1.0 V reverse at all times.
- **Junctions.** At every DC operating point no PMOS source-to-well or drain-to-well junction may be forward biased
  by more than 0.4 V. The substrate terminals (NMOS bulk, HBT and resistor `bn`) are tied to `vss` by the
  eligibility check.
- Each limit has a 1 mV tolerance.

There is no stored-energy rule: every graded current is a DC operating point, and every transient starts from the
operating point with the supply at 0 V or the circuit disabled, so stored charge cannot supply a graded quantity.
