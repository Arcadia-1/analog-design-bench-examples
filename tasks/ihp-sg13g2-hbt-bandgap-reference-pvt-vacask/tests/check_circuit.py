#!/usr/bin/env python3
"""Eligibility check for an IHP SG13G2 VACASK netlist (the subset in spec/netlist-guide.md).

Usage: check_circuit.py circuit.sim --require-subcircuit NAME PORT...

Exit code 0 and "OK" when the file is eligible; otherwise one "ERROR:" line per
problem and exit code 1. The parser is shared with the signoff module, which
flattens the design to map every PDK device terminal to a simulator node.
"""
import argparse
import math
import re
import sys

# name -> (terminals, {parameter: (minimum, maximum, integer)})
HV = {"w": (0.3e-6, 1e-3, False), "ng": (1, 1000, True), "m": (1, 10000, True)}
LV = {"w": (0.15e-6, 1e-3, False), "l": (0.13e-6, 100e-6, False), "ng": (1, 1000, True), "m": (1, 10000, True)}
RES = {"w": (0.5e-6, 1e-3, False), "l": (0.5e-6, 10e-3, False), "b": (0, 10000, True), "m": (1, 10000, True)}
DEVICES = {
    "sg13_hv_nmos": (("d", "g", "s", "b"), {**HV, "l": (0.45e-6, 100e-6, False)}),
    "sg13_hv_pmos": (("d", "g", "s", "b"), {**HV, "l": (0.4e-6, 100e-6, False)}),
    "sg13_lv_nmos": (("d", "g", "s", "b"), LV),
    "sg13_lv_pmos": (("d", "g", "s", "b"), LV),
    "npn13G2": (("c", "b", "e", "bn"), {"nx": (1, 10, True)}),
    "npn13G2l": (("c", "b", "e", "bn"), {"nx": (1, 4, True), "le": (1e-6, 2.5e-6, False)}),
    "npn13G2v": (("c", "b", "e", "bn"), {"nx": (1, 4, True), "le": (1e-6, 5e-6, False)}),
    "rppd": (("1", "2", "bn"), RES),
    "rsil": (("1", "2", "bn"), {k: v for k, v in RES.items() if k != "b"}),
    "rhigh": (("1", "2", "bn"), RES),
    "cap_cmim": (("plus", "minus"), {"w": (1.14e-6, 100e-6, False), "l": (1.14e-6, 100e-6, False),
                                     "m": (1, 10000, True)}),
}
# Terminals that must reach the vss pin: the p-substrate under every device.
SUBSTRATE = {"npn13G2": "bn", "npn13G2l": "bn", "npn13G2v": "bn", "rppd": "bn", "rsil": "bn",
             "rhigh": "bn", "sg13_hv_nmos": "b", "sg13_lv_nmos": "b"}
PREFIX = {"a": 1e-18, "f": 1e-15, "p": 1e-12, "n": 1e-9, "u": 1e-6, "m": 1e-3, "k": 1e3, "M": 1e6, "G": 1e9,
          "T": 1e12}
IDENT = r"[A-Za-z_][A-Za-z0-9_]*"
NUMBER = re.compile(r"^([0-9]+\.?[0-9]*|\.[0-9]+)(?:([eE][+-]?[0-9]+)|(meg|[afpnumkMGT]))?$")
RESERVED = {"include", "section", "endsection", "load", "model", "global", "ground", "subckt", "ends",
            "parameters", "control", "endc", "analysis", "sweep", "embed", "save"}
MAX_DEPTH = 8
# Names the IHP SG13G2 VACASK libraries, VACASK's builtins and the loaded device modules already define;
# a subckt of the design may not reuse them.
LIBRARY_NAMES = set("""
Rparasitic bondpad cap_cmim cap_cmomf cap_cmomf_mod cap_cmomi cap_cmomi_mod cap_rfcmim cmim_core cparasitic
dantenna darea dcorner ddnwpsub dincaparea_mod dinner_mod dinneredge_mod dinrevh_mod dinrevl_mod dinrevlside_mod
diodevdd_2kv diodevdd_4kv diodevdd_mod diodevss_2kv diodevss_4kv diodevss_mod dmain_mod doutcaparea_mod
doutcapcorner_mod dpantenna dparea dpcorner dperim dpperim dpwdnw drev_mod dsub_2kv_mod dsub_4kv_mod dsub_mod
dsubedge0_mod dsubedgexds_mod dsubw isolbox mod1_pnp nmoscl_2 nmoscl_4 nmoscl_psp nmoscl_psp_rf npn13G2 npn13G2_5t
npn13G2_NX_vbic npn13G2l npn13G2l_5t npn13G2l_NX_vbic npn13G2v npn13G2v_5t npn13G2v_NX_vbic ntap1 pnpMPA
pnpMPA_mod ptap1 res_rppd rhigh rmod_rhigh rmod_rsil rppd rsil schottky_nbl1 sg13_hv_nmos sg13_hv_pmos
sg13_hv_svaricap sg13_lv_nmos sg13_lv_pmos sg13g2_default_mod_c sg13g2_default_mod_l sg13g2_default_mod_r
sg13g2_hv_nmos_psp sg13g2_hv_nmos_psp_rf sg13g2_hv_pmos_psp sg13g2_hv_pmos_psp_rf sg13g2_lv_nmos_psp
sg13g2_lv_nmos_psp_rf sg13g2_lv_pmos_psp sg13g2_lv_pmos_psp_rf
vsource isource vcvs vccs ccvs cccs mutual
sp_resistor sp_capacitor sp_inductor sp_diode sp_bjt vbic13_5t r3_cmc psp103va psp103 psp103_nqs mosvar
""".split())
# Total MIM capacitor area (sum of w * l * m over every cap_cmim, counting hierarchy), about 200 pF.
MIM_AREA_MAX = 133000e-12


