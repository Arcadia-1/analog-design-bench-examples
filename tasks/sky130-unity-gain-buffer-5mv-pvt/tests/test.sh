#!/usr/bin/env sh
set -eu
mkdir -p /logs/verifier
for old_result in reward.json reward.txt new-ctrf.json; do
    if [ -e "/logs/verifier/$old_result" ]; then
        mv "/logs/verifier/$old_result" "/logs/verifier/$old_result.previous.$$"
    fi
done
verifier_finished=0
write_zero_reward() {
    if [ "$verifier_finished" -eq 0 ]; then
        mkdir -p /logs/verifier
        printf "%s\n" "{\"reward\":0,\"tests_total\":6,\"tests_passed\":0,\"partial\":0.0}" > /logs/verifier/reward.json
    fi
}
verifier_pid=""
terminate_verifier() {
    if [ -n "$verifier_pid" ]; then
        kill -TERM "$verifier_pid" 2>/dev/null || true
        wait "$verifier_pid" 2>/dev/null || true
    fi
    exit 143
}
trap write_zero_reward EXIT
trap terminate_verifier INT TERM
test -s /app/circuit.spi
/opt/analog-arena/check_circuit.py /app/circuit.spi --require-subcircuit unity_gain_buffer vss ibias vdd vin vout --enforce-sky130-geometry
python3 /app/analog_arena_tests/verify.py &
verifier_pid=$!
wait "$verifier_pid"
python3 -c 'import json; from pathlib import Path; r = json.loads(Path("/logs/verifier/reward.json").read_text()); assert r["reward"] in (0, 1) and r["tests_total"] == 6'
verifier_finished=1
