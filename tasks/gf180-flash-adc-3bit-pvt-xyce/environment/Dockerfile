FROM ghcr.io/arcadia-1/circuit-bench-gf180-xyce@sha256:75603bfe5b5e76b955d7bfa0c6b5916ca477541ba9310175f6a9a6767dd85aef

RUN useradd --create-home --uid 1000 agent

WORKDIR /app
COPY starter/ /app/
RUN rm -f /app/Dockerfile \
    && find /app \( -name '.DS_Store' -o -name '._*' -o -name '__pycache__' \) -prune -exec rm -rf {} + \
    && chmod 0755 /app/signoff/run_bench.py /app/signoff/check_circuit.py \
    && chown -R agent:agent /app
CMD ["bash"]
