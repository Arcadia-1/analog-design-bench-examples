#!/usr/bin/env python3
"""Eligibility check for a GF180MCU 3.3 V netlist simulated with Xyce.

    check_circuit.py circuit.spi [--require-subcircuit NAME PIN ...]

The netlist may hold only `.subckt` / `.ends` blocks. Inside them:

- `M<name> d g s b nfet_03v3|pfet_03v3 w=<W> l=<L> [m=<K>]`, with plain numeric values in metres
  (`w=4u l=0.28u`): 0.22u <= w <= 100u, 0.28u <= l <= 50u, K a whole number >= 1. Use `m` for parallel devices;
  `nf`, the diffusion geometry (`ad`, `as`, `pd`, `ps`: signoff sets it from `w`) and every other instance parameter
  are refused.
- `R<name> a b <value> [m=<K>]` and `C<name> a b <value> [m=<K>]`: ideal elements with a plain positive value.
- `X<name> <nodes> <subckt> [m=<K>]`: an instance of a subcircuit defined in the same file, with its pin count.

No sources, switches, behavioral or controlled elements, inductors, models, parameters, includes, options or analyses.
Every subcircuit must contain at least one element, none may instantiate itself, and the required subcircuit must exist
with exactly the given name and pin order. Names (elements, nodes, subcircuits) use letters, digits and `_ . - [ ] < >`.
Exit code 0 when the netlist passes, 1 otherwise. Standard library only.
"""
from __future__ import annotations

import argparse
import math
import re
import sys
from pathlib import Path

MOS_MODELS = ("nfet_03v3", "pfet_03v3")
MOS_PARAMS = {"w", "l", "m"}
W_RANGE = (0.22e-6, 100e-6)
L_RANGE = (0.28e-6, 50e-6)
SCALE = {"meg": 1e6, "mil": 25.4e-6, "t": 1e12, "g": 1e9, "k": 1e3, "m": 1e-3, "u": 1e-6, "n": 1e-9, "p": 1e-12,
         "f": 1e-15}
NUMBER_RE = re.compile(r"^([+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:e[+-]?\d+)?)([a-z]*)$", re.I)
NAME_RE = re.compile(r"^[A-Za-z0-9_.\-\[\]<>]+$")


def spice_number(token: str) -> float | None:
    """A plain SPICE literal such as 4u, 0.28u, 2p or 1e-9; None for anything else (expressions included)."""
    m = NUMBER_RE.match(token.strip())
    if not m:
        return None
    suffix = m.group(2).lower()
    scale = 1.0
    if suffix:
        prefix = next((p for p in SCALE if suffix.startswith(p)), None)
        if prefix is None:
            return None
        scale = SCALE[prefix]
    value = float(m.group(1)) * scale
    return value if math.isfinite(value) else None


def logical_lines(text: str) -> list[tuple[int, str]]:
    """(line number, text) with comments removed; `$` and `;` end a line, `+` continues the previous one."""
    out: list[tuple[int, str]] = []
    for number, raw in enumerate(text.splitlines(), 1):
        line = re.split(r"[$;]", raw, maxsplit=1)[0].strip()
        if not line or line[0] == "*":
            continue
        if line[0] == "+":
            if out:
                out[-1] = (out[-1][0], out[-1][1] + " " + line[1:].strip())
            else:
                out.append((number, line))
        else:
            out.append((number, line))
    return out


def whole(value: str) -> bool:
    k = spice_number(value)
    return k is not None and k >= 1 and k == int(k)


