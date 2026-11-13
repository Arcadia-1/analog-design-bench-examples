# Netlist guide: GF180MCU with Xyce

The circuit is simulated with **Xyce 7.10**, not ngspice. The benches load the
GF180MCU models like this; `/app/circuit.spi` must not contain these lines:

```spice
.include "/opt/gf180/pdk/gf180mcuD/libs.tech/xyce/design.xyce"
.lib "/opt/gf180/pdk/gf180mcuD/libs.tech/xyce/sm141064.xyce" typical
.include "/app/circuit.spi"
.options device temp=27
```

The corner sections are `typical`, `ss`, `ff`, `sf` and `fs`. Xyce ignores
`.temp`; temperature is set with `.options device temp=`. Xyce runs one
analysis per netlist.

## What the circuit may contain

Only `.subckt` / `.ends` blocks, and inside them:

| Element | Form | Rules |
|---|---|---|
| MOS | `M<name> d g s b nfet_03v3 w=4u l=0.28u [m=2]` (or `pfet_03v3`) | plain numbers in metres; `w` 0.22u to 100u, `l` 0.28u to 50u; `m` a whole number for parallel devices; `ad`, `as`, `pd`, `ps` optional; no `nf` or other parameters |
| resistor | `R<name> a b 500 [m=2]` | ideal, plain positive value |
| capacitor | `C<name> a b 2p [m=2]` | ideal, plain positive value |
| subcircuit | `X<name> <nodes> <subckt> [m=2]` | a subcircuit defined in the same file, with its pin count; `m` makes that many parallel copies |

The MOS models are binned BSIM4 cards: write the model name `nfet_03v3` or
`pfet_03v3` directly on an `M` line (there are no `X` wrappers in the Xyce
models). NMOS bulks normally go to `vss` and PMOS bulks to `vdd` or their
source. Comments start with `*`; `$` and `;` end a line; `+` continues one.

Example:

```spice
.subckt inv a y vdd vss
MP y a vdd vdd pfet_03v3 w=4u l=0.28u
MN y a vss vss nfet_03v3 w=2u l=0.28u
.ends inv
```

Check a netlist with the grader's own eligibility check:

```bash
python3 /app/signoff/check_circuit.py /app/circuit.spi --require-subcircuit flash_adc_3bit vss vdd vrefn vrefp clk vin b2 b1 b0
```