def parse_value(text):
    """Return the value of a plain numeric literal with an optional SI prefix, or None."""
    match = NUMBER.match(text)
    if not match:
        return None
    value = float(match.group(1) + (match.group(2) or ""))
    prefix = match.group(3)  # an exponent and an SI prefix together are refused, as VACASK refuses them
    if prefix == "meg":
        value *= 1e6
    elif prefix:
        value *= PREFIX[prefix]
    return value


def logical_lines(text):
    """Strip comments and join continued lines; yield (line number, statement)."""
    text = re.sub(r"/\*.*?\*/", lambda m: "\n" * m.group(0).count("\n"), text, flags=re.S)
    pending, start = "", 0
    for number, raw in enumerate(text.splitlines(), 1):
        line = raw.split("//", 1)[0].rstrip()
        if not pending:
            start = number
        if line.endswith("\\"):
            pending += line[:-1] + " "
            continue
        pending += line
        if pending.count("(") > pending.count(")"):
            pending += " "
            continue
        if pending.strip():
            yield start, pending.strip()
        pending = ""
    if pending.strip():
        yield start, pending.strip()


def parse(text):
    """Parse the netlist. Return (subcircuits, errors).

    subcircuits: {name: {"ports": [...], "line": n, "instances": [(name, nodes, master, params, line)]}}
    """
    subckts, errors, current = {}, [], None
    for number, statement in logical_lines(text):
        if "$" in statement or "@" in statement or '"' in statement or "'" in statement:
            errors.append(f"line {number}: '$', '@' and quotes are not allowed: {statement}")
            continue
        head = statement.split()[0]
        if head == "subckt":
            if current is not None:
                errors.append(f"line {number}: nested subckt definitions are not allowed")
                continue
            match = re.match(rf"^subckt\s+({IDENT})\s*(?:\(([^()]*)\)|((?:\s+{IDENT})*))\s*$", statement)
            if not match:
                errors.append(f"line {number}: malformed subckt line: {statement}")
                continue
            name = match.group(1)
            ports = (match.group(2) if match.group(2) is not None else match.group(3) or "").split()
            if name in subckts or name in LIBRARY_NAMES:
                errors.append(f"line {number}: subckt {name} is defined twice or reuses a name of the PDK "
                              f"libraries or the simulator")
            bad = [p for p in ports if not re.fullmatch(IDENT, p) or p in RESERVED]
            if bad or len(set(ports)) != len(ports):
                errors.append(f"line {number}: invalid or repeated port names in subckt {name}")
            current = {"ports": ports, "line": number, "instances": []}
            subckts[name] = current
            continue
        if head == "ends":
            if current is None or not re.fullmatch(rf"ends(\s+{IDENT})?", statement):
                errors.append(f"line {number}: unexpected ends")
            current = None
            continue
        if head in RESERVED:
            errors.append(f"line {number}: '{head}' statements are not allowed")
            continue
        if current is None:
            errors.append(f"line {number}: only subckt definitions may appear at the top level: {statement}")
            continue
        match = re.match(rf"^({IDENT})\s*\(([^()]*)\)\s*({IDENT})((?:\s+{IDENT}\s*=\s*\S+)*)\s*$", statement)
        if not match:
            errors.append(f"line {number}: malformed instance: {statement}")
            continue
        name, nodes, master = match.group(1), match.group(2).split(), match.group(3)
        params, seen = {}, set()
        for key, value in re.findall(rf"({IDENT})\s*=\s*(\S+)", match.group(4)):
            if key in seen:
                errors.append(f"line {number}: parameter {key} given twice on {name}")
            seen.add(key)
            parsed = parse_value(value)
            if parsed is None or not math.isfinite(parsed):
                errors.append(f"line {number}: {name}: {key}={value} is not a plain number with an optional SI prefix")
                continue
            params[key] = parsed
        bad = [n for n in nodes if not re.fullmatch(IDENT, n) or n in RESERVED]
        if bad:
            errors.append(f"line {number}: {name}: invalid node names {bad} (node 0 and quoted names are not allowed)")
        if any(name == inst[0] for inst in current["instances"]):
            errors.append(f"line {number}: instance name {name} is used twice in one subckt")
        current["instances"].append((name, nodes, master, params, number))
    if current is not None:
        errors.append("a subckt is missing its ends")
    return subckts, errors


