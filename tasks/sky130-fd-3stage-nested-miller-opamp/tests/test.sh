#!/bin/bash
set -uo pipefail
export OMP_NUM_THREADS=1
printf '%s\n' 'set num_threads=1' > /root/.spiceinit
trap 'if [ ! -f /logs/verifier/reward.json ]; then mkdir -p /logs/verifier; printf "%s\n" "{\"reward\":0,\"tests_total\":21,\"tests_passed\":0,\"partial\":0.0}" > /logs/verifier/reward.json; fi' EXIT

test -s /app/circuit.spi || exit 0
mkdir -p /logs/verifier/reports/analog-signoff
/opt/analog-arena/check_circuit.py /app/circuit.spi --allow-ideal R C --enforce-sky130-geometry \
    --require-subcircuit fd3_nmc_opamp vss ibias vdd vcm vinp vinn voutp voutn || exit 0
python3 /app/analog_arena_tests/verify.py
