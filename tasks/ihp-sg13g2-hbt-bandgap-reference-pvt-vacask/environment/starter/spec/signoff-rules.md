# Signoff rules

These rules apply to every simulation of your circuit in final grading. Breaking one in any run scores the task 0,
and the report names the run and the device. `/app/signoff/run_bench.py` applies them to the public benches with the
grader's own code (`/app/signoff/bgr_signoff.py`).

- **Simulator.** VACASK 0.3.4 with the IHP SG13G2 models of `/opt/ihp/pdk/ihp-sg13g2/libs.tech/vacask`, including
  the self-heating of the HBTs. Each run writes every node voltage to a raw file, and the rules below are checked on
  it. A run that fails is not repeated: every check that depends on it fails.
- **MOS voltage ratings.** |Vgs|, |Vgd|, |Vds| and |Vgb| of every MOS:
  - at every DC operating point: at most 3.63 V for the HV devices (`sg13_hv_nmos`, `sg13_hv_pmos`, the 3.3 V
    rating plus 10 %) and 1.32 V for the LV devices (`sg13_lv_*`, 1.2 V plus 10 %);
  - at every time point of a startup transient, including t = 0: at most 10 % more, 3.99 V and 1.45 V, an allowance
    for the switching overshoot of internal nodes when the supply or enable steps, not for a node held above the
    supply.
- **HBT safe operating area**, at every DC point and every transient time point: |Vbe| at most 1.6 V, |Vbc| at most
  5.1 V (`npn13G2`, `npn13G2l`) or 7.0 V (`npn13G2v`), and |Vce| at most 1.6 V (`npn13G2`, `npn13G2l`) or 2.2 V
  (`npn13G2v`). These are the `vbe_max`, `vbc_max` and `vce_max` parameters of the HBT model cards of the
  IHP-Open-PDK at the same commit (0dedf26, `libs.tech/ngspice/models/sg13g2_hbt_mod.lib`); the VACASK models carry
  no SOA parameters.
- **Junctions.** At every DC operating point no PMOS source-to-well or drain-to-well junction may be forward biased
  by more than 0.4 V. The substrate terminals (NMOS bulk, HBT and resistor `bn`) are tied to `vss` by the
  eligibility check.
- Each limit has a 1 mV tolerance.

There is no stored-energy rule: every graded current is a DC operating point, and every transient starts from the
operating point with the supply at 0 V or the circuit disabled, so stored charge cannot supply a graded quantity.
