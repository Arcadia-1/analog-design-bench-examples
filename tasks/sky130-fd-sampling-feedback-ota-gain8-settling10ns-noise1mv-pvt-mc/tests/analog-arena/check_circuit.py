#!/usr/bin/env python3
"""Check that a circuit uses approved PDK and ideal elements."""

import argparse
from fnmatch import fnmatchcase
import json
import math
import os
import re
from pathlib import Path


IDEAL_ELEMENTS = {"R", "C", "L", "K"}
ALLOWED_DIRECTIVES = {".subckt", ".ends"}
SOURCE_PDK_LIST = Path(__file__).with_name("sky130_pdk_subcircuits.txt")
SOURCE_MOS_GEOMETRY = Path(__file__).with_name("sky130_mos_geometry.json")
NUMBER_RE = re.compile(
    r"^[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:e[+-]?\d+)?[a-z]*$",
    re.IGNORECASE,
)
SCALE_FACTORS = {
    "t": 1e12,
    "g": 1e9,
    "meg": 1e6,
    "k": 1e3,
    "m": 1e-3,
    "u": 1e-6,
    "n": 1e-9,
    "p": 1e-12,
    "f": 1e-15,
    "mil": 25.4e-6,
}
POSITIVE_PARAMETERS = {"l", "w", "nf", "m", "mult", "mf"}
NONNEGATIVE_PARAMETERS = {"ad", "as", "pd", "ps"}
INTEGER_PARAMETERS = {"nf", "m", "mult", "mf"}
# Published minimum lengths in SKY130_NETLIST_GUIDE.md are eligibility limits,
# independent of ngspice's tolerance when searching model bins.
PUBLIC_MOS_MIN_LENGTH_UM = {
    "sky130_fd_pr__nfet_01v8": 0.15,
    "sky130_fd_pr__pfet_01v8": 0.15,
    "sky130_fd_pr__nfet_01v8_lvt": 0.15,
    "sky130_fd_pr__pfet_01v8_lvt": 0.35,
    "sky130_fd_pr__nfet_g5v0d10v5": 0.5,
    "sky130_fd_pr__pfet_g5v0d10v5": 0.5,
}


def logical_lines(path: Path):
    lines = []
    for number, raw in enumerate(path.read_text().splitlines(), 1):
        text = raw.split("$", 1)[0].split(";", 1)[0].strip()
        if text.startswith("+") and lines:
            lines[-1] = (lines[-1][0], f"{lines[-1][1]} {text[1:].strip()}")
        else:
            lines.append((number, text))
    return lines


def spice_words(text: str) -> list[str]:
    """Tokenize numeric assignments with SPICE's optional surrounding space."""
    return re.sub(r"\s*=\s*", "=", text).split()


def positional_words(words: list[str]) -> list[str]:
    result = []
    for word in words:
        if word.lower() == "params:" or "=" in word:
            break
        result.append(word)
    return result


def x_target(text: str) -> str:
    positional = positional_words(spice_words(text)[1:])
    return positional[-1].lower() if positional else ""


def pdk_interfaces(path: Path) -> dict[str, list[str] | None]:
    """Read approved names and optional formal pins from the pinned PDK list."""
    interfaces = {}
    for raw in path.read_text().splitlines():
        words = raw.split("#", 1)[0].lower().split()
        if words:
            interfaces[words[0]] = words[1:] or None
    return interfaces


def expand_pdk_policy(patterns: list[str], approved: set[str]) -> set[str]:
    """Expand task policy patterns only against the pinned approved names."""
    selected = set()
    for pattern in patterns:
        matches = {name for name in approved if fnmatchcase(name, pattern)}
        if not matches:
            raise ValueError(f"no approved PDK subcircuit matches policy pattern {pattern!r}")
        selected.update(matches)
    return selected


