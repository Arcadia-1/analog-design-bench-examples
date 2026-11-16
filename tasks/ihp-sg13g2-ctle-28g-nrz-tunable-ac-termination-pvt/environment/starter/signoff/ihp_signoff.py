"""Run ngspice under this task's IHP SG13G2 signoff rules (/app/spec/signoff-rules.md).

`run()` takes the same arguments as subprocess.run for an `ngspice -b <deck>` command. Before the run it checks the
included circuit against the netlist rules and puts the PDK's .spiceinit (which loads the OSDI models) in the run
directory. After the run it reads every `signoff_*.raw` file the bench wrote (operating points and transients) and
checks the device voltage ratings. A broken rule is recorded in VIOLATIONS and the run returns exit code 3 with a
`SIGNOFF RULE:` line in its output. The same file is in /app/signoff/ and in the grader.
"""

import os
import re
from array import array
import subprocess
import time
from pathlib import Path

import ihp_netlist

PDK_SPICEINIT = Path(os.environ.get("IHP_PDK_ROOT", "/opt/ihp/pdk/ihp-sg13g2")) / "libs.tech/ngspice/.spiceinit"
TOP = "ctle_28g"
TOP_PINS = ("vss", "vdd", "iref", "vocm", "vinp", "vinn", "gain_b0", "gain_b1", "peak_b0", "peak_b1", "voutp",
            "voutn")
TOLERANCE_V = 1e-3  # numerical noise only: a node held at a rail can read a few uV past it
VIOLATIONS = []

NOT_CONVERGED = re.compile(r"timestep too small|simulation\(s\) aborted|operating point could not be simulated", re.I)
RETRY_OPTIONS = ".options gminsteps=50 srcsteps=50 itl1=1000 itl2=500 itl4=200"
RUN_TIMEOUT_S = 300  # a run still going after this is stopped and gives no measurements
TIMED_OUT = 124
DEADLINE = None  # time.monotonic() after which a failed run is not retried (set by the grader)
INCLUDE = re.compile(r'^\s*\.include\s+"?([^"\s]+)"?\s*$', re.I | re.M)
DUT = re.compile(r"^\s*xdut\s+(.+?)\s+" + TOP + r"\s*$", re.I | re.M)


class RuleError(Exception):
    pass


def read_raw(path, wanted=None):
    """Real-valued ngspice ASCII rawfile: {vector name: array of values} (voltage names without v()), streamed, and
    keeping only the vectors in `wanted` when given (a transient of every node would otherwise fill the memory)."""
    header = {}
    names = []
    with open(path) as handle:
        for line in handle:
            line = line.rstrip("\n")
            if line == "Variables:":
                break
            key, _, value = line.partition(":")
            header[key] = value.strip()
        try:
            nvars, npts = int(header["No. Variables"]), int(header["No. Points"])
        except (KeyError, ValueError) as error:
            raise RuleError(f"unreadable rawfile {Path(path).name}") from error
        if "complex" in header.get("Flags", "real"):
            raise RuleError(f"rawfile {Path(path).name} is complex")
        for _ in range(nvars):
            name = handle.readline().split()[1].lower()
            match = re.fullmatch(r"v\((.+)\)", name)
            names.append(match.group(1) if match else name)
        if handle.readline().strip() != "Values:":
            raise RuleError(f"unreadable rawfile {Path(path).name}")
        keep = [i for i, name in enumerate(names) if wanted is None or name in wanted]
        data = {names[i]: array("d") for i in keep}
        columns = [(i, data[names[i]]) for i in keep]
        tokens = (token for line in handle for token in line.split())
        for _ in range(npts):
            next(tokens)  # point index
            row = [next(tokens) for _ in range(nvars)]
            for i, column in columns:
                column.append(float(row[i]))
    return data


def node(data, name, where):
    if name not in data:
        raise RuleError(f"{where}: node {name} missing from the operating point output")
    return data[name]


