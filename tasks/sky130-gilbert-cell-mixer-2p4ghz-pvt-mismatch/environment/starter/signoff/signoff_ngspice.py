"""Running ngspice under the SKY130 signoff rules of sky130_signoff.py, for every task's verifier.

`run(cmd, **kwargs)` takes the place of `subprocess.run(cmd, **kwargs)` for an ngspice command line. Before the run it
rewrites the deck (the one non-option argument) into a copy next to it:
- `.spiceinit` in the working folder starts with the PDK's own setting, `set ngbehavior=hsa` (a multi-finger device takes
  the model bin of its finger width, as the SKY130 models were fitted), and one thread per run;
- the circuit under test is every `.include` outside the PDK (/opt/sky130) and outside the verifier's own files
  (/app/analog_arena_tests). It is checked against the netlist rules (an ideal R, C or L takes only its value and m=;
  every MOS model has a voltage rating; m on a local subcircuit is a whole number) and simulated as rewritten by
  with_expanded_multipliers, with_diffusion_geometry (xschem's source/drain geometry where the netlist leaves it out),
  with_mismatch_mult and with_xhigh_po_tc;
- every rated MOS defined in it gets its voltage limits (with_ratings), and every window that averages or integrates a
  source current gets the stored-energy accounting (with_energy).
After the run, a model-parameter error, a rating violation or a stored-energy release beyond the allowance is a rule
violation. A run that fails to converge, or reaches its operating point only through the transient-op fallback, runs
again with RETRY_OPTIONS. A violated run returns code 3 with a `SIGNOFF RULE:` line on stdout, and the violation is kept
in VIOLATIONS and appended to RULES_FILE. utils.write_results adds a failed `signoff_rules` check and scores the task 0
when any run broke a rule. `enforce()` does the same for a verifier that writes reward.json itself.
"""
import os
import threading
import re
import subprocess
from pathlib import Path

import sky130_signoff as s

TIMEOUT = int(os.environ.get("SIGNOFF_NGSPICE_TIMEOUT", "1800"))
TAKES_VALUE = {"-r", "--rawfile", "-o", "--output", "-p", "--pipe", "-D", "--define", "-s", "--server"}
INCLUDE_RE = re.compile(r'^(\s*\.inc(?:lude)?\s+)("?)([^"\s]+)("?)(\s*)$', re.I)
SOURCE_RE = re.compile(r"^\s*v\S*\s+\S+\s+\S+\s+(?:dc\s+)?([-+]?[\d.]+(?:e[-+]?\d+)?)(\S*)", re.I)
INJECTED = re.compile(r"^(@@E |@@SOA|\s*zres_\w+\s*=)")
NOT_THE_CIRCUIT = ("/opt/sky130/", "/app/analog_arena_tests/")
RULES_FILE = Path(os.environ.get("SIGNOFF_RULES_FILE", "/logs/verifier/signoff-rules.txt"))
VIOLATIONS: list[str] = []


def deck_index(args):
    positional, skip = [], False
    for i, a in enumerate(args):
        if skip:
            skip = False
        elif a in TAKES_VALUE:
            skip = True
        elif not str(a).startswith("-"):
            positional.append(i)
    return positional[0] if len(positional) == 1 and Path(args[positional[0]]).is_file() else None


def rated(subckts, circuit_names, deck_text):
    """ngspice paths and models of every rated MOS defined in the circuit under test, wherever the bench places it."""
    devices = []

    def walk(name, prefix, stack):
        if name in stack:
            return
        for words in subckts[name]["lines"]:
            if words[0][0] in "xX":
                target, inst = s.x_parts(words)[1], words[0].lower()
                if target in subckts:
                    walk(target, f"{prefix}.{inst}", stack + (name,))
                elif target in s.RATINGS and name in circuit_names:
                    devices.append((f"m.{prefix}.{inst}.m{target}", target))

    depth, control = 0, False
    for line in s.netlist_lines(deck_text):
        words = line.split()
        word = words[0].lower()
        if word == ".subckt":
            depth += 1
        elif word == ".ends":
            depth = max(0, depth - 1)
        elif word in (".control", ".endc"):
            control = word == ".control"
        elif not depth and not control and word[0] == "x" and s.x_parts(words)[1] in subckts:
            walk(s.x_parts(words)[1], word, ())
    return devices


def supply_of(deck_text):
    values = [abs(v) for line in deck_text.splitlines() if (m := SOURCE_RE.match(line))
              and (v := s.spice_number(m.group(1) + m.group(2))) is not None]
    return max(values) if values else 1.8


