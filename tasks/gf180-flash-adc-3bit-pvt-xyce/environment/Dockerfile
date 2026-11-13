FROM ghcr.io/arcadia-1/circuit-bench-gf180-xyce@sha256:8beddf99cfc3cd89fb14a3f0d86b03b59e5300934d88c85e5ac73ed1813967bd

RUN useradd --create-home --uid 1000 agent

WORKDIR /app
COPY starter/ /app/
RUN rm -f /app/Dockerfile \
    && find /app \( -name '.DS_Store' -o -name '._*' -o -name '__pycache__' \) -prune -exec rm -rf {} + \
    && chmod 0755 /app/signoff/run_bench.py /app/signoff/check_circuit.py \
    && chown -R agent:agent /app
CMD ["bash"]
