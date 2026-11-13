FROM ghcr.io/arcadia-1/circuit-bench-ihp-sg13g2-ngspice@sha256:4fb1ad94de2ca53d80f0dc6381cc0ad4069b18d82196eb3d20a0ed44dd536ee6

RUN useradd --create-home --uid 1000 agent

WORKDIR /app
COPY starter/ /app/
RUN rm -f /app/Dockerfile \
    && cp "$IHP_PDK_ROOT/libs.tech/ngspice/.spiceinit" /app/.spiceinit \
    && cp "$IHP_PDK_ROOT/libs.tech/ngspice/.spiceinit" /app/testbench/.spiceinit \
    && chmod 0755 /app/signoff/check_circuit.py /app/signoff/run_bench.py \
    && find /app \( -name '.DS_Store' -o -name '._*' \) -delete \
    && git init /app \
    && git -C /app config user.email analog-arena@example.invalid \
    && git -C /app config user.name analog-arena \
    && git -C /app add . \
    && git -C /app commit -m 'Initialize Analog Arena starter' \
    && chown -R agent:agent /app
CMD ["bash"]
