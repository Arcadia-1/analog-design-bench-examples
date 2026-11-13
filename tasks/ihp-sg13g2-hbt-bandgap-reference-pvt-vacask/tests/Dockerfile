FROM ghcr.io/arcadia-1/circuit-bench-ihp-sg13g2-vacask@sha256:9d65201c47f5682971b2eea90994d79739011ba32a3e7eccce5a64897df9f561

WORKDIR /app

COPY test.sh /tests/test.sh
COPY verify.py bgr_signoff.py check_circuit.py /app/analog_arena_tests/
COPY benches /app/analog_arena_tests/benches
RUN chmod +x /tests/test.sh