def violations(text: str, required: list[str] | None = None) -> list[str]:
    lines = logical_lines(text)
    found: list[str] = []
    pins: dict[str, list[str]] = {}
    for _, line in lines:
        words = line.split()
        if words[0].lower() == ".subckt" and len(words) >= 2:
            pins[words[1].lower()] = [w.lower() for w in words[2:]]
    elements: dict[str, int] = {name: 0 for name in pins}
    children: dict[str, set[str]] = {name: set() for name in pins}
    current = None
    seen: set[str] = set()
    names: set[str] = set()
    for number, line in lines:
        words = line.split()
        head = words[0].lower()
        where = f"line {number}"
        if head.startswith("."):
            if head == ".subckt":
                if current is not None:
                    found.append(f"{where}: nested .subckt")
                elif len(words) < 2 or words[1].lower() in seen:
                    found.append(f"{where}: malformed or duplicate .subckt")
                else:
                    current = words[1].lower()
                    seen.add(current)
                    names = set()
                    bad = [w for w in words[2:] if "=" in w or w.lower() == "params:" or not NAME_RE.match(w)]
                    if bad:
                        found.append(f"{where}: .subckt {current} takes pin names only, not {' '.join(bad)}")
            elif head == ".ends":
                if current is None:
                    found.append(f"{where}: .ends outside a subcircuit")
                current = None
            else:
                found.append(f"{where}: directive {head} is not allowed (only .subckt and .ends)")
            continue
        if current is None:
            found.append(f"{where}: element outside a subcircuit: {line[:60]}")
            continue
        if head in names:
            found.append(f"{where}: duplicate element name {words[0]} in {current}")
        bad_names = [w for w in words if "=" not in w and not NAME_RE.match(w)]
        if bad_names:
            found.append(f"{where}: names may use letters, digits and _ . - [ ] < > only, not {' '.join(bad_names)}")
        names.add(head)
        kind = head[0]
        params = {}
        positional = []
        for w in words[1:]:
            if "=" in w:
                key, _, value = w.partition("=")
                params[key.lower()] = value
            else:
                if params:
                    found.append(f"{where}: positional word {w} after a parameter")
                positional.append(w)
        if any(w.lower() == "params:" for w in words) or any("{" in w or "'" in w for w in words):
            found.append(f"{where}: expressions and params: are not allowed")
            continue
        if "m" in params and not whole(params["m"]):
            found.append(f"{where}: m={params['m']} must be a whole number of at least 1")
        if kind == "m":
            if len(positional) != 5 or positional[4].lower() not in MOS_MODELS:
                found.append(f"{where}: a MOS is `M<name> d g s b nfet_03v3|pfet_03v3 w=.. l=..`")
                continue
            extra = sorted(set(params) - MOS_PARAMS)
            if extra:
                found.append(f"{where}: {positional[4]} takes w, l and m only, not {', '.join(extra)}"
                             + (" (use m for parallel devices)" if "nf" in extra else "")
                             + (" (signoff sets the diffusion geometry from w)" if set(extra) & {"ad", "as", "pd", "ps"} else ""))
            w, l = spice_number(params.get("w", "")), spice_number(params.get("l", ""))
            if w is None or l is None:
                found.append(f"{where}: {positional[4]} needs plain numeric w and l in metres (e.g. w=4u l=0.28u)")
            elif not (W_RANGE[0] * (1 - 1e-9) <= w <= W_RANGE[1] * (1 + 1e-9)
                      and L_RANGE[0] * (1 - 1e-9) <= l <= L_RANGE[1] * (1 + 1e-9)):
                found.append(f"{where}: {positional[4]} w={params['w']} l={params['l']}; w must be 0.22u to 100u "
                             "and l 0.28u to 50u")
            elements[current] += 1
        elif kind in "rc":
            value = spice_number(positional[2]) if len(positional) == 3 else None
            if value is None or value <= 0:
                found.append(f"{where}: an ideal {kind.upper()} is `{kind.upper()}<name> a b <plain positive value>`")
            extra = sorted(set(params) - {"m"})
            if extra:
                found.append(f"{where}: an ideal element takes only its value and m=, not {', '.join(extra)}")
            elements[current] += 1
        elif kind == "x":
            if not positional:
                found.append(f"{where}: malformed instance")
                continue
            target = positional[-1].lower()
            extra = sorted(set(params) - {"m"})
            if extra:
                found.append(f"{where}: a subcircuit instance takes only m=, not {', '.join(extra)}")
            if target not in pins:
                found.append(f"{where}: unknown subcircuit {target} (only subcircuits defined in this file)")
            elif len(positional) - 1 != len(pins[target]):
                found.append(f"{where}: {target} has {len(pins[target])} pins, instance gives {len(positional) - 1}")
            else:
                children[current].add(target)
                elements[current] += 1
        else:
            found.append(f"{where}: element {words[0]} is not allowed (only M, R, C and X)")
    if current is not None:
        found.append(f"subcircuit {current} is missing .ends")
    for name, count in elements.items():
        if count == 0:
            found.append(f"subcircuit {name} has no element")

    def cyclic(name: str, stack: tuple[str, ...]) -> bool:
        return name in stack or any(cyclic(c, stack + (name,)) for c in children.get(name, ()))

    for name in pins:
        if cyclic(name, ()):
            found.append(f"subcircuit {name} instantiates itself")
            break
    if required:
        name, want = required[0].lower(), [p.lower() for p in required[1:]]
        if name not in pins:
            found.append(f"required subcircuit {name} is missing")
        elif pins[name] != want:
            found.append(f"subcircuit {name} pins are {' '.join(pins[name])}; required {' '.join(want)}")
    return found


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("circuit", type=Path)
    parser.add_argument("--require-subcircuit", nargs="+", metavar=("NAME", "PIN"))
    args = parser.parse_args()
    try:
        text = args.circuit.read_text(errors="replace")
    except OSError as error:
        print(f"{args.circuit}: cannot read: {error}")
        return 1
    found = violations(text, args.require_subcircuit)
    if found:
        print(f"{args.circuit}: netlist check failed:")
        for reason in found:
            print(f"  {reason}")
        return 1
    print(f"{args.circuit}: netlist check passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
