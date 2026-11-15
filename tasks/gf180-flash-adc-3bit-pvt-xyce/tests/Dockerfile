FROM ghcr.io/arcadia-1/circuit-bench-gf180-xyce@sha256:75603bfe5b5e76b955d7bfa0c6b5916ca477541ba9310175f6a9a6767dd85aef

WORKDIR /app

COPY test.sh /tests/test.sh
COPY verify.py adc_metrics.py xyce_signoff.py check_circuit.py /app/analog_arena_tests/
COPY benches /app/analog_arena_tests/benches
RUN chmod +x /tests/test.sh
