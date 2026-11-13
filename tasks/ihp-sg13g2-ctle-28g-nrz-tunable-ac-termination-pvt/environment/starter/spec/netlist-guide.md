# IHP SG13G2 netlist guide

The benches load the PDK libraries (`cornerMOSlv.lib`, `cornerMOShv.lib`, `cornerHBT.lib`, `cornerRES.lib`,
`cornerCAP.lib` under `$IHP_PDK_ROOT/libs.tech/ngspice/models/`); the circuit only instantiates devices. Every PDK
device is a subcircuit, so its instance name starts with `X`. Values take SPICE suffixes (`u` = 1e-6, `n`, `p`,
`f`, `k`, `meg`).

| device | terminals | parameters (others are rejected) |
|---|---|---|
| `sg13_lv_nmos`, `sg13_lv_pmos` (1.2 V) | `d g s b` | `w`, `l` (required), `ng` fingers, `m`; `l` 0.13u-10u, `w/ng` 0.15u-10u |
| `sg13_hv_nmos` (3.3 V) | `d g s b` | as above; `l` 0.45u-10u |
| `sg13_hv_pmos` (3.3 V) | `d g s b` | as above; `l` 0.40u-10u |
| `npn13G2` SiGe HBT | `c b e bn` | `Nx` emitter fingers 1-10 (emitter 0.07 x 0.9 um each), `m` |
| `rsil`, `rppd`, `rhigh` poly resistors | `p n bn` | `w`, `l` (required, at least 0.5u), `m` |
| `cap_cmim` MIM capacitor | `p n` | `w`, `l` (required, 1.14u-75u), `m` |
| `sg13_hv_svaricap` MOS varactor | `g1 w g2 bn` | `w` 3.74u-9.74u, `l` 0.3u-0.8u (required), `nx` fingers, `m` |

`bn` is the substrate: tie it to `vss`. `m` and `ng`/`Nx`/`nx` are whole numbers. Examples:

```spice
XM1 d g s vss sg13_lv_nmos w=20u l=0.13u ng=4
XM2 d g vdd vdd sg13_hv_pmos w=10u l=0.5u ng=2 m=2
XQ1 c b e vss npn13G2 Nx=4
XR1 a b vss rppd w=1u l=5u
XC1 a b cap_cmim w=10u l=10u
XV1 a wctl b vss sg13_hv_svaricap w=9.74u l=0.8u nx=2
```

Notes:

- `npn13G2`: the model card is valid for `Ic` below 3 mA per emitter finger, `Vbe` 0.65-0.96 V and `Vce`
  0.4-2.0 V; its peak transit frequency is near 300 GHz. Self-heating is on.
- `sg13_hv_svaricap` is a differential accumulation-mode varactor: the capacitance between each gate (`g1`, `g2`)
  and the n-well `w` is high when the gate is above the well and low when the well is well above the gate. Between
  `g1` and `g2` the two halves are in series, and `w` sits at the pair's differential virtual ground.
- Ideal `R`, `C`, `L` and `K` are allowed (see `signoff-rules.md`); PDK resistors and MIM capacitors add their
  process corners and parasitics.
- Grading corners: `mos_tt/ss/ff/sf/fs` for MOS and varactors, with `hbt_wcs`, `res_wcs`, `cap_wcs` at `ss`,
  `hbt_bcs`, `res_bcs`, `cap_bcs` at `ff`, and the typical sections otherwise.