def spice_number(token: str) -> float | None:
    """Parse a finite SPICE numeric literal without evaluating expressions."""
    value = token.strip().lower()
    if not NUMBER_RE.fullmatch(value):
        return None
    match = re.match(
        r"^([+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:e[+-]?\d+)?)([a-z]*)$",
        value,
    )
    if match is None:
        return None
    number_text, suffix = match.groups()
    scale = 1.0
    matched_suffix = not suffix
    for prefix in ("meg", "mil", "t", "g", "k", "m", "u", "n", "p", "f"):
        if suffix.startswith(prefix):
            scale = SCALE_FACTORS[prefix]
            matched_suffix = True
            break
    if not matched_suffix:
        return None
    try:
        number = float(number_text) * scale
    except ValueError:
        return None
    return number if math.isfinite(number) else None


def assignment_violations(words: list[str]) -> list[str]:
    violations = []
    for word in words:
        if "=" not in word:
            continue
        name, value_text = word.split("=", 1)
        name = name.lower()
        value = spice_number(value_text)
        if not name or value is None:
            violations.append(f"parameter {word} is not a finite numeric literal")
        elif name in POSITIVE_PARAMETERS and value <= 0:
            violations.append(f"parameter {name} must be positive")
        elif name in NONNEGATIVE_PARAMETERS and value < 0:
            violations.append(f"parameter {name} must be non-negative")
    return violations


def assignments(words: list[str]) -> dict[str, float]:
    result = {}
    for word in words:
        if "=" not in word:
            continue
        name, value_text = word.split("=", 1)
        value = spice_number(value_text)
        if name and value is not None:
            result[name.lower()] = value
    return result


def ngspice_in_range(value: float, lower: float, upper: float) -> bool:
    """ngspice-46 INPgetModBin's metre-based comparison, including its tolerance."""
    return abs(value - lower) < 1e-9 or abs(value - upper) < 1e-9 or lower < value < upper


def matches_mos_bin(length_m: float, width_m: float, bins: list[dict]) -> bool:
    return any(
        ngspice_in_range(length_m, float(bounds["lmin"]), float(bounds["lmax"]))
        and ngspice_in_range(width_m, float(bounds["wmin"]), float(bounds["wmax"]))
        for bounds in bins
    )


def sky130_geometry_violations(text: str, geometry: dict) -> list[str]:
    target = x_target(text)
    parameters = assignments(spice_words(text)[1:])
    violations = []
    for name in sorted(INTEGER_PARAMETERS & parameters.keys()):
        if not parameters[name].is_integer():
            violations.append(f"parameter {name} must be an integer")
    if wrapper := geometry["wrappers"].get(target):
        dimensions = {**wrapper["defaults"], **parameters}
        length, width, fingers = (dimensions[name] for name in ("l", "w", "nf"))
        minimum_length = PUBLIC_MOS_MIN_LENGTH_UM.get(target)
        if minimum_length is not None and length < minimum_length:
            violations.append(
                f"{target} l={length:g}um is below published minimum l={minimum_length:g}um"
            )
        if length > 0 and width > 0 and fingers > 0:
            # The pinned ngspice-46 runs without compatibility mode or wnflag;
            # INPgetModBin therefore uses total W, without dividing by nf.
            bins = geometry["model_bins"][wrapper["model"]]
            if not matches_mos_bin(length * 1e-6, width * 1e-6, bins):
                violations.append(
                    f"{target} l={length:g}um w={width:g}um is outside modeled range; "
                    f"no matching {wrapper['model']} bin"
                )
    if target.startswith("sky130_fd_pr__cap_mim_") and "mf" in parameters:
        violations.append(
            f"{target} continuous wrapper does not declare mf; "
            "mult affects mismatch, not nominal capacitance"
        )
    return violations


