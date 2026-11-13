#!/usr/bin/env python3
"""Run a public bench under the same signoff rules the grader applies.

    python3 /app/signoff/run_bench.py testbench/<bench>.spi

The bench's circuit is rewritten exactly as in grading (`set ngbehavior=hsa` model binning, xschem source/drain
geometry where the netlist leaves it out, expanded multipliers, the res_xhigh_po temperature coefficient, device voltage
ratings and stored-energy accounting) and simulated with ngspice. The ngspice output is printed. A broken rule prints a
`SIGNOFF RULE:` line and the script exits with code 3; a broken rule scores the task 0 in grading. The public benches
cover only the nominal point, so a clean run here is necessary, not sufficient.
"""
import os
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.dont_write_bytecode = True  # leave no __pycache__ in /app
os.environ.setdefault("SIGNOFF_RULES_FILE", str(Path(os.environ.get("TMPDIR", "/tmp")) / "signoff-rules.txt"))

import signoff_ngspice  # noqa: E402  (the grader's own module, copied unchanged)

# The bench helpers and these files are not the circuit under test.
signoff_ngspice.NOT_THE_CIRCUIT = signoff_ngspice.NOT_THE_CIRCUIT + (f"{HERE}/", "/app/testbench/")


def main() -> int:
    if len(sys.argv) != 2 or sys.argv[1] in ("-h", "--help"):
        print(__doc__)
        return 2
    bench = Path(sys.argv[1]).resolve()
    before = set(bench.parent.glob(".signoff-*"))
    try:
        # An absolute deck path: signoff_ngspice finds the deck relative to this process, not to cwd.
        result = signoff_ngspice.run(["ngspice", "-b", str(bench)], cwd=bench.parent,
                                     stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    finally:
        for copy in set(bench.parent.glob(".signoff-*")) - before:  # the rewritten deck and circuit
            copy.unlink(missing_ok=True)
    sys.stdout.write(result.stdout)
    return result.returncode


if __name__ == "__main__":
    raise SystemExit(main())
