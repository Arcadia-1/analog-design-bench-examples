FROM ghcr.io/arcadia-1/circuit-bench-sky130-ngspice@sha256:2da03dc7f1ead7ddc33f2f930a0487ae8618e51518009344584a98fca41a621d

COPY analog-arena/ /opt/analog-arena/
ENV PATH="/opt/analog-arena:${PATH}"
ENV SKY130_PDK_SUBCIRCUITS=/opt/analog-arena/sky130_pdk_subcircuits.txt
RUN chmod 0755 /opt/analog-arena/check_circuit.py

ENV PATH="/opt/ngspice/bin:${PATH}"

WORKDIR /app

COPY test.sh /tests/test.sh
COPY verify.py utils.py /app/analog_arena_tests/
COPY benches /app/analog_arena_tests/benches
RUN chmod +x /tests/test.sh