def check_devices(subckts):
    errors = []
    for sname, sub in subckts.items():
        for name, nodes, master, params, number in sub["instances"]:
            where = f"line {number}: {sname}.{name}"
            if master in DEVICES:
                terminals, allowed = DEVICES[master]
                if len(nodes) != len(terminals):
                    errors.append(f"{where}: {master} needs {len(terminals)} nodes ({' '.join(terminals)})")
                for key, value in params.items():
                    if key not in allowed:
                        errors.append(f"{where}: parameter {key} is not allowed on {master} (allowed: {', '.join(allowed)})")
                        continue
                    low, high, integer = allowed[key]
                    if integer and value != int(value):
                        errors.append(f"{where}: {key} must be an integer")
                    if value < low * (1 - 1e-9) or value > high * (1 + 1e-9):
                        errors.append(f"{where}: {key}={value:g} is outside [{low:g}, {high:g}]")
                if master.startswith("sg13_") and ("w" not in params or "l" not in params):
                    errors.append(f"{where}: MOS devices must set w and l")
                if master in ("rppd", "rsil", "rhigh", "cap_cmim") and ("w" not in params or "l" not in params):
                    errors.append(f"{where}: {master} must set w and l")
            elif master in subckts:
                if len(nodes) != len(subckts[master]["ports"]):
                    errors.append(f"{where}: subckt {master} has {len(subckts[master]['ports'])} ports")
                if params:
                    errors.append(f"{where}: subckt instances take no parameters")
            else:
                errors.append(f"{where}: {master} is not an allowed PDK cell or a subckt of this file")
    return errors


def flatten(subckts, top, prefix, portmap):
    """Return leaf PDK devices of `top` as (path, master, {terminal: node}, params).

    `portmap` maps the ports of `top` to bench nodes; internal nodes are named the way
    VACASK names them in raw files (instance path joined with ':').
    """
    leaves = []

    def walk(name, path, mapping, stack):
        if len(stack) > MAX_DEPTH or name in stack:
            raise ValueError(f"subckt {name} is instantiated recursively or deeper than {MAX_DEPTH} levels")
        for inst, nodes, master, params, _ in subckts[name]["instances"]:
            resolved = [mapping.get(n, f"{path}:{n}") for n in nodes]
            if master in DEVICES:
                leaves.append((f"{path}:{inst}", master, dict(zip(DEVICES[master][0], resolved)), params))
            else:
                walk(master, f"{path}:{inst}", dict(zip(subckts[master]["ports"], resolved)), stack | {name})

    walk(top, prefix, portmap, frozenset())
    return leaves


def check_file(path, required=None):
    try:
        with open(path, encoding="utf-8") as handle:
            text = handle.read()
    except (OSError, UnicodeDecodeError) as exc:
        return None, [f"cannot read {path}: {exc}"]
    subckts, errors = parse(text)
    errors += check_devices(subckts)
    if required:
        name, ports = required[0], required[1:]
        if name not in subckts:
            errors.append(f"subckt {name} is not defined")
        elif subckts[name]["ports"] != ports:
            errors.append(f"subckt {name} must have the ports ({' '.join(ports)}) in that order, "
                          f"found ({' '.join(subckts[name]['ports'])})")
        elif not errors:
            try:
                leaves = flatten(subckts, name, "top", {p: p for p in ports})
            except ValueError as exc:
                errors.append(str(exc))
                leaves = []
            if not leaves and not errors:
                errors.append(f"subckt {name} contains no PDK devices")
            area = sum(p.get("w", 0.0) * p.get("l", 0.0) * p.get("m", 1.0)
                       for _, master, _, p in leaves if master == "cap_cmim")
            if area > MIM_AREA_MAX * (1 + 1e-9):
                errors.append(f"total cap_cmim area {area * 1e12:.0f} um^2 exceeds {MIM_AREA_MAX * 1e12:.0f} um^2")
            for leaf_path, master, terminals, _ in leaves:
                pin = SUBSTRATE.get(master)
                if pin and "vss" in ports and terminals[pin] != "vss":
                    errors.append(f"{leaf_path} ({master}): terminal {pin} is the p-substrate and must "
                                  f"connect to vss, found {terminals[pin]}")
    return subckts, errors


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("netlist")
    parser.add_argument("--require-subcircuit", nargs="+", metavar="NAME_AND_PORTS")
    args = parser.parse_args()
    _, errors = check_file(args.netlist, args.require_subcircuit)
    for error in errors:
        print(f"ERROR: {error}")
    if errors:
        return 1
    print("OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
