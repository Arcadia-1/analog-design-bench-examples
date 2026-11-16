#!/usr/bin/env python3
"""Task netlist rules for fd_opamp, checked after check_circuit.py; exit 0 when they hold.

`node_rules.py <circuit.spi>`:
- no node may be a ground name (0, 00, 0.0, gnd, gnd! in any case): the design
  reaches the bench only through its eight pins;
- an ideal resistor or capacitor is written `Rname n1 n2 <value>` or
  `Cname n1 n2 <value>` with a literal value, optionally followed by an integer
  multiplier `m=<n>` (n >= 1); no other instance parameter is allowed;
- the limits count every instance reached from fd_opamp through the subcircuit
  hierarchy, with the product of the integer `m` of every X line on the way:
  each resistor at most 10 Mohm (R / m_total), each capacitor at most 100 pF
  (C * m_total), all capacitors together at most 200 pF, and all resistors
  together (each R / m_total) at most 50 Mohm, which also bounds any series chain.
Comments follow ngspice: `*` at line start, `;` anywhere, `$` at line start or
after a space or tab (a `$` glued to a word is part of that word).
"""
import re
import sys
from pathlib import Path

R_MAX = 10e6
R_TOTAL_MAX = 50e6
C_MAX = 100e-12
C_TOTAL_MAX = 200e-12
TOP = "fd_opamp"
SCALE = {"t": 1e12, "g": 1e9, "meg": 1e6, "k": 1e3, "m": 1e-3, "u": 1e-6, "n": 1e-9, "p": 1e-12, "f": 1e-15}
NUMBER = re.compile(r"([-+]?(?:\d+\.?\d*|\.\d+)(?:e[-+]?\d+)?)(meg|[tgkmunpf])?[a-z]*")


def is_ground(node: str) -> bool:
    low = node.lower()
    return low in {"gnd", "gnd!"} or re.fullmatch(r"0+(\.0*)?|\.0+", low) is not None


def value(token: str) -> float:
    m = NUMBER.fullmatch(token.lower())
    if not m:
        raise ValueError(f"the value {token!r} is not a literal number")
    return float(m.group(1)) * SCALE.get(m.group(2) or "", 1.0)


def words_of(text: str) -> list[str]:
    """SPICE words as ngspice reads them: optional space around '=' removed, and
    parentheses and commas treated as separators (so `(0 vss)` names node 0)."""
    return re.sub(r"\s*=\s*", "=", re.sub(r"[(),]", " ", text)).split()


def strip_comment(raw: str) -> str:
    line = raw.split(";", 1)[0]
    return re.split(r"(?:^|(?<=[ \t]))\$", line, maxsplit=1)[0].rstrip()


def logical_lines(text: str) -> list[tuple[int, str]]:
    out: list[tuple[int, str]] = []
    for number, raw in enumerate(text.splitlines(), 1):
        line = strip_comment(raw)
        if not line.strip() or line.lstrip().startswith("*"):
            continue
        if line.lstrip().startswith("+") and out:
            out[-1] = (out[-1][0], out[-1][1] + " " + line.lstrip()[1:])
        else:
            out.append((number, line.strip()))
    return out


def integer_m(setting: str) -> int | None:
    return int(setting) if re.fullmatch(r"\d+", setting) and int(setting) >= 1 else None


def split_instance(words: list[str]) -> tuple[list[str], list[str]]:
    """The positional words of an X line (nodes, then the subcircuit) and its parameters."""
    for k, w in enumerate(words[1:], 1):
        if "=" in w or w.lower() == "params:":
            return words[1:k], words[k:]
    return words[1:], []


def check(path: Path) -> list[str]:
    problems: list[str] = []
    subckts: dict[str, list[tuple[int, str, list[str]]]] = {}
    current = None
    for number, line in logical_lines(path.read_text()):
        words = words_of(line)
        head = words[0].lower()
        if head == ".subckt" and len(words) > 1:
            current = words[1].lower()
            subckts[current] = []
            nodes = words[2:]
        elif head == ".ends":
            current = None
            continue
        elif head.startswith("."):
            continue
        elif head[0] == "x":
            nodes = split_instance(words)[0][:-1]
        else:
            nodes = words[1:3]
        for node in nodes:
            if is_ground(node):
                problems.append(f"line {number}: node {node!r} is a ground name, which is not allowed: {line}")
        if current is not None and not head.startswith("."):
            subckts[current].append((number, line, words))
    if TOP not in subckts:
        return problems + [f"subcircuit {TOP} not found"]

    c_total = 0.0
    r_total = 0.0
    # A subcircuit's lines are visited once per instance; syntax problems are reported once.
    seen_lines: set[int] = set()

    def walk(name: str, mult: int, stack: tuple[str, ...]) -> None:
        nonlocal c_total, r_total
        for number, line, words in subckts[name]:
            head = words[0].lower()
            first = number not in seen_lines
            seen_lines.add(number)
            if head[0] == "x":
                positional, params = split_instance(words)
                target = positional[-1].lower() if positional else ""
                if target not in subckts:
                    continue  # a PDK device: check_circuit.py checks its parameters
                m = 1
                for p in params:
                    key, _, setting = p.partition("=")
                    if key.lower() == "m" and integer_m(setting) is not None:
                        m = integer_m(setting)
                    elif first:
                        problems.append(f"line {number}: instance parameter {p!r} is not allowed on a "
                                        f"local subcircuit (only an integer m >= 1): {line}")
                if target in stack:
                    problems.append(f"line {number}: recursive subcircuit {target}: {line}")
                    continue
                walk(target, mult * m, stack + (target,))
                continue
            if head[0] not in "rc":
                continue
            if len(words) < 4 or "=" in words[3]:
                if first:
                    problems.append(f"line {number}: write the value as the fourth word, a literal number: {line}")
                continue
            try:
                v = value(words[3])
            except ValueError as error:
                if first:
                    problems.append(f"line {number}: {error}: {line}")
                continue
            m = 1
            bad = False
            for extra in words[4:]:
                key, _, setting = extra.partition("=")
                if key.lower() == "m" and integer_m(setting) is not None:
                    m = integer_m(setting)
                else:
                    bad = True
                    if first:
                        problems.append(f"line {number}: instance parameter {extra!r} is not allowed "
                                        f"(only an integer m >= 1): {line}")
            if bad:
                continue
            total = mult * m
            where = "" if total == 1 else f" ({total} in parallel)"
            if head[0] == "r":
                r_total += v / total
            if head[0] == "r" and v / total > R_MAX:
                problems.append(f"line {number}: resistance {v / total:g} ohm exceeds {R_MAX:g} ohm{where}: {line}")
            if head[0] == "c":
                if v * total > C_MAX:
                    problems.append(f"line {number}: capacitance {v * total:g} F exceeds {C_MAX:g} F{where}: {line}")
                c_total += v * total

    walk(TOP, 1, (TOP,))
    if r_total > R_TOTAL_MAX:
        problems.append(f"the resistors reached from {TOP} add up to {r_total:g} ohm, more than {R_TOTAL_MAX:g} ohm")
    if c_total > C_TOTAL_MAX:
        problems.append(f"the capacitors reached from {TOP} add up to {c_total:g} F, more than {C_TOTAL_MAX:g} F")
    return problems


def main() -> int:
    if len(sys.argv) != 2:
        print(__doc__)
        return 2
    problems = check(Path(sys.argv[1]))
    for problem in problems:
        print(f"{sys.argv[1]}: {problem}")
    if problems:
        return 1
    print(f"{sys.argv[1]}: task netlist rules passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
