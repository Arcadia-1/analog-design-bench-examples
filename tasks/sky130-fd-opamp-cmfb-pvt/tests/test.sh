#!/bin/bash
set -uo pipefail
export OMP_NUM_THREADS=1
trap 'if [ ! -f /logs/verifier/reward.json ]; then mkdir -p /logs/verifier; printf "%s\n" "{\"reward\":0,\"tests_total\":23,\"tests_passed\":0,\"partial\":0.0}" > /logs/verifier/reward.json; fi' EXIT

test -s /app/circuit.spi || exit 0
/opt/analog-arena/check_circuit.py /app/circuit.spi --allow-ideal R C --enforce-sky130-geometry \
    --allow-pdk sky130_fd_pr__nfet_01v8 sky130_fd_pr__pfet_01v8 sky130_fd_pr__nfet_01v8_lvt sky130_fd_pr__pfet_01v8_lvt \
    --require-subcircuit fd_opamp vss ibias vdd vcm vinp vinn voutp voutn || exit 0
python3 /app/analog_arena_tests/node_rules.py /app/circuit.spi || exit 0
python3 /app/analog_arena_tests/verify.py
