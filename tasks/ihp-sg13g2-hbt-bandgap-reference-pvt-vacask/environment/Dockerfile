FROM ghcr.io/arcadia-1/circuit-bench-ihp-sg13g2-vacask@sha256:9d65201c47f5682971b2eea90994d79739011ba32a3e7eccce5a64897df9f561

RUN useradd --create-home --uid 1000 agent

WORKDIR /app
COPY starter/ /app/
RUN rm -f /app/Dockerfile \
    && find /app \( -name '.DS_Store' -o -name '._*' -o -name '__pycache__' \) -prune -exec rm -rf {} + \
    && for dir in /app /app/testbench /home/agent; do cp "$IHP_PDK_ROOT/libs.tech/vacask/.vacaskrc.toml" "$dir/"; done \
    && chmod 0755 /app/signoff/run_bench.py /app/signoff/check_circuit.py \
    && chown -R agent:agent /app /home/agent
CMD ["bash"]
