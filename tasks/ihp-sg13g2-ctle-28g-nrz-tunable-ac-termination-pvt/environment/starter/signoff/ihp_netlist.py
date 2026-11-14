"""IHP SG13G2 netlist rules for this task: parse, check and flatten a submitted circuit.

The same file is in /app/signoff/ (public) and in the grader. The rules are stated in /app/spec/signoff-rules.md.
"""

import re

NUMBER = re.compile(r"^[+-]?(?:\d+\.?\d*|\.\d+)(?:e[+-]?\d+)?(meg|[tgkmunpf])?$", re.I)
SCALE = {"t": 1e12, "g": 1e9, "meg": 1e6, "k": 1e3, "m": 1e-3, "u": 1e-6, "n": 1e-9, "p": 1e-12, "f": 1e-15}
NAME = re.compile(r"^[a-z][a-z0-9_]*$", re.I)
NODE = re.compile(r"^[a-z0-9_]+$", re.I)
RESERVED_NODES = {"0", "gnd", "gnd!"}

# Nominal operating voltage + 10 % for MOS and the hv varactor; the VBIC model card's vbe_max/vbc_max/vce_max for the HBT.
MOS_RATING_V = {"lv": 1.32, "hv": 3.63}
HBT_RATING_V = {"vce": 1.6, "vbe": 1.6, "vbc": 5.1}
JUNCTION_FORWARD_MAX_V = 0.4

PDK = {
    "sg13_lv_nmos": {"kind": "mos", "pins": ("d", "g", "s", "b"), "pol": "n", "class": "lv", "lmin": 0.13e-6},
    "sg13_lv_pmos": {"kind": "mos", "pins": ("d", "g", "s", "b"), "pol": "p", "class": "lv", "lmin": 0.13e-6},
    "sg13_hv_nmos": {"kind": "mos", "pins": ("d", "g", "s", "b"), "pol": "n", "class": "hv", "lmin": 0.45e-6},
    "sg13_hv_pmos": {"kind": "mos", "pins": ("d", "g", "s", "b"), "pol": "p", "class": "hv", "lmin": 0.40e-6},
    "npn13g2": {"kind": "hbt", "pins": ("c", "b", "e", "bn")},
    "rsil": {"kind": "res", "pins": ("p", "n", "bn"), "wmin": 0.5e-6, "lmin": 0.5e-6},
    "rppd": {"kind": "res", "pins": ("p", "n", "bn"), "wmin": 0.5e-6, "lmin": 0.5e-6},
    "rhigh": {"kind": "res", "pins": ("p", "n", "bn"), "wmin": 0.5e-6, "lmin": 0.5e-6},
    "cap_cmim": {"kind": "cap", "pins": ("p", "n")},
    "sg13_hv_svaricap": {"kind": "var", "pins": ("g1", "w", "g2", "bn")},
}
PARAMS = {"mos": {"w", "l", "ng", "m"}, "hbt": {"nx", "m"}, "res": {"w", "l", "m"}, "cap": {"w", "l", "m"},
          "var": {"w", "l", "nx", "m"}}
REQUIRED = {"mos": {"w", "l"}, "hbt": set(), "res": {"w", "l"}, "cap": {"w", "l"}, "var": {"w", "l"}}
VARACTOR_W = (3.74e-6, 9.74e-6)
VARACTOR_L = (0.3e-6, 0.8e-6)
MOS_WMIN_PER_FINGER = 0.15e-6
# Ideal elements stand for on-die passives, so their size is bounded: an inductor no larger than a few spiral turns,
# a capacitor no larger than a large MIM array (about 0.03 mm2 at 1.5 fF/um2).
IDEAL_MAX = {"l": 5e-9, "c": 50e-12}
IDEAL_LIMIT_TEXT = {"l": "5 nH", "c": "50 pF"}
MOS_LMAX = 10e-6
CMIM_SIDE = (1.14e-6, 75e-6)
NX_RANGE = (1, 10)


class NetlistError(Exception):
    pass


def number(token):
    match = NUMBER.match(token)
    if not match:
        raise NetlistError(f"not a numeric literal: {token}")
    suffix = match.group(1)
    body = token[: len(token) - len(suffix)] if suffix else token
    return float(body) * (SCALE[suffix.lower()] if suffix else 1.0)


def whole(value, what):
    if value != int(value) or value < 1:
        raise NetlistError(f"{what} must be a whole number >= 1")
    return int(value)


def logical_lines(text):
    lines = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("*"):
            continue
        if line.startswith("+"):
            if not lines:
                raise NetlistError("continuation line without a preceding line")
            lines[-1] += " " + line[1:].strip()
        else:
            lines.append(line)
    return lines


