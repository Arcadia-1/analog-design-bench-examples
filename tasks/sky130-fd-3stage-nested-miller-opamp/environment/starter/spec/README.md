# Fully differential three-stage nested-Miller op amp: specification

This file is the complete public contract. SKY130 netlist syntax is in
`/opt/analog-arena/SKY130_NETLIST_GUIDE.md`.

## Interface and operating conditions

- Subcircuit: `.subckt fd3_nmc_opamp vss ibias vdd vcm vinp vinn voutp voutn` (exact name and pin order).
- Supplies: `vss` is 0 V; `vdd` is 1.8 V nominal.
- Bias: an ideal 10 uA current flows from `vdd` into `ibias` at every condition except the bias-tracking test. The design must sink it and derive its bias currents from it.
- Common-mode point VCM = VDD/2 (0.81, 0.9 and 0.99 V at 1.62, 1.8 and 1.98 V).
- `vcm` carries VCM. The design's common-mode feedback must hold the output common mode (`voutp` + `voutn`)/2 at it. Every bench feeds the `vcm` pin from its ideal VCM source through 1 kohm, so current the design draws from `vcm` is measured.
- Load: each output drives 2 pF to ground and 1 Mohm to VCM in every application bench (the transconductance benches return the output through 1 kohm instead).
- Devices: Sky130 1.8 V MOS models (`sky130_fd_pr__nfet_01v8`, `sky130_fd_pr__pfet_01v8` and their `_lvt` variants) within the modeled geometry, plus ideal resistors and capacitors. No sources, behavioral elements or other ideal parts.
- PVT points: the corners ff, ss, fs and sf, each at 1.62 and 1.98 V and at -40 and 125 C (16 points), plus the nominal point (tt, 1.8 V, 27 C). These 17 points are a representative subset (the vertices of the declared supply and temperature ranges at the skew corners), not the complete 45-point product; the operating-range sweeps below cover the interior at tt.
- Critical loads: the 16 PVT points are repeated with a +20 uA and a -20 uA DC current into each output, and with the 1 Mohm load returned to ground instead of VCM (48 more points).

## Scored checks

Each check passes or fails; the reward is the fraction of checks that pass. "Every PVT point" means the 16 PVT points and the nominal point; "every PVT and load point" adds the 48 critical-load points.

| Check | Requirement |
|---|---|
| `complete_signoff` | Every test of the run list below completes at every point it runs at (nominal, 16 PVT points, 48 critical-load points, 30 Monte Carlo runs, both temperature extremes, both range sweeps). |
| `pvt_open_loop_gain` | Open-loop gain >= 70 dB at every PVT and load point. |
| `pvt_gain_bandwidth` | Unity-gain frequency >= 5 MHz at every PVT and load point. |
| `pvt_loop_stability` | Phase margin >= 60 deg, gain margin >= 10 dB and exactly one 0 dB crossing at every PVT and load point. |
| `closed_loop_flatness` | Closed-loop peaking <= 1 dB at the nominal point (gain -1 network). |
| `pvt_slew_rate` | Slew rate per output >= 2 V/us on both edges at every PVT point. |
| `pvt_settling` | Large-step settling to +/-0.1 % <= 1 us on both edges at every PVT point. |
| `input_common_mode_range` | The input common-mode range covers 0.6 to 1.6 V at the nominal point and 0.85 to 0.95 V at every PVT point. |
| `pvt_output_swing` | Differential output swing VODpp >= 2.5 V at every PVT point. |
| `pvt_cm_rejection` | Input-common-mode to output-common-mode gain <= -30 dB at every PVT point. |
| `pvt_cm_supply_rejection` | VDD and VSS rejection at the output common mode >= 25 dB at every PVT point. |
| `pvt_output_common_mode` | Output common-mode error abs(Vocm - VCM) <= 20 mV at every PVT point. |
| `pvt_noise` | White input-referred noise <= 60 nV/sqrt(Hz) at every PVT point. |
| `pvt_distortion` | THD <= -40 dB (1 %) at 10 kHz and 0.5 Vpp per output at every PVT point. |
| `full_power_bandwidth` | Full-power bandwidth >= 300 kHz at the nominal point. |
| `offset` | Mismatch offset sigma <= 5 mV; systematic input-referred abs(VOS) <= 2 mV at every PVT point. |
| `pvt_supply_current` | Supply current <= 450 uA at every PVT point. |
| `pvt_vcm_pin_current` | Current drawn from the `vcm` reference <= 1 uA at every PVT point. |
| `bias_current_tracking` | The supply current follows the bias current with exponent >= 0.7 at the nominal point. |
| `operating_range` | The open-loop criteria below hold continuously from 1.62 to 1.98 V and from -40 to 125 C. |
| `transient_stability` | No oscillation during power-up/power-down or supply surges at -40, 27 and 125 C, and no output phase reversal at the nominal point. |

