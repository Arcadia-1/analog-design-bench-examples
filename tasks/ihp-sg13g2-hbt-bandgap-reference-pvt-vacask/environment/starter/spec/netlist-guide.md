# Netlist guide: IHP SG13G2 with VACASK

The circuit is simulated with **VACASK 0.3.4**, whose netlist language is not
SPICE: it is closer to Spectre. `/app/circuit.sim` holds only subcircuit
definitions; the benches load the models and include it like this, and the
circuit must not contain these lines:

```text
include "sg13g2_vacask_common.lib"
include "cornerMOShv.lib" section=mos_tt
include "cornerMOSlv.lib" section=mos_tt
include "cornerHBT.lib" section=hbt_typ
include "cornerRES.lib" section=res_typ
include "cornerCAP.lib" section=cap_typ
include "circuit.sim"
```

VACASK finds these files through `.vacaskrc.toml` (a copy of
`$IHP_PDK_ROOT/libs.tech/vacask/.vacaskrc.toml`), which must be in the folder
VACASK starts in or next to the bench; `/app` and `/app/testbench` have one.

## Syntax

- One statement per line. A line ending in `\` continues on the next; a line
  break inside `( )` is also ignored.
- Comments: `//` to the end of the line, or `/* ... */`.
- Names are case-sensitive and start with a letter or `_`.
- A subcircuit:

  ```text
  subckt name (port1 port2 ...)
    instance lines
  ends
  ```

- An instance: `name (node1 node2 ...) master param=value ...`. The node list
  is in parentheses and follows the master's terminal order.
- Values are plain numbers with an optional SI prefix and no unit:
  `a f p n u m k M G T`, or `meg`; nothing else is accepted (no `K`, `x`,
  `mil` or units). **`M` is mega and `m` is milli** (`1M` = 1e6, `1m` =
  1e-3); `2u` is 2e-6. Write `2u`, not `2um`.
- Node `0` (global ground) may not be used: connect to the `vss` port.

## What the circuit may contain

Only `subckt` ... `ends` blocks, not nested, and inside them instances of the
cells below or of subcircuits defined in the same file. Parameters, `$`
names, expressions, `parameters`, `model`, `include`, `load`, `@if`,
`global`, `ground`, `control` and `embed` statements are not allowed.

| Cell | Terminals | Parameters (all others are refused) |
|---|---|---|
| `sg13_hv_nmos` | `(d g s b)` | `w` 0.3u to 1000u and `l` 0.45u to 100u (both required), `ng`, `m`; `b` must be `vss` |
| `sg13_hv_pmos` | `(d g s b)` | `w` 0.3u to 1000u and `l` 0.4u to 100u (both required), `ng`, `m`; `b` is its n-well: `vdd`, its source, or another node |
| `sg13_lv_nmos` | `(d g s b)` | `w` 0.15u to 1000u and `l` 0.13u to 100u (both required), `ng`, `m`; `b` must be `vss` |
| `sg13_lv_pmos` | `(d g s b)` | `w` 0.15u to 1000u and `l` 0.13u to 100u (both required), `ng`, `m` |
| `npn13G2` | `(c b e bn)` | `nx` emitters, 1 to 10 (emitter 0.07 µm × 0.9 µm each) |
| `npn13G2l` | `(c b e bn)` | `nx` 1 to 4, `le` emitter length 1u to 2.5u |
| `npn13G2v` | `(c b e bn)` | `nx` 1 to 4, `le` 1u to 5u (high-voltage HBT) |
| `rppd` | `(1 2 bn)` | `w` 0.5u to 1000u and `l` 0.5u to 10000u (both required), `b` bends, `m`; about 260 Ω/square, low temperature coefficient |
| `rsil` | `(1 2 bn)` | `w` 0.5u to 1000u and `l` 0.5u to 10000u (both required), `m`; about 7 Ω/square |
| `rhigh` | `(1 2 bn)` | `w` 0.5u to 1000u and `l` 0.5u to 10000u (both required), `b` bends, `m`; about 1360 Ω/square, temperature coefficient about −2300 ppm/C |
| `cap_cmim` | `(plus minus)` | `w` and `l` 1.14u to 100u (both required), `m`; about 1.5 fF/µm² |

Whole-number parameters and their ranges: `ng` 1 to 1000, `m` 1 to 10000, `b` 0 to 10000, `nx` as above.

`bn` is the substrate terminal of the HBTs and resistors and must be `vss`.
HBTs have no `m`: place several instances, or use `nx`. The HBT models include
self-heating. The HV MOS devices are the 3.3 V devices; the LV MOS devices
are 1.2 V devices (see the ratings in [`signoff-rules.md`](signoff-rules.md)).

Example:

```text
// a diode-connected HBT pair and a current mirror
subckt mirror (in out vdd)
  xp1 (in in vdd vdd) sg13_hv_pmos w=4u l=2u
  xp2 (out in vdd vdd) sg13_hv_pmos w=4u l=2u m=2
ends

subckt bandgap (vdd vss en vref)
  xm (n1 n2 vdd) mirror
  q1 (n1 n1 vss vss) npn13G2 nx=1
  r1 (n2 vref vss) rppd w=0.5u l=40u
  ...
ends
```

Hierarchical node names in raw files join the instance path with `:`; in the
benches the circuit is instance `xdut`, so node `n1` above is `xdut:n1`.

The grader runs this eligibility check before any simulation; a failure scores 0:

```bash
python3 /app/signoff/check_circuit.py /app/circuit.sim --require-subcircuit bandgap vdd vss en vref
```

## VACASK bench cheat sheet

Benches (not the circuit) use these; the public benches in `/app/testbench` are complete examples.

```text
model vsource vsource                      // sources need a model line
vdd (vdd 0) vsource dc=3.3 mag=1           // DC value, AC excitation
vin (in 0) vsource type="pulse" val0=0 val1=3.3 rise=1u delay=1u
cl (vref 0) sg13g2_default_mod_c c=10p     // ideal C/R for benches only
control
  var vsup=3.3                             // circuit variable, usable in instance values
  options temp=27                          // temperature
  save v(vref) i(vdd)                      // what to write (default: all nodes and source currents)
  sweep temp option="temp" values=[-40, 27, 125]
  sweep vsup variable="vsup" from=3.0 to=3.6 step=0.3
    analysis dc1 op                        // one raw file per analysis: dc1.raw
  analysis ac1 ac from=1 to=1meg mode="dec" points=10
  analysis nz noise out="vref" in="vdd" values=[10k]
  alter instance("vdd") type="pulse" val0=0 val1=3.3 rise=1u delay=1u
  analysis tr tran stop=40u step=10n maxstep=50n icmode="op"
endc
```

Sweeps written before an analysis nest (the last is the innermost); `nodeset={"vref", 0}` on an `op` gives an
initial guess; `clear saves` resets `save`. Run `vacask bench.sim` in the bench's folder.

To read a raw file in Python, `bgr_signoff.read_raw` in `/app/signoff` returns
the vector names and data; VACASK also ships `rawfile.py` in
`/usr/lib/vacask/python`.
