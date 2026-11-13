# Analog Design Bench: example tasks

Three complete [Harbor](https://github.com/laude-institute/harbor) tasks, each a
model for writing a new Analog Design Bench task. An agent designs a Sky130
circuit in `/app/circuit.spi`; a hidden ngspice verifier scores it. Each task
directory is self-contained. The rules are in
[`TASK_QUALITY_STANDARD.md`](TASK_QUALITY_STANDARD.md).

- [`tasks/sky130-ota-5t-gain40-pm60-noise50uv-pvt`](tasks/sky130-ota-5t-gain40-pm60-noise50uv-pvt/): five-transistor OTA
- [`tasks/sky130-flash-adc-3bit-pvt`](tasks/sky130-flash-adc-3bit-pvt/): 3-bit flash ADC
- [`tasks/sky130-gilbert-cell-mixer-2p4ghz-pvt-mismatch`](tasks/sky130-gilbert-cell-mixer-2p4ghz-pvt-mismatch/): 2.4 GHz Gilbert-cell mixer

## Run

With Harbor 0.25.0 (`uv tool install "harbor==0.25.0"`) and Docker. The
reference must score 1 and the untouched starter 0:

```bash
harbor run --agent oracle --path tasks/<task>
harbor run --agent nop --path tasks/<task>
```

A benchmark run: mini-swe-agent, 100 steps, no cost limit, API key and endpoint
in `.env`:

```bash
harbor run --agent mini-swe-agent --model '<provider>/<model>' \
  --agent-kwarg 'config={"agent": {"step_limit": 100, "cost_limit": 0}}' \
  --agent-kwarg cost_limit=0 --env-file .env --path tasks/<task>
```

## License

Copyright (c) 2026 Arcadia-1. All rights reserved. No license is granted: this
repository may be copied, run, modified or used to write new tasks only by
people Arcadia-1 has authorized, for the purposes it has authorized.
