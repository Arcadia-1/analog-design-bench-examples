FROM ghcr.io/arcadia-1/circuit-bench-gf180-xyce@sha256:8beddf99cfc3cd89fb14a3f0d86b03b59e5300934d88c85e5ac73ed1813967bd

WORKDIR /app

COPY test.sh /tests/test.sh
COPY verify.py adc_metrics.py xyce_signoff.py check_circuit.py /app/analog_arena_tests/
COPY benches /app/analog_arena_tests/benches
RUN chmod +x /tests/test.sh
