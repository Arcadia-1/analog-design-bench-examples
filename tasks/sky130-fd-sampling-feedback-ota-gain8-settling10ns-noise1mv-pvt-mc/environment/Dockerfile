FROM ghcr.io/arcadia-1/circuit-bench-sky130-ngspice@sha256:2da03dc7f1ead7ddc33f2f930a0487ae8618e51518009344584a98fca41a621d

COPY analog-arena/ /opt/analog-arena/
ENV PATH="/opt/analog-arena:${PATH}"
ENV SKY130_PDK_SUBCIRCUITS=/opt/analog-arena/sky130_pdk_subcircuits.txt
RUN chmod 0755 /opt/analog-arena/check_circuit.py

RUN useradd --create-home --uid 1000 agent

WORKDIR /app
COPY starter/ /app/
RUN rm -f /app/Dockerfile \
    && (command -v git >/dev/null 2>&1 || (apt-get update && apt-get install -y git && rm -rf /var/lib/apt/lists/*)) \
    && find /app \( -name '.DS_Store' -o -name '._*' \) -delete \
    && git init /app \
    && git -C /app config user.email analog-arena@example.invalid \
    && git -C /app config user.name analog-arena \
    && git -C /app add . \
    && git -C /app commit -m 'Initialize Analog Arena starter' \
    && chown -R agent:agent /app
CMD ["bash"]
