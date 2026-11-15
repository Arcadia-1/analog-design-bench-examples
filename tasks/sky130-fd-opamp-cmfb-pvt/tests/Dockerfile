FROM ghcr.io/arcadia-1/circuit-bench-sky130-ngspice@sha256:ab4433f6ff4c26dd1730eddb663e5efe8fbb2a7832a637164d079655fa91fddc

COPY analog-arena/ /opt/analog-arena/
ENV PATH="/opt/analog-arena:${PATH}"
ENV SKY130_PDK_SUBCIRCUITS=/opt/analog-arena/sky130_pdk_subcircuits.txt
RUN chmod 0755 /opt/analog-arena/check_circuit.py

WORKDIR /app
COPY test.sh /tests/test.sh
COPY verify.py utils.py node_rules.py /app/analog_arena_tests/
COPY benches /app/analog_arena_tests/benches
RUN chmod +x /tests/test.sh
