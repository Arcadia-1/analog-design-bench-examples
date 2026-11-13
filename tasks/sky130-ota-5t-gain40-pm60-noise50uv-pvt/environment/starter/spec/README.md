# Five-transistor OTA: specification

This folder is the complete public contract. Rules are stated once: the
electrical contract here, the simulation rules in
[`signoff-rules.md`](signoff-rules.md), and the SKY130 netlist syntax in
`/opt/analog-arena/SKY130_NETLIST_GUIDE.md`.

## Interface

`.subckt ota_5t vss ibias vdd vinn vinp vout`, with exactly that pin order.
`vdd` and `vss` are the supply rails, `vinp` and `vinn` the non-inverting and
inverting inputs, `vout` the single-ended output. `ibias` is the only external
bias port: the bench drives a constant reference current into it from `vdd`.
That current comes from the measured supply, so it counts toward power. The
input sources are measured too: power the circuit takes from them counts
toward the same limit.

The design is a five-transistor OTA: a differential input pair, an active
current-mirror load, and a tail current source mirrored from `ibias`. Grading
scores only the external electrical behavior below; it does not count devices
or inspect names, dimensions, hierarchy or connectivity.

## Operating conditions

- Supply `vdd` from 1.62 V to 1.98 V; temperature from -40 C to 125 C; Sky130
  corners `tt`, `ss` and `ff`.
- Reference current into `ibias`: any constant value from 45 uA to 55 uA.
- Input common-mode voltage: `vdd/2`.
- Load: 1 pF from `vout` to `vss`.

## Coverage

Gain, bandwidth, phase margin, noise and power are graded on a grid that
includes the corners of the supply and temperature ranges and interior
temperatures, at all three process corners, at reference currents inside the
45–55 uA range. Common-mode and supply rejection are graded at representative
points of the same ranges. Mismatch, slew, large-signal settling and other
loads are not graded.

Grading starts with `tt`/1.80 V/27 C at 50 uA. If that point misses the gain
or bandwidth limit, every check fails and nothing else runs. If any grid point
misses a grid limit, the rejection checks fail without running.

## Scored checks

Each check passes or fails; the reward is the fraction that pass. Every limit
applies at every graded point.

| Check | Requirement |
|---|---|
| `gain_40db` | Loop gain at 1 kHz at least 40 dB |
| `ugb_100mhz` | Loop unity-gain frequency at least 100 MHz |
| `phase_margin_60deg` | Phase margin at that frequency at least 60 degrees |
| `power_0p5mw` | Power from the supply (including the `ibias` reference current) and from the input source, at most 0.5 mW |
| `noise_50uvrms` | Input-referred noise integrated from 10 Hz to 10 MHz at most 50 uVrms |
| `cmrr_40db` | Open-loop common-mode rejection at 1 kHz at least 40 dB |
| `psrr_30db` | Open-loop rejection of each supply rail at 1 kHz at least 30 dB |

Measurement definitions:

- **Loop gain, UGB and phase margin.** `vout` is tied to `vinn` at DC and the
  loop is broken at that connection for a Middlebrook double-injection
  measurement (`T = (Tv·Ti − 1)/(Tv + Ti + 2)`), swept from 10 Hz to 10 GHz at
  100 points per decade. UGB is the first falling 0 dB crossing; phase margin
  is 180 degrees plus the loop phase there. A loop without a crossing fails.
- **Power** is `vdd` times the `vdd` current plus the `vinp` source voltage
  times its current, at the unity-feedback operating point. A legal input
  draws no DC current, so the second term is zero.
- **Noise** is the input-referred noise of the unity-feedback circuit at
  `vout`, integrated from 10 Hz to 10 MHz.
- **CMRR** is the open-loop differential gain over the common-mode gain at
  1 kHz. Each measured copy has `vinp` at `vdd/2` and `vout` tied to `vinn`
  through a 1 GH inductor, so its DC operating point is the unity-feedback one
  while the loop is open at 1 kHz; the AC drive reaches `vinn` through a 1 F
  capacitor.
- **PSRR** is the open-loop differential gain over the gain from an AC source
  on `vdd` (positive rail) or on `vss` (negative rail) to `vout`, at 1 kHz,
  with the operating point set the same way.

## Deliverable and eligibility

- Edit `/app/circuit.spi`. Use official Sky130 PDK subcircuits; parameter-free
  local hierarchy is allowed. Ideal passives, independent or controlled
  sources, behavioral devices, ideal switches and XSPICE devices are not
  allowed. Keep models, supplies, bias and load out of the circuit.
- Before simulation the grader runs the check below; a failure scores 0.

  ```bash
  check_circuit.py /app/circuit.spi --require-subcircuit ota_5t vss ibias vdd vinn vinp vout
  ```

## Public benches

From `/app`, `ngspice -b testbench/tb_op_ac_noise_tt.spi`,
`testbench/tb_cmrr_tt.spi`, `testbench/tb_psrr_plus_tt.spi` and
`testbench/tb_psrr_minus_tt.spi` run every measurement at `tt`/1.80 V/27 C with
50 uA, using the same circuit and metric definitions as grading. Grading runs
them at other conditions inside the ranges above. The public benches do not
apply the signoff rules.
