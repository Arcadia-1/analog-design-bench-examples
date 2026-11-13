#!/usr/bin/env python3
"""Netlist check for the IHP SG13G2 CTLE: `check_circuit.py <circuit.spi>`; exit 0 when the rules in
/app/spec/signoff-rules.md (Netlist) hold, 1 with the first violation otherwise."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import ihp_netlist  # noqa: E402
import ihp_signoff  # noqa: E402

if len(sys.argv) != 2 or sys.argv[1] in ("-h", "--help"):
    print(__doc__)
    sys.exit(0 if len(sys.argv) == 2 else 2)
try:
    ihp_netlist.check(Path(sys.argv[1]).read_text(), ihp_signoff.TOP, ihp_signoff.TOP_PINS)
except (OSError, ihp_netlist.NetlistError) as error:
    print(f"check_circuit: FAIL: {error}")
    sys.exit(1)
print("check_circuit: OK")