## How the scored quantities are measured

- **Open loop.** Each input is biased through 1 GH from a differential-only servo (sources acting on Vod around VCM) while 1 GF grounds it for AC, so the DC point is closed-loop and every analysed frequency (1 mHz to 10 GHz) is open-loop. AC +/-0.5 V drives the inputs; the gain is Vod/Vid. Gain is read at 1 mHz, the unity-gain frequency at the first falling 0 dB crossing, phase margin as 180 deg plus the phase there, and gain margin as minus the gain where the phase reaches -180 deg.
- **Gain -1 network.** 100 kohm input and feedback resistors on each side, feedback from `voutn` to `vinp` and from `voutp` to `vinn`. Closed-loop flatness, CMRR, PSRR, distortion, full-power bandwidth, offset and the output common mode are measured in it. Peaking is the largest gain of Vod/Vid above its low-frequency value (1 Hz to 10 GHz).
- **Slew and settling.** Capacitive gain -1: 1 pF input and feedback capacitors on each side, with a differential servo (ideal sources through switches) setting the DC level of the amplifier inputs: at DC, Vod sits at the amplifier's offset and the input common mode at VCM. The switches open well before the first edge. The two input sources step +/-0.5 V in antiphase, so Vod steps 1 V, up and down. Slew rate per output is the steepest slope of Vod divided by 2; settling is the time from the input edge until Vod stays within +/-0.1 % of the step around the commanded level: the level before the edge plus 1 V on the rising edge, and back to that starting level on the falling edge.
- **Input common-mode range.** The inputs sit at a common mode stepped from 0 to VDD in 25 mV steps, with a DC-only differential servo keeping the amplifier open-loop for AC. The range is the contiguous span around VCM where the differential gain at 0.1 Hz stays within 6 dB of its value at VCM and the output common mode within 50 mV of VCM. A step with no operating point counts as outside the range.
- **Output swing.** Gain -2 network (50 kohm inputs, 100 kohm feedback), one input swept from 0 to VDD. VODpp is the Vod span over which the small-signal gain stays at least 90 % of its value at VCM.
- **Common-mode rejection.** 1 V AC common-mode source on both input resistors of the gain -1 network; the input-to-output common-mode gain at 10 mHz.
- **Supply rejection at the output common mode.** Gain -1 network with its inputs at VCM and 1 V AC on `vdd` or `vss`; rejection of the output common mode at 10 mHz.
- **Output common mode.** abs((Vop + Von)/2 - VCM) in the gain -1 network with zero input.
- **Noise.** Capacitive gain -1 network (1 pF) with a differential-only DC servo; noise of Vod referred to one input source (the network's noise gain is about 2). The white level is the minimum density between 10 kHz and the lower of 1 MHz and one tenth of the unity-gain frequency.
- **Distortion.** Gain -1 network, 0.5 Vpp per output (1 Vpp differential) at 10 kHz; THD of Vod from harmonics 2 to 10 over whole periods after two settling periods.
- **Full-power bandwidth.** Gain -1 network at 1 Vpp per output; the highest frequency at which THD stays at or below 1 % (decade steps from the decade below a third of SR/(2 pi A), then six bisection steps).
- **Current from `vcm`.** abs(V(vcm source) - V(vcm pin)) / 1 kohm at the operating point of the gain -1 network with zero input.
- **Offset and supply current.** The input-referred offset is Vod/2 in the gain -1 network. Its systematic value and the supply current are read at every PVT point; the mismatch sigma comes from 30 fixed-seed runs of the Sky130 `tt_mm` mismatch models at the nominal point.
- **Bias tracking.** At the nominal point the offset bench repeats with the bias current at 8 uA and at 12 uA. The exponent is ln(Icc at 12 uA / Icc at 8 uA) / ln(12 / 8): about 1 when the currents mirror `ibias`, about 0 when they are generated any other way.
- **Operating range.** At tt, the supply is swept from 1.62 to 1.98 V in 90 mV steps at 27 C, and the temperature from -40 to 125 C in 27.5 C steps at 1.8 V. A point passes when the open-loop gain is within 6 dB of nominal, the unity-gain frequency is at least half of nominal and the phase margin is at least 45 deg; every point of both sweeps must pass.
- **Transient stability.** VDD ramps from 0 to its nominal value and back in 5 ms and in 10 ms; separately a +/-50 mV square wave with 10 ns edges rides on VDD at 100 kHz, 300 kHz and 1 MHz. The output common mode and Vod must not oscillate (activity above 2 mV around the moving average for more than 20 us in total during a ramp, or ringing that has not decayed before the next supply edge). Phase reversal is checked at the nominal point with a differential input swept across the full input range.
- **Loop stability before the transient tests.** The transient tests (large step, distortion, full-power bandwidth, power-up and supply surge) run at a point when the open-loop phase margin and gain margin measured at that point are both positive. At any other point they are recorded as skipped and their checks fail.

## Run list

- Nominal point: offset, output common mode and supply current, open loop, closed-loop flatness, input common-mode range, output swing, common-mode and supply rejection, noise, large step, THD at 10 kHz, full-power bandwidth, transconductance with the phase-reversal sweep, power-up/power-down, supply surge and bias tracking.
- Each of the 16 PVT points: offset and supply current, open loop, input common-mode range, output swing, common-mode and supply rejection, noise, large step and THD at 10 kHz.
- Each of the 48 critical-load points: the open-loop test.
- 30 mismatch Monte Carlo runs of the offset at the nominal point.
- tt at 1.8 V, -40 and 125 C: offset, open loop, power-up/power-down and supply surge.
- The supply and temperature sweeps of the operating range: the open-loop test.

## Deliverable and eligibility

- Edit `/app/circuit.spi` and implement `.subckt fd3_nmc_opamp vss ibias vdd vcm vinp vinn voutp voutn`: a transistor-level fully differential three-stage op amp with nested Miller compensation on each half and continuous-time common-mode feedback that holds the output common mode at `vcm`. Internal hierarchy, device count, dimensions and connectivity are not scored.
- Before simulation the grader runs this check; a failure scores 0:

  ```bash
  check_circuit.py /app/circuit.spi --allow-ideal R C --enforce-sky130-geometry --require-subcircuit fd3_nmc_opamp vss ibias vdd vcm vinp vinn voutp voutn
  ```

## Public benches

From `/app`, `ngspice -b testbench/<bench>.spi` runs each at the nominal point (tt, 1.8 V, 27 C) with the same networks, loads and definitions as grading; they do not cover the PVT, load, Monte Carlo or range points:

- `tb_op_tt.spi`: offset, output common mode, supply current, current from `vcm`, bias tracking; with the `tt_mm` library and `.option seed=` it gives one mismatch sample;
- `tb_openloop_ac_tt.spi`: open-loop gain, unity-gain frequency, phase margin; at other supplies and temperatures it is the operating-range test;
- `tb_closedloop_tt.spi`: closed-loop peaking;
- `tb_step_tt.spi`: 10-90 % slew and 0.1 % settling of both edges;
- `tb_icmr_tt.spi`: gain and output common-mode error at each input common-mode step;
- `tb_swing_tt.spi`: differential output swing;
- `tb_rejection_tt.spi`: input-CM to output-CM gain and supply rejection at the output common mode;
- `tb_noise_tt.spi`: input-referred noise density;
- `tb_thd_tt.spi`: THD at 10 kHz and 0.5 Vpp per output; with `AMP=0.5` and a higher `FREQ`, the full-power bandwidth;
- `tb_powerup_tt.spi`: output activity after a 5 ms supply ramp.
