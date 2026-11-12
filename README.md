# Analog Design Bench examples

Three complete Analog Design Bench tasks that show the package format every
new Analog Design Bench task follows. Write new tasks the same way: copy the
layout of one of these, keep each file's role, and meet
[`TASK_QUALITY_STANDARD.md`](TASK_QUALITY_STANDARD.md).

| Task | Circuit | What it shows |
|---|---|---|
| [`sky130-unity-gain-buffer-5mv-pvt`](tasks/sky130-unity-gain-buffer-5mv-pvt) | current-biased unity-gain buffer | complete 45-point PVT signoff, commanded-target settling, input-loading check, offline verifier tests |
| [`sky130-unregulated-charge-pump-10mhz-pvt`](tasks/sky130-unregulated-charge-pump-10mhz-pvt) | voltage-doubling charge pump with enable | a declared representative PVT set, clock and enable driven through 50 ohm, shared signoff rules |
| [`sky130-fd-sampling-feedback-ota-gain8-settling10ns-noise1mv-pvt-mc`](tasks/sky130-fd-sampling-feedback-ota-gain8-settling10ns-noise1mv-pvt-mc) | fully differential OTA with CMFB | a 30-point Cartesian PVT matrix, fixed-seed mismatch, CMFB disturbance recovery |

## Package layout

```text
tasks/<task-id>/
├── instruction.md          # a short ticket: goal, deliverable, where the spec is
├── task.toml               # Harbor 1.3, in the shape the standard gives
├── environment/            # the agent's image (build context)
│   ├── Dockerfile          # simulator image + checker layer + user `agent` owning /app
│   ├── analog-arena/       # netlist checker and Sky130 netlist guide, installed at /opt/analog-arena/
│   └── starter/            # copied to /app
│       ├── circuit.spi     # the deliverable: an empty subcircuit with the required interface
│       ├── spec/           # the complete public contract, each rule in one place
│       └── testbench/      # public development benches
├── tests/                  # the verifier's image (separate build context)
│   ├── Dockerfile
│   ├── analog-arena/       # the same checker files
│   ├── test.sh             # artifact check -> check_circuit.py -> verify.py once -> reward.json
│   ├── verify.py           # the one scoring program
│   ├── utils.py            # task-agnostic helpers
│   └── benches/            # hidden benches
└── solution/               # reference design and solve.sh (the oracle)
```

- **`instruction.md`** stays a few paragraphs. Interface, operating
  conditions, limits, measurement definitions and eligibility live in
  `environment/starter/spec/`, which the agent sees at `/app/spec/`.
- **`task.toml`** holds only the Terminal-Bench taxonomy in `[metadata]`. Status,
  maturity and review evidence go in PRs and review reports, never in the task.
- **Images** build directly on the public simulator image pinned in
  [`toolchains.toml`](toolchains.toml), which carries no checker or scoring code.
  Each task ships the checker itself in both build contexts, and both
  Dockerfiles install it at `/opt/analog-arena/`.
- **`tests/test.sh`** writes reward 0 on any early exit, runs `check_circuit.py`
  as the eligibility gate and then `verify.py` exactly once.
- **File headers** say what a file is for. Repair history, PR numbers and
  conversion notes belong in Git history, not in task files.

## Checking a task

[`tools/check_task.sh`](tools/check_task.sh) builds both images and grades the
reference, the empty starter, and every design in `tests/mutants/` and
`tests/alts/` with the task's own `tests/test.sh`, as Harbor would. It needs
Docker or Podman on a Linux host.

```bash
tools/check_task.sh tasks/sky130-unity-gain-buffer-5mv-pvt
```

The reference must score 1, the starter 0, every mutant below 1, and every
alternative design 1.

## Known gaps

These examples show the package format. They do not yet show everything the
standard asks for:

- None of the three ships `tests/mutants/` or `tests/alts/` yet.
- The charge pump grades eight representative points at a fixed 1.8 V supply,
  and does not meter the power its clock and enable inputs deliver.
- The OTA's matrix covers 1.80 and 1.98 V only, and its `vocm` input is an
  ideal source whose current is not measured.
- The buffer's public benches are the hidden benches at the nominal point.

## License

See [`LICENSE`](LICENSE).
