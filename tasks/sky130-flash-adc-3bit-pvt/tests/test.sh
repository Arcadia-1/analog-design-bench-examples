#!/usr/bin/env sh
set -u

export OMP_NUM_THREADS=1
printf '%s\n' 'set num_threads=1' > /root/.spiceinit
mkdir -p /logs/verifier
rm -f /logs/verifier/reward.json /logs/verifier/new-ctrf.json
trap 'if [ ! -f /logs/verifier/reward.json ]; then printf "%s\n" "{\"reward\":0,\"tests_total\":6,\"tests_passed\":0,\"partial\":0.0}" > /logs/verifier/reward.json; fi' EXIT

test -s /app/circuit.spi || exit 0
/opt/analog-arena/check_circuit.py /app/circuit.spi --allow-ideal R C --require-subcircuit flash_adc_3bit vss vdd vrefn vrefp clk vin b2 b1 b0 || exit 0
rm -f /logs/verifier/signoff-rules.txt
python3 /app/analog_arena_tests/verify.py
