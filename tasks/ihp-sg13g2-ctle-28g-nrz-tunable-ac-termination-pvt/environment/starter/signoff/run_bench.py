#!/usr/bin/env python3
"""Run a public bench under the same signoff rules the grader applies.

    python3 /app/signoff/run_bench.py /app/testbench/<bench>.spi

The included circuit is checked against the netlist rules, the bench is simulated with ngspice in its own directory
(with the PDK's .spiceinit, which loads the OSDI models), and the device voltage ratings are checked at every
operating point and transient time point the bench writes out. If the run does not converge it is repeated once with
the grader's retry options. The ngspice output is printed. A broken rule prints a `SIGNOFF RULE:` line and the script
exits with code 3; a broken rule scores the task 0 in grading. The public benches cover only the nominal point, so a
clean run here is necessary, not sufficient.
"""
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.dont_write_bytecode = True  # leave no __pycache__ in /app

import ihp_signoff  # noqa: E402  (the grader's own module, copied unchanged)


def main() -> int:
    if len(sys.argv) != 2 or sys.argv[1] in ("-h", "--help"):
        print(__doc__)
        return 2
    bench = Path(sys.argv[1]).resolve()
    with tempfile.TemporaryDirectory(prefix="bench-") as work:
        shutil.copy(bench, Path(work) / bench.name)
        result = ihp_signoff.run(["ngspice", "-b", bench.name], cwd=work,
                                 stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    sys.stdout.write(result.stdout)
    return result.returncode


if __name__ == "__main__":
    raise SystemExit(main())
