# Analog Design Bench: example tasks

Four example harbor tasks, each a model for writing a new Analog Design Bench task. 
An agent designs an analog circuit in `/app/circuit.spi`; a hidden verifier scores it. 
Each task directory is self-contained. 
The rules are in [`TASK_QUALITY_STANDARD.md`](TASK_QUALITY_STANDARD.md).

| Task | PDK | Simulator |
| --- | --- | --- |
| `sky130-fd-opamp-cmfb-pvt` | Sky130 | ngspice |
| `ihp-sg13g2-ctle-28g-nrz-tunable-ac-termination-pvt` | IHP SG13G2 | ngspice |
| `gf180-flash-adc-3bit-pvt-xyce` | GF180MCU | Xyce |
| `ihp-sg13g2-hbt-bandgap-reference-pvt-vacask` | IHP SG13G2 | VACASK |

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