def check_ratings(devices, data, run_name, operating_point):
    """Return the first rating violation in one rawfile, or None."""
    for path, model, pins in devices:
        spec = ihp_netlist.PDK[model]
        v = {terminal: node(data, net, run_name) for terminal, net in pins.items()}
        points = len(next(iter(v.values())))
        limits = []
        if spec["kind"] == "mos":
            rating = ihp_netlist.MOS_RATING_V[spec["class"]]
            limits = [("gate-source", "g", "s", rating), ("gate-drain", "g", "d", rating),
                      ("drain-source", "d", "s", rating), ("gate-bulk", "g", "b", rating)]
        elif spec["kind"] == "hbt":
            limits = [("base-emitter", "b", "e", ihp_netlist.HBT_RATING_V["vbe"]),
                      ("base-collector", "b", "c", ihp_netlist.HBT_RATING_V["vbc"])]
        elif spec["kind"] == "var":
            rating = ihp_netlist.MOS_RATING_V["hv"]
            limits = [("gate-well", "g1", "w", rating), ("gate-well", "g2", "w", rating)]
        for i in range(points):
            for label, a, b, rating in limits:
                value = v[a][i] - v[b][i]
                if abs(value) > rating + TOLERANCE_V:
                    return f"{run_name}: {path} ({model}) {label} {value:+.3f} V exceeds {rating:g} V"
            if spec["kind"] == "hbt":
                vce = v["c"][i] - v["e"][i]
                if vce > ihp_netlist.HBT_RATING_V["vce"] + TOLERANCE_V:
                    return (f"{run_name}: {path} ({model}) collector-emitter {vce:.3f} V exceeds "
                            f"{ihp_netlist.HBT_RATING_V['vce']:g} V")
            if not operating_point:
                continue
            forward = ihp_netlist.JUNCTION_FORWARD_MAX_V + TOLERANCE_V
            if spec["kind"] == "mos":
                sign = 1 if spec["pol"] == "n" else -1
                for t in ("s", "d"):
                    if sign * (v["b"][i] - v[t][i]) > forward:
                        return f"{run_name}: {path} ({model}) bulk junction at {t} forward biased"
            elif spec["kind"] == "hbt" and v["bn"][i] - v["c"][i] > forward:
                return f"{run_name}: {path} ({model}) collector-substrate junction forward biased"
            elif spec["kind"] == "var" and v["bn"][i] - v["w"][i] > forward:
                return f"{run_name}: {path} ({model}) well-substrate junction forward biased"
    return None


def prepare(deck_path, cwd):
    """Check the circuit the deck includes and return its PDK devices as instantiated by the deck."""
    deck = Path(deck_path).read_text()
    include = INCLUDE.search(deck)
    dut = DUT.search(deck)
    if not include or not dut:
        raise RuleError("bench has no .include of the circuit or no XDUT ctle_28g instance")
    circuit = Path(include.group(1))
    if not circuit.is_absolute():
        circuit = Path(cwd) / circuit
    try:
        subckts = ihp_netlist.check(circuit.read_text(), TOP, TOP_PINS)
    except (OSError, ihp_netlist.NetlistError) as error:
        raise RuleError(f"netlist: {error}") from error
    ports = dut.group(1).split()
    if len(ports) != len(TOP_PINS):
        raise RuleError("bench XDUT pin count")
    init = PDK_SPICEINIT.read_text() + "\nset num_threads=1\nset filetype=ascii\n"
    (Path(cwd) / ".spiceinit").write_text(init)
    remove_rawfiles(cwd)
    return ihp_netlist.flatten(subckts, TOP, "xdut", ports)


def remove_rawfiles(cwd):
    for stale in Path(cwd).glob("signoff_*.raw"):
        stale.unlink()


def run(cmd, cwd, **kwargs):
    deck_path = Path(cmd[-1])
    if not deck_path.is_absolute():
        deck_path = Path(cwd) / deck_path
    run_name = deck_path.name
    try:
        devices = prepare(deck_path, cwd)
    except RuleError as error:
        return fail(cmd, str(error))
    result = simulate(cmd, cwd, kwargs)
    if result.returncode == TIMED_OUT:
        return result
    past_deadline = DEADLINE is not None and time.monotonic() > DEADLINE
    if past_deadline and result.returncode != 0:
        return result  # no retry after the grading deadline
    if not past_deadline and (result.returncode != 0 or NOT_CONVERGED.search(result.stdout or "")):
        # One retry with more gmin and source stepping and more iterations.
        deck = deck_path.read_text()
        if deck.count("\n.control\n") != 1:
            return result
        deck_path.write_text(deck.replace("\n.control\n", "\n" + RETRY_OPTIONS + "\n.control\n"))
        remove_rawfiles(cwd)
        result = simulate(cmd, cwd, kwargs)
        if result.returncode != 0:
            return result
    try:
        for raw in sorted(Path(cwd).glob("signoff_*.raw")):
            wanted = {net for _path, _model, pins in devices for net in pins.values()}
            violation = check_ratings(devices, read_raw(raw, wanted), f"{run_name}/{raw.stem}", "_op" in raw.stem)
            if violation:
                return fail(cmd, violation, result.stdout)
    except (RuleError, ValueError, IndexError, StopIteration) as error:
        return fail(cmd, f"ratings: {error}", result.stdout)
    return result


def simulate(cmd, cwd, kwargs):
    try:
        return subprocess.run(cmd, cwd=cwd, timeout=RUN_TIMEOUT_S, **kwargs)
    except subprocess.TimeoutExpired:
        return subprocess.CompletedProcess(cmd, TIMED_OUT, stdout=f"run stopped after {RUN_TIMEOUT_S} s\n", stderr="")


def fail(cmd, message, stdout=""):
    line = f"SIGNOFF RULE: {message}"
    VIOLATIONS.append(line)
    return subprocess.CompletedProcess(cmd, 3, stdout=(stdout or "") + "\n" + line + "\n", stderr="")


def rule_check():
    return ("signoff_rules", False, VIOLATIONS[0] + (f" (+{len(VIOLATIONS) - 1} more)" if len(VIOLATIONS) > 1 else ""))