def prepare(deck):
    """(rewritten deck text, stored-energy windows, netlist violations, whether a circuit under test was found)."""
    text = deck.read_text(errors="replace")
    lines, circuits, violations, everything = text.splitlines(), [], [], text
    for i, line in enumerate(lines):
        m = INCLUDE_RE.match(line)
        if not m:
            continue
        path = Path(m.group(3))
        path = path if path.is_absolute() else deck.parent / path
        try:
            included = path.read_text(errors="replace")
        except OSError:
            continue
        everything += "\n" + included
        if str(path.resolve()).startswith(NOT_THE_CIRCUIT) or not s.parse_subckts(included):
            continue
        for top in s.parse_subckts(included):
            violations += s.netlist_violations(included, {"subckt": {"name": top}})
        graded = s.with_expanded_multipliers(included) or included
        graded = s.with_diffusion_geometry(graded) or graded
        graded = s.with_mismatch_mult(graded) or graded
        circuits.append(graded)
        # unique per deck, process and thread: parallel runs in one directory must not share a copy
        copy = deck.parent / f".signoff-{deck.stem}-{os.getpid()}-{threading.get_ident()}-{i}-{path.name}"
        copy.write_text(s.with_xhigh_po_tc(graded) or graded)
        lines[i] = f'{m.group(1)}"{copy}"'
    text = "\n".join(lines) + "\n"
    windows = []
    if circuits:
        circuit = "\n".join(circuits)
        names = set(s.parse_subckts(circuit))
        subckts = s.parse_subckts(everything.replace("\n.end\n", "\n") + "\n" + circuit)
        text = s.with_ratings(text, rated(subckts, names, text))
        tops = {s.x_parts(l.split())[1] for l in s.netlist_lines(text) if l[0] in "xX"} & names
        storage = ([], [], [])
        for top in sorted(tops):
            storage = tuple(a + b for a, b in zip(storage, s.dut_storage(circuit, text, top)))
        text, windows = s.with_energy(text, storage, [])
    return text, windows, list(dict.fromkeys(violations)), bool(circuits)


def spiceinit(cwd):
    init = Path(cwd or ".") / ".spiceinit"
    current = init.read_text() if init.exists() else ""
    if "ngbehavior" not in current:
        init.write_text(s.SPICEINIT + current)


def simulate(cmd, at, text, deck, cwd, timeout, env=None):
    copy = deck.parent / f".signoff-{os.getpid()}-{threading.get_ident()}-{deck.name}"
    copy.write_text(text)
    run = [str(c) for c in cmd]
    run[at] = str(copy)
    try:
        p = subprocess.run(run, cwd=cwd, capture_output=True, text=True, timeout=timeout, env=env)
        return p.returncode, p.stdout, p.stderr
    except subprocess.TimeoutExpired as e:
        out = e.stdout.decode(errors="replace") if isinstance(e.stdout, bytes) else (e.stdout or "")
        return 124, out, f"timeout after {timeout} s\n"


def output_log(args):
    for flag in ("-o", "--output"):
        if flag in args and args.index(flag) + 1 < len(args):
            return Path(args[args.index(flag) + 1])
    return None


def clean(text):
    return "".join(l for l in text.splitlines(keepends=True) if not INJECTED.match(l) and not s.SOA_RE.search(l))


