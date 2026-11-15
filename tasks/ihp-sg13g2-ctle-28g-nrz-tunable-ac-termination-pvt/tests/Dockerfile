FROM ghcr.io/arcadia-1/circuit-bench-ihp-sg13g2-ngspice@sha256:9bce514e0772a72ac5db6c59b6de7de5fad40623bcc1b3571f35838018377463

WORKDIR /app

COPY test.sh /tests/test.sh
COPY verify.py utils.py ihp_signoff.py ihp_netlist.py check_circuit.py /app/analog_arena_tests/
COPY benches /app/analog_arena_tests/benches
RUN chmod +x /tests/test.sh
