# Unity-gain buffer: specification

This file is the complete public contract. SKY130 netlist syntax is in
`/opt/analog-arena/SKY130_NETLIST_GUIDE.md`.

## Interface

`.subckt unity_gain_buffer vss ibias vdd vin vout`. `ibias` is the only
external analog-bias port. Local helper subcircuits are allowed. Grading
scores only the external electrical behavior below; it does not inspect
device names, topology, dimensions, hierarchy or connectivity.

## Conditions, coverage and scored requirements

Design a transistor-level, current-biased unity-gain buffer: the output must be driven by the circuit from its supply, not by the signal source. The testbench provides a 50 uA reference current from `vdd` into `ibias`, a 1 pF capacitor from `vout` to `vss`, and the Sky130 supply rails (`vss` = 0 V, `vdd` = VDD). `vin` is driven by an ideal voltage source with zero source resistance. Input voltages below are relative to the applied supply.

Signoff covers the complete process-voltage-temperature matrix: every combination of the Sky130 corner model sections tt, ss, ff, sf, and fs, supplies VDD = 1.62, 1.80, and 1.98 V, and temperatures -40, 27, and 125 C (45 points). Process variation is represented only by these deterministic corner sections; local mismatch, Monte Carlo variation, and layout parasitics are not modeled. The nominal tt/1.80 V/27 C point is evaluated first; if it fails any requirement, the other 44 points are not run and the reward is 0. At every point, signoff requires:

- DC tracking error `|V(vout) - V(vin)|` no greater than 5 mV at every point of a 121-point DC sweep of `vin` from 0.45 VDD to 0.55 VDD.
- Low-frequency closed-loop transfer `|V(vout)/V(vin)|` of at least 0.95 V/V at 1 kHz and bandwidth of at least 100 MHz. The AC analysis biases `vin` at VDD/2 with a 1 V AC stimulus and sweeps 1 kHz to 10 GHz at 100 points per decade. Bandwidth is the first falling frequency at which the transfer is 3 dB below its 1 kHz magnitude (interpolated by ngspice); a response without such a crossing inside the sweep has no bandwidth measurement and fails.
- Input loading no greater than 0.5 pF, half the load capacitance. In the same AC analysis, the effective input capacitance is `|I(vin)/V(vin)| / (2*pi*100 MHz)`, where `I(vin)` is the small-signal current drawn from the `vin` source at 100 MHz. A path in which the source itself charges the 1 pF load while meeting the bandwidth requirement presents at least about 0.67 pF, so this limit requires the buffer to isolate the source from the load.
- Quiescent DUT power drawn from `vdd` must not exceed 0.5 mW. It is the average `vdd` source current from 10 ns to 19 ns of the transient below, with `vin` at VDD/2, minus the 50 uA reference current, times VDD. This limit excludes the power consumed by the externally supplied 50 uA reference-current branch at `ibias`; only current consumed internally by the DUT is counted.
- Both a +100 mV command from VDD/2 to VDD/2+100 mV and a -100 mV command from VDD/2 to VDD/2-100 mV are tested in one 220 ns transient (10 ps maximum time step). `vin` holds VDD/2 until 19.9 ns, ramps in 100 ps to VDD/2+100 mV at 20 ns, returns to VDD/2 between 79.9 ns and 80 ns (this return is not scored), and ramps to VDD/2-100 mV between 139.9 ns and 140 ns, where it holds until 220 ns. For each direction, the settling time is measured from the end of the command ramp (20 ns or 140 ns) to the last entry into the externally defined +/-5 mV band around the commanded voltage, after which the output must remain inside that band through the end of the window (79.9 ns for the positive step, 220 ns for the negative step). The last entry is resolved at the simulator time points. The settling time must not exceed 10 ns. The final tracking error, the mean of `vout` over 70-79 ns (positive) or 210-220 ns (negative) relative to the commanded voltage, must not exceed 5 mV.
- For the positive step, overshoot of the `vout` maximum over 20-79.9 ns above the commanded final voltage must not exceed 10 mV. For the negative step, undershoot of the `vout` minimum over 140-220 ns below the commanded final voltage must not exceed 10 mV.

Every analysis must complete without simulator errors and print every measurement as a finite number. The reward is 1 only when every requirement above passes at all 45 PVT points; otherwise it is 0.

The public development decks in `/app/testbench` (`tb_tracking_tt.spi`, `tb_ac_tt.spi`, `tb_settling_tt.spi`) are directly runnable nominal tt/1.80 V/27 C examples of DC tracking, AC gain, bandwidth and input loading, and the commanded-target step and quiescent-power checks. Run them from `/app/testbench`, for example `ngspice -b tb_ac_tt.spi`. Signoff runs the same decks with the corner, supply, and temperature replaced for each PVT point.

## Eligibility

Apart from comment lines, `circuit.spi` may contain only `.subckt`/`.ends` lines and `X` instances of the Sky130 PDK subcircuits listed in `/opt/analog-arena/sky130_pdk_subcircuits.txt` or of your local helper subcircuits; ideal `R`, `C`, and `L` elements, switches, independent, controlled, or behavioral sources, raw device cards, and all other directives are rejected by the checker and receive zero reward.

Before simulation the grader runs this check; a failure scores 0:

```bash
check_circuit.py /app/circuit.spi --require-subcircuit unity_gain_buffer vss ibias vdd vin vout --enforce-sky130-geometry
```