def run(cmd, cwd=None, timeout=None, stdout=None, stderr=None, text=False, check=False, env=None, **kwargs):
    """subprocess.run(cmd, ...) for an ngspice command, under the signoff rules; see the module docstring."""
    cmd = [str(c) for c in cmd]
    args, at = cmd[1:], deck_index(cmd[1:])
    spiceinit(cwd)
    rule, code, out, err = None, 0, "", ""
    if at is None:
        p = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=timeout or TIMEOUT, env=env)
        code, out, err = p.returncode, p.stdout, p.stderr
    else:
        at += 1
        deck = Path(cwd or ".", cmd[at]) if not Path(cmd[at]).is_absolute() else Path(cmd[at])
        rewritten, windows, violations, graded = prepare(deck)
        if violations:
            rule = "netlist: " + "; ".join(violations)
        else:
            code, out, err = simulate(cmd, at, rewritten, deck, cwd, timeout or TIMEOUT, env)
            log_file = output_log(args)
            read_log = lambda: out + err + (log_file.read_text(errors="replace") if log_file and log_file.exists() else "")
            log = read_log()
            failed = code != 0 or s.FATAL_RE.search(log)
            if (failed and code != 124 and s.CONVERGENCE_RE.search(log)) or (not failed and s.OP_FALLBACK_RE.search(log)):
                for option in s.RETRY_OPTIONS:
                    if "gear" in option and not re.search(r"timestep too small", log, re.I):
                        continue
                    c2, o2, e2 = simulate(cmd, at, s.with_option(rewritten, option), deck, cwd, timeout or TIMEOUT, env)
                    out_save, err_save = out, err
                    out, err = o2, e2
                    l2 = read_log()
                    if c2 == 0 and not s.FATAL_RE.search(l2) and not s.OP_FALLBACK_RE.search(l2):
                        code, log = c2, l2
                        break
                    out, err = out_save, err_save
            if log_file and log_file.exists():
                log_file.write_text(clean(log_file.read_text(errors="replace")))
            if graded:
                rule = s.model_failure(log) or s.rating_failure(log)
                if not rule and windows:
                    bad = s.energy_violations([{"bench": deck.name, "label": "", "energy":
                                                s.energy_results(log, windows, supply_of(rewritten))}])
                    rule = bad[0] if bad else None
            out, err = clean(out), clean(err)
    if rule:
        VIOLATIONS.append(f"{Path(cmd[at]).name if at else 'ngspice'}: {rule}")
        try:
            RULES_FILE.parent.mkdir(parents=True, exist_ok=True)
            with RULES_FILE.open("a") as f:
                f.write(VIOLATIONS[-1].replace("\n", " ") + "\n")
        except OSError:
            pass
        out, code = out + f"SIGNOFF RULE: {rule}\n", 3
    if stderr == subprocess.STDOUT:
        out, err = out + err, ""
    for stream, value in ((stdout, out), (stderr, err)):
        if stream not in (None, subprocess.PIPE, subprocess.DEVNULL, subprocess.STDOUT) and hasattr(stream, "write"):
            stream.write(value if "b" not in getattr(stream, "mode", "") else value.encode())
    result = subprocess.CompletedProcess(cmd, code,
                                         (out if text else out.encode()) if stdout == subprocess.PIPE else None,
                                         (err if text else err.encode()) if stderr == subprocess.PIPE else None)
    if check and code:
        raise subprocess.CalledProcessError(code, cmd, result.stdout, result.stderr)
    return result


def rule_check():
    """The `signoff_rules` check for a result writer: passed when no run broke a rule."""
    if not VIOLATIONS:
        return ("signoff_rules", True, "no signoff rule broken")
    unique = list(dict.fromkeys(VIOLATIONS))
    return ("signoff_rules", False, f"{len(unique)} run(s) broke a signoff rule; the task scores 0: " + " | ".join(unique[:5]))


def enforce(output=Path("/logs/verifier")):
    """Score the task 0 when any run broke a signoff rule: rewrite reward.json and add a failed `signoff_rules` test to
    new-ctrf.json (once). Called at the end of test.sh, after the verifier."""
    import json
    lines = [l for l in (RULES_FILE.read_text().splitlines() if RULES_FILE.exists() else []) if l.strip()]
    if not lines:
        return 0
    unique = list(dict.fromkeys(lines))
    message = f"{len(unique)} run(s) broke a signoff rule; the task scores 0: " + " | ".join(unique[:5])
    reward_path = output / "reward.json"
    try:
        reward = json.loads(reward_path.read_text())
    except (OSError, ValueError):
        reward = {}
    reward.update(reward=0, partial=0.0, signoff_rules=unique[:20])
    output.mkdir(parents=True, exist_ok=True)
    reward_path.write_text(json.dumps(reward) + "\n")
    report = output / "new-ctrf.json"
    try:
        ctrf = json.loads(report.read_text())
        tests = ctrf["results"]["tests"]
        if not any(t.get("name") == "signoff_rules" for t in tests):
            tests.append({"name": "signoff_rules", "status": "failed", "message": message})
            summary = ctrf["results"]["summary"]
            summary["tests"], summary["failed"] = summary.get("tests", 0) + 1, summary.get("failed", 0) + 1
            report.write_text(json.dumps(ctrf, indent=2) + "\n")
    except (OSError, ValueError, KeyError, TypeError):
        pass
    print(f"FAIL signoff_rules: {message}")
    return 0


if __name__ == "__main__":
    raise SystemExit(enforce())