def parse(text):
    """Return {subckt_name: {"pins": [...], "elements": [...]}} or raise NetlistError."""
    subckts = {}
    current = None
    for line in logical_lines(text):
        for bad in ("{", "}", "'", '"', ";", "$"):
            if bad in line:
                raise NetlistError(f"character {bad!r} is not allowed (no expressions, quotes or inline comments)")
        tokens = line.split()
        head = tokens[0].lower()
        if head == ".subckt":
            if current is not None:
                raise NetlistError("nested .subckt")
            if len(tokens) < 3 or any("=" in t for t in tokens):
                raise NetlistError(f"bad .subckt line (no parameters allowed): {line}")
            name = tokens[1].lower()
            if not NAME.match(name) or name in subckts or name in PDK:
                raise NetlistError(f"bad or duplicate subcircuit name: {tokens[1]}")
            pins = [p.lower() for p in tokens[2:]]
            if len(set(pins)) != len(pins) or not all(NODE.match(p) for p in pins):
                raise NetlistError(f"bad pins on .subckt {tokens[1]}")
            current = {"name": name, "pins": pins, "elements": []}
            subckts[name] = current
        elif head == ".ends":
            if current is None:
                raise NetlistError(".ends without .subckt")
            current = None
        elif head == ".end":
            if current is not None:
                raise NetlistError(".end inside a subcircuit")
        elif head.startswith("."):
            raise NetlistError(f"control line not allowed: {tokens[0]}")
        else:
            if current is None:
                raise NetlistError(f"element outside a subcircuit: {line}")
            current["elements"].append(element(tokens))
    if current is not None:
        raise NetlistError(f"missing .ends for {current['name']}")
    return subckts


def element(tokens):
    name = tokens[0].lower()
    if not NAME.match(name):
        raise NetlistError(f"bad element name: {tokens[0]}")
    kind = name[0]
    if kind in "rcl":
        if len(tokens) != 4:
            raise NetlistError(f"{tokens[0]}: an ideal R, C or L is 'name node node value' only")
        nodes = [t.lower() for t in tokens[1:3]]
        value = number(tokens[3])
        if not value > 0:
            raise NetlistError(f"{tokens[0]}: value must be positive")
        if kind in IDEAL_MAX and value > IDEAL_MAX[kind] * (1 + 1e-9):
            raise NetlistError(f"{tokens[0]}: an ideal {kind.upper()} may be at most {IDEAL_LIMIT_TEXT[kind]} "
                               "(an on-die element)")
        return {"name": name, "type": kind, "nodes": nodes, "value": value}
    if kind == "k":
        if len(tokens) != 4:
            raise NetlistError(f"{tokens[0]}: K is 'name L1 L2 coupling'")
        k = number(tokens[3])
        if not 0 < k < 1:
            raise NetlistError(f"{tokens[0]}: coupling must be between 0 and 1")
        return {"name": name, "type": "k", "inductors": [t.lower() for t in tokens[1:3]], "value": k}
    if kind == "x":
        params = {}
        positional = []
        for token in tokens[1:]:
            if "=" in token:
                key, _, val = token.partition("=")
                key = key.lower()
                if not key or key in params:
                    raise NetlistError(f"{tokens[0]}: bad or repeated parameter {token}")
                params[key] = number(val)
            elif params:
                raise NetlistError(f"{tokens[0]}: positional token after parameters: {token}")
            else:
                positional.append(token.lower())
        if len(positional) < 2:
            raise NetlistError(f"{tokens[0]}: missing nodes or subcircuit name")
        return {"name": name, "type": "x", "nodes": positional[:-1], "model": positional[-1], "params": params}
    raise NetlistError(f"{tokens[0]}: element type '{tokens[0][0]}' is not allowed (only R, C, L, K and X)")


def check(text, top, top_pins):
    """Parse and check a submission. Returns the parsed subcircuits; raises NetlistError on the first violation."""
    subckts = parse(text)
    if top not in subckts:
        raise NetlistError(f"missing .subckt {top}")
    if subckts[top]["pins"] != list(top_pins):
        raise NetlistError(f".subckt {top} pins must be exactly: {' '.join(top_pins)}")
    for sub in subckts.values():
        names = [e["name"] for e in sub["elements"]]
        if len(set(names)) != len(names):
            raise NetlistError(f"duplicate element names in {sub['name']}")
        inductors = {e["name"] for e in sub["elements"] if e["type"] == "l"}
        for e in sub["elements"]:
            for node in e.get("nodes", []):
                if not NODE.match(node) or node in RESERVED_NODES:
                    raise NetlistError(f"{e['name']}: node name {node!r} is not allowed (use vss, not 0 or gnd)")
            if e["type"] == "k":
                a, b = e["inductors"]
                if a == b or a not in inductors or b not in inductors:
                    raise NetlistError(f"{e['name']}: must couple two different L in the same subcircuit")
            if e["type"] == "x":
                check_instance(e, subckts)
    check_no_recursion(subckts, top)
    check_substrate(subckts, top, top_pins)
    return subckts


