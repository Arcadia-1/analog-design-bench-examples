#!/usr/bin/env bash
# Grade a task's reference, empty starter, mutants and alternative designs with
# its own verifier, the way Harbor does: build both images, install each
# design at /app/circuit.spi in a fresh verifier container, run tests/test.sh.
#
#   tools/check_task.sh tasks/<task-id>            # every case
#   tools/check_task.sh tasks/<task-id> reference  # cases whose name contains "reference"
#
# Expected: reference 1, starter 0, every mutant below 1, every alternative 1.
# ENGINE selects the container engine (default: podman if present, else docker).
set -euo pipefail

task=$(cd "$1" && pwd)
filter=${2:-}
id=$(basename "$task")
engine=${ENGINE:-$(command -v podman >/dev/null 2>&1 && echo podman || echo docker)}
work=$(mktemp -d "${TMPDIR:-/tmp}/check-$id-XXXXXX")

"$engine" build -q -t "check-env:$id" "$task/environment" >/dev/null
"$engine" build -q -t "check-ver:$id" "$task/tests" >/dev/null

# The agent image must give the agent the starter, the checker and the guide.
"$engine" run --rm --network=none --user agent "check-env:$id" sh -c \
    'test -w /app/circuit.spi && test -s /app/spec/README.md && check_circuit.py --help >/dev/null \
     && test -s /opt/analog-arena/SKY130_NETLIST_GUIDE.md && command -v ngspice >/dev/null' \
    || { echo "FAIL agent image: starter, spec, checker, guide or ngspice missing"; exit 1; }

cases=("reference=$task/solution/circuit.spi" "starter=$task/environment/starter/circuit.spi")
for f in "$task"/tests/mutants/*.spi; do [ -e "$f" ] && cases+=("mutant-$(basename "$f" .spi)=$f"); done
for f in "$task"/tests/alts/*.spi; do [ -e "$f" ] && cases+=("alt-$(basename "$f" .spi)=$f"); done

status=0
for entry in "${cases[@]}"; do
    name=${entry%%=*} design=${entry#*=}
    [ -n "$filter" ] && [[ $name != *"$filter"* ]] && continue
    mkdir -p "$work/$name/logs"
    cp "$design" "$work/$name/circuit.spi"
    start=$(date +%s)
    "$engine" run --rm --network=none \
        -v "$work/$name/circuit.spi:/app/circuit.spi:ro" -v "$work/$name/logs:/logs" \
        "check-ver:$id" bash /tests/test.sh >"$work/$name/test.log" 2>&1 || true
    reward=$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["reward"])' \
        "$work/$name/logs/verifier/reward.json" 2>/dev/null || echo missing)
    case $name in
        reference|alt-*) want="== 1" ok=$(python3 -c "print(float('$reward') == 1)" 2>/dev/null || echo False) ;;
        *) want="< 1" ok=$(python3 -c "print(float('$reward') < 1)" 2>/dev/null || echo False) ;;
    esac
    [ "$ok" = True ] && verdict=ok || { verdict=WRONG; status=1; }
    printf '%-6s %-34s reward=%-8s (want %s) %4ss\n' "$verdict" "$name" "$reward" "$want" "$(( $(date +%s) - start ))"
done
echo "logs: $work"
exit $status
