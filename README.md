# Analog Design Bench: example tasks

Three complete Analog Design Bench tasks, kept as models for anyone writing a new
one. In each task an agent designs a transistor-level Sky130 circuit in
`/app/circuit.spi` and a hidden ngspice verifier scores it. The rules every task
must meet are in [`TASK_QUALITY_STANDARD.md`](TASK_QUALITY_STANDARD.md), and
[`reviews/`](reviews/) holds the review report of each example against them.

| Task | Circuit | What it shows |
|---|---|---|
| [`sky130-ota-5t-gain40-pm60-noise50uv-pvt`](tasks/sky130-ota-5t-gain40-pm60-noise50uv-pvt/) | Five-transistor OTA | Small-signal specs over a PVT grid; loop gain by Middlebrook injection; CMRR and PSRR at the DC operating point set by feedback |
| [`sky130-flash-adc-3bit-pvt`](tasks/sky130-flash-adc-3bit-pvt/) | 3-bit flash ADC | Mixed-signal timing; code transitions plus sine SNDR/SFDR over PVT; metered clock, reference and input power |
| [`sky130-gilbert-cell-mixer-2p4ghz-pvt-mismatch`](tasks/sky130-gilbert-cell-mixer-2p4ghz-pvt-mismatch/) | Gilbert-cell mixer | RF conversion gain, isolation, spurs and two-tone IIP3 over PVT, imbalance and mismatch; metered RF and LO drive power |

## What a task contains

A task is a [Harbor](https://github.com/laude-institute/harbor) task directory:

| Path | Purpose |
|---|---|
| `instruction.md` | Short ticket the agent reads first |
| `task.toml` | Harbor metadata: taxonomy, agent user, timeouts, resources |
| `environment/` | The agent's image. `starter/` becomes `/app`: the empty `circuit.spi`, the contract in `spec/`, public nominal benches in `testbench/`, and `signoff/run_bench.py`, which runs a public bench under the grader's own signoff rules |
| `tests/` | The hidden verifier, built as a separate image: `test.sh` (fails closed), `verify.py`, hidden benches, the signoff rules |
| `tests/mutants/` | Reference copies with one deliberate defect each; every one must score below 1 |
| `tests/alts/` | Different correct designs; every one must score 1 |
| `solution/` | The reference design and `solve.sh`, which installs it |

Hidden benches use conditions and stimuli the public benches do not show, so
passing the public benches is necessary but not sufficient.

## Checking a task

On a Linux host with podman or docker, bash and python3:

```bash
tools/check_task.sh tasks/sky130-ota-5t-gain40-pm60-noise50uv-pvt
```

It builds both images from the task directory, checks that the agent image
holds the starter, the spec, the netlist checker and ngspice, and that the
public signoff copies match the verifier's, then grades the reference, the
empty starter, every mutant and every alternative the way Harbor does. It
expects reference 1, starter 0, mutants below 1 and alternatives 1. A second
argument filters the cases by name. It does not parse `task.toml` or run an
agent; use Harbor for that.

`toolchains.toml` pins the simulator image every task builds on and the Harbor
release (`uv tool install "harbor==0.25.0"`). Agent runs use mini-swe-agent with
a 2-hour timeout, a 100-step limit and no cost limit.

## License

All rights reserved; no license is granted. Only people Arcadia-1 has
authorized may use this repository, including to write new tasks. See
[`LICENSE`](LICENSE).