def required_interface_violation(
    lines: list[tuple[int, str]],
    required: list[str] | None,
) -> tuple[int, str, str] | None:
    if not required:
        return None
    expected_name, *expected_pins = (word.lower() for word in required)
    for number, text in lines:
        words = spice_words(text)
        if len(words) < 2 or words[0].lower() != ".subckt":
            continue
        if words[1].lower() != expected_name:
            continue
        pins = [word.lower() for word in positional_words(words[2:])]
        if pins == expected_pins:
            return None
        return (
            number,
            f"required subcircuit {expected_name} has pins {' '.join(pins) or '?'}; "
            f"expected {' '.join(expected_pins)}",
            text,
        )
    return (0, f"required subcircuit {expected_name} not found", expected_name)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("circuit", type=Path)
    parser.add_argument('--passive-expressions', action='store_true',
                        help='opt-in RLC-only constant expressions and local parameters; no PDK or sources')
    parser.add_argument(
        "--allow-ideal",
        nargs="*",
        choices=sorted(IDEAL_ELEMENTS),
        default=(),
        metavar="ELEMENT",
    )
    parser.add_argument(
        "--require-subcircuit",
        nargs="+",
        metavar=("NAME", "PIN"),
        help="require an exact public top-level subcircuit name and pin order",
    )
    parser.add_argument(
        "--enforce-sky130-geometry",
        action="store_true",
        help="enforce known modeled MOS ranges and integral multiplicities",
    )
    pdk_policy = parser.add_mutually_exclusive_group()
    pdk_policy.add_argument(
        "--allow-pdk",
        nargs="+",
        type=str.lower,
        metavar="PATTERN",
        help="allow only approved PDK names matching these fnmatch patterns",
    )
    pdk_policy.add_argument(
        "--disallow-pdk",
        nargs="+",
        type=str.lower,
        default=(),
        metavar="PATTERN",
        help="exclude approved PDK names matching these published fnmatch patterns",
    )
    args = parser.parse_args()

    if args.passive_expressions:
        from passive_expressions import validate_passive
        if set(args.allow_ideal) != {'R','L','C'} or args.allow_pdk or args.disallow_pdk:
            parser.error('passive expressions require exactly --allow-ideal R L C and no PDK policy')
        try:
            validate_passive(logical_lines(args.circuit), args.require_subcircuit, spice_number)
        except (ValueError, SyntaxError, OverflowError, ZeroDivisionError, TypeError, RecursionError) as error:
            print(f'{args.circuit}: passive eligibility failed: {error}')
            return 1
        print(f'{args.circuit}: netlist check passed (constant RLC expressions)')
        return 0

    pdk_list = Path(os.environ.get("SKY130_PDK_SUBCIRCUITS", SOURCE_PDK_LIST))
    pdk_pins = pdk_interfaces(pdk_list)
    pdk_subcircuits = set(pdk_pins)
    try:
        allowed_pdk = (
            expand_pdk_policy(args.allow_pdk, pdk_subcircuits)
            if args.allow_pdk is not None
            else pdk_subcircuits - expand_pdk_policy(args.disallow_pdk, pdk_subcircuits)
        )
    except ValueError as error:
        parser.error(str(error))
    geometry = None
    if args.enforce_sky130_geometry:
        geometry_path = Path(os.environ.get("SKY130_MOS_GEOMETRY", SOURCE_MOS_GEOMETRY))
        try:
            geometry = json.loads(geometry_path.read_text())
        except (OSError, ValueError) as error:
            parser.error(f"cannot load pinned MOS geometry: {error}")
    lines = logical_lines(args.circuit)
    local_pins = {
        words[1].lower(): positional_words(words[2:])
        for _, text in lines
        if len(words := spice_words(text)) > 1 and words[0].lower() == ".subckt"
    }
    local_subcircuits = set(local_pins)
    allowed_elements = {"X", *args.allow_ideal}
    local_references = {name: set() for name in local_subcircuits}
    has_leaf = {name: False for name in local_subcircuits}
    inductors = {name: set() for name in local_subcircuits}
    couplings = []
    violations = []
    current_subcircuit = None
    seen_subcircuits = set()

    for name in sorted(local_subcircuits & pdk_subcircuits):
        violations.append((0, f"local subcircuit shadows PDK name {name}", name))

    for number, text in lines:
        if not text or text[0] in "*;":
            continue
        if text.startswith("."):
            words = spice_words(text)
            directive = words[0].lower()
            if directive not in ALLOWED_DIRECTIVES:
                violations.append((number, f"disallowed directive {directive}", text))
            elif directive == ".subckt":
                if len(words) < 2:
                    violations.append((number, "malformed .subckt", text))
                elif current_subcircuit is not None:
                    violations.append((number, "nested .subckt", text))
                elif words[1].lower() in seen_subcircuits:
                    violations.append((number, "duplicate .subckt", text))
                else:
                    current_subcircuit = words[1].lower()
                    seen_subcircuits.add(current_subcircuit)
                    for reason in assignment_violations(words[2:]):
                        violations.append((number, reason, text))
            elif current_subcircuit is None:
                violations.append((number, ".ends outside subcircuit", text))
            else:
                if len(words) > 1 and words[1].lower() != current_subcircuit:
                    violations.append((number, ".ends name does not match .subckt", text))
                current_subcircuit = None
            continue
        kind = text[0].upper()
        if current_subcircuit is None:
            violations.append((number, "element outside subcircuit", text))
        if kind not in allowed_elements:
            violations.append((number, f"disallowed {kind}", text))
        elif kind == "X":
            words = spice_words(text)
            positional = positional_words(words[1:])
            target = positional[-1].lower() if positional else ""
            for reason in assignment_violations(words[1:]):
                violations.append((number, reason, text))
            if args.enforce_sky130_geometry:
                for reason in sky130_geometry_violations(text, geometry):
                    violations.append((number, reason, text))
            pins = local_pins.get(target, pdk_pins.get(target))
            if pins is not None:
                expected = len(pins)
                actual = len(positional) - 1
                if actual != expected:
                    violations.append((
                        number,
                        f"subcircuit {target} expects {expected} pins, got {actual}",
                        text,
                    ))
            if target in local_subcircuits:
                if current_subcircuit:
                    local_references[current_subcircuit].add(target)
            elif target in pdk_subcircuits:
                if target not in allowed_pdk:
                    violations.append((number, f"disallowed PDK subcircuit {target}", text))
                if current_subcircuit:
                    has_leaf[current_subcircuit] = True
            else:
                violations.append((number, f"unknown subcircuit {target or '?'}", text))
        else:
            words = spice_words(text)
            if len(words) < 4:
                violations.append((number, f"malformed {kind}", text))
            else:
                value = spice_number(words[3])
                if value is None:
                    violations.append((number, f"{kind} value is not a finite numeric literal", text))
                elif kind == "K" and not -1.0 <= value <= 1.0:
                    violations.append((number, "K value must be between -1 and 1", text))
                elif kind != "K" and value <= 0:
                    violations.append((number, f"{kind} value must be positive", text))
                for reason in assignment_violations(words[4:]):
                    violations.append((number, reason, text))
            if current_subcircuit and kind == "L":
                inductors[current_subcircuit].add(words[0].lower())
            elif current_subcircuit and kind == "K" and len(words) >= 4:
                couplings.append((number, text, current_subcircuit, words[1].lower(), words[2].lower()))
            if current_subcircuit and kind != "K":
                has_leaf[current_subcircuit] = True

    if current_subcircuit is not None:
        violations.append((0, f"subcircuit {current_subcircuit} is missing .ends", current_subcircuit))

    if interface_violation := required_interface_violation(lines, args.require_subcircuit):
        violations.append(interface_violation)

    for number, text, subcircuit, first, second in couplings:
        missing = [name for name in (first, second) if name not in inductors[subcircuit]]
        if missing:
            violations.append((number, f"K references unknown inductor {missing[0]}", text))

    resolved = {}

    def resolves(name: str, visiting=frozenset()) -> bool:
        if name in resolved:
            return resolved[name]
        if name in visiting:
            return False
        children_resolve = all(
            resolves(child, visiting | {name})
            for child in local_references[name]
        )
        resolved[name] = children_resolve and (
            has_leaf[name] or bool(local_references[name])
        )
        return resolved[name]

    for name in sorted(local_subcircuits):
        if not resolves(name):
            violations.append((0, f"subcircuit {name} has no approved leaf", name))

    for number, reason, text in violations:
        location = f"{args.circuit}:{number}" if number else str(args.circuit)
        print(f"{location}: {reason}: {text}")
    if violations:
        return 1
    print(f"{args.circuit}: netlist check passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