def check_substrate(subckts, top, top_pins):
    """The p substrate is the vss pin: every NMOS bulk and every HBT, resistor and varactor substrate terminal
    (`bn`) must reach the top-level vss net (SG13G2 has no isolated p-well in this device set)."""
    ground = top_pins[0]
    for path, model, pins in flatten(subckts, top, "x", top_pins):
        spec = PDK[model]
        if spec["kind"] == "mos" and spec["pol"] == "n" and pins["b"] != ground:
            raise NetlistError(f"{path}: {model} bulk must be {ground} (the substrate)")
        if "bn" in pins and pins["bn"] != ground:
            raise NetlistError(f"{path}: {model} substrate terminal bn must be {ground}")


def check_instance(e, subckts):
    model, params = e["model"], e["params"]
    if model in subckts:
        if len(e["nodes"]) != len(subckts[model]["pins"]):
            raise NetlistError(f"{e['name']}: {model} has {len(subckts[model]['pins'])} pins")
        if set(params) - {"m"}:
            raise NetlistError(f"{e['name']}: a local subcircuit instance takes only m=")
        if "m" in params:
            whole(params["m"], f"{e['name']} m")
        return
    if model not in PDK:
        raise NetlistError(f"{e['name']}: {model} is not an allowed IHP SG13G2 device or a local subcircuit")
    spec = PDK[model]
    kind = spec["kind"]
    if len(e["nodes"]) != len(spec["pins"]):
        raise NetlistError(f"{e['name']}: {model} has {len(spec['pins'])} terminals ({' '.join(spec['pins'])})")
    extra = set(params) - PARAMS[kind]
    if extra:
        raise NetlistError(f"{e['name']}: parameters {sorted(extra)} not allowed on {model}")
    missing = REQUIRED[kind] - set(params)
    if missing:
        raise NetlistError(f"{e['name']}: {model} needs {sorted(missing)}")
    if "m" in params:
        whole(params["m"], f"{e['name']} m")
    if kind == "mos":
        ng = whole(params.get("ng", 1), f"{e['name']} ng")
        if params["l"] < spec["lmin"] * (1 - 1e-9) or params["l"] > MOS_LMAX:
            raise NetlistError(f"{e['name']}: l must be {spec['lmin'] * 1e6:g}u to {MOS_LMAX * 1e6:g}u")
        if params["w"] / ng < MOS_WMIN_PER_FINGER * (1 - 1e-9) or params["w"] / ng > 10e-6 * (1 + 1e-9):
            raise NetlistError(f"{e['name']}: w/ng must be 0.15u to 10u")
    elif kind == "hbt":
        nx = whole(params.get("nx", 1), f"{e['name']} Nx")
        if not NX_RANGE[0] <= nx <= NX_RANGE[1]:
            raise NetlistError(f"{e['name']}: Nx must be 1 to 10")
    elif kind == "res":
        if params["w"] < spec["wmin"] * (1 - 1e-9) or params["l"] < spec["lmin"] * (1 - 1e-9):
            raise NetlistError(f"{e['name']}: {model} w and l must be at least 0.5u")
    elif kind == "cap":
        for side in ("w", "l"):
            if not CMIM_SIDE[0] * (1 - 1e-9) <= params[side] <= CMIM_SIDE[1] * (1 + 1e-9):
                raise NetlistError(f"{e['name']}: cap_cmim {side} must be 1.14u to 75u")
    elif kind == "var":
        whole(params.get("nx", 1), f"{e['name']} Nx")
        if not VARACTOR_W[0] * (1 - 1e-9) <= params["w"] <= VARACTOR_W[1] * (1 + 1e-9):
            raise NetlistError(f"{e['name']}: sg13_hv_svaricap w must be 3.74u to 9.74u")
        if not VARACTOR_L[0] * (1 - 1e-9) <= params["l"] <= VARACTOR_L[1] * (1 + 1e-9):
            raise NetlistError(f"{e['name']}: sg13_hv_svaricap l must be 0.3u to 0.8u")


def check_no_recursion(subckts, top):
    def visit(name, stack):
        if name in stack:
            raise NetlistError(f"recursive subcircuit {name}")
        for e in subckts[name]["elements"]:
            if e["type"] == "x" and e["model"] in subckts:
                visit(e["model"], stack + [name])
    visit(top, [])


def flatten(subckts, top, instance, port_nets):
    """PDK devices of `top` instantiated as `instance` with its pins on `port_nets`, as
    [(path, model, {terminal: simulator node name})]. Node names follow ngspice: lower case, '.' between levels."""
    devices = []

    def walk(name, prefix, ports):
        sub = subckts[name]
        mapping = dict(zip(sub["pins"], ports))

        def net(node):
            return mapping.get(node, f"{prefix}{node}")
        for e in sub["elements"]:
            if e["type"] != "x":
                continue
            nets = [net(n) for n in e["nodes"]]
            path = f"{prefix}{e['name']}"
            if e["model"] in subckts:
                walk(e["model"], path + ".", nets)
            else:
                devices.append((path, e["model"], dict(zip(PDK[e["model"]]["pins"], nets))))
    walk(top, f"{instance}.".lower(), [n.lower() for n in port_nets])
    return devices
