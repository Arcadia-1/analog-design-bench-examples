FROM ghcr.io/arcadia-1/circuit-bench-ihp-sg13g2-ngspice@sha256:4fb1ad94de2ca53d80f0dc6381cc0ad4069b18d82196eb3d20a0ed44dd536ee6

WORKDIR /app

COPY test.sh /tests/test.sh
COPY verify.py utils.py ihp_signoff.py ihp_netlist.py check_circuit.py /app/analog_arena_tests/
COPY benches /app/analog_arena_tests/benches
RUN chmod +x /tests/test.sh
