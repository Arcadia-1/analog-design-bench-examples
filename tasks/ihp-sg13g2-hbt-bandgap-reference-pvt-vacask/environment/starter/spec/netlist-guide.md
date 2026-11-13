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
  `a f p n u m k M G T`, plus `meg`. **`M` is mega and `m` is milli** (`1M`
  = 1e6, `1m` = 1e-3); `2u` is 2e-6. Write `2u`, not `2um`.
- Node `0` (global ground) may not be used: connect to the `vss` port.

## What the circuit may contain

Only `subckt` ... `ends` blocks, not nested, and inside them instances of the
cells below or of subcircuits defined in the same file. Parameters, `$`
names, expressions, `parameters`, `model`, `include`, `load`, `@if`,
`global`, `ground`, `control` and `embed` statements are not allowed.

| Cell | Terminals | Parameters (all others are refused) |
|---|---|---|
| `sg13_hv_nmos` | `(d g s b)` | `w` ≥ 0.3u, `l` ≥ 0.45u (both required), `ng`, `m` (whole numbers); `b` must be `vss` |
| `sg13_hv_pmos` | `(d g s b)` | `w` ≥ 0.3u, `l` ≥ 0.4u, `ng`, `m`; `b` is its n-well: `vdd`, its source, or another node |
| `sg13_lv_nmos` | `(d g s b)` | `w` ≥ 0.15u, `l` ≥ 0.13u, `ng`, `m`; `b` must be `vss` |
| `sg13_lv_pmos` | `(d g s b)` | `w` ≥ 0.15u, `l` ≥ 0.13u, `ng`, `m` |
| `npn13G2` | `(c b e bn)` | `nx` emitters, 1 to 10 (emitter 0.07 µm × 0.9 µm each) |
| `npn13G2l` | `(c b e bn)` | `nx` 1 to 4, `le` emitter length 1u to 2.5u |
| `npn13G2v` | `(c b e bn)` | `nx` 1 to 4, `le` 1u to 5u (high-voltage HBT) |
| `rppd` | `(1 2 bn)` | `w` ≥ 0.5u, `l` ≥ 0.5u (required), `b` bends, `m`; about 260 Ω/square, low temperature coefficient |
| `rsil` | `(1 2 bn)` | `w`, `l`, `m`; about 7 Ω/square |
| `rhigh` | `(1 2 bn)` | `w`, `l`, `b`, `m`; about 1360 Ω/square, temperature coefficient about −2300 ppm/C |
| `cap_cmim` | `(plus minus)` | `w`, `l` 1.14u to 100u (required), `m`; about 1.5 fF/µm² |

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

Check a netlist with the grader's own eligibility check:

```bash
python3 /app/signoff/check_circuit.py /app/circuit.sim --require-subcircuit bandgap vdd vss en vref
```

To read a raw file in Python, `bgr_signoff.read_raw` in `/app/signoff` returns
the vector names and data; VACASK also ships `rawfile.py` in
`/usr/lib/vacask/python`.
