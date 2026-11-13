# Analog Arena Task Quality Standard

This document defines the repository-wide quality bar for a formal Analog Arena task. It defines task quality, not a repair, image-publication, or Vela-launch workflow. Task-specific electrical limits and all other public requirements are stated in each task's `instruction.md` and the specification documents it points to.

## Checklist before submitting

Every item must hold. The sections below explain each one.

- [ ] The reference scores 1 and the untouched starter 0 (`harbor run --agent oracle` and `--agent nop`).
- [ ] `tests/mutants/` holds reference copies with one defect each; every one scores below 1, and every scored check is failed by at least one mutant.
- [ ] `tests/alts/` holds at least one correct design with a different topology or sizing; it scores 1.
- [ ] Limits are natural engineering targets, not the reference's worst case rounded outward, and the reference passes each with margin.
- [ ] Claimed PVT or Monte Carlo coverage is the complete declared product; a representative subset is stated as such in the spec.
- [ ] Hidden benches use conditions or stimuli the public benches do not show.
- [ ] Every external input (clock, bias, reference, signal source) has a finite source impedance, and the power it delivers is metered.
- [ ] Open-loop gain, CMRR and PSRR benches set the operating point by DC feedback.
- [ ] Settling and recovery targets are commanded values, never the submitted design's own output.
- [ ] `check_circuit.py` runs before simulation and a failure scores 0; nothing recognizes or scores topology.
- [ ] `tests/test.sh` calls `verify.py` once; an early exit, crash or empty submission scores 0.
- [ ] A complete grading run finishes within 15 minutes.
- [ ] `instruction.md` is a short ticket; interface, conditions, limits and measurement definitions are in `spec/`, each rule once.
- [ ] `task.toml` has the shape below (taxonomy-only `[metadata]`, agent user `agent`, no network) and parses with Harbor 0.25.0.
- [ ] Task files carry no repair history, PR numbers or conversion notes.

## Core principles

A formal task must provide all four of the following:

1. **A useful scenario.** The measured behavior represents a meaningful analog, mixed-signal, RF, power, or circuit-design capability.
2. **A reasonable task.** The interface, operating conditions, named topology or abstraction, and specifications form a physically coherent design problem.
3. **A fair evaluation.** Legal implementations that meet the published contract pass on electrical behavior without reproducing or exposing the reference's private internal connectivity.
4. **Complete tests.** The final verifier measures every capability needed to support the task's claims, at the declared operating conditions.

Task review follows the maintainers' review guide. Before promotion, a
candidate must be compared with the existing tasks. A deliberate variant must
state the externally measurable capability that distinguishes it from existing
tasks.

## Reference solution

- The reference must implement the named task honestly and must not rely on a verifier loophole, untested operating condition, simulator artifact, or contradictory public and hidden fixtures.
- The reference must have finite, reproducible evidence for the claimed behavior. A final hidden-signoff result, when it is supplied, is strong evidence; its absence is not by itself a release, repair, or Vela-launch gate.
- The reference topology must be physically meaningful at the abstraction level claimed by the task. Ideal components and omitted effects are allowed only when the abstraction remains useful and is stated clearly.
- Development agents choose how many simulations to run. They are never required to replay the full hidden signoff during development.

## Specifications and calibration

- Electrical limits must be natural engineering targets: values associated with the application, a standard performance class, a familiar ratio, or another concise design requirement. Prefer values such as 40 dB gain, 100 MHz bandwidth, or 50 dB rejection when they express the intended capability. Do not derive conspicuously precise limits by rounding a reference's worst result just enough to pass.
- The reference proves that the published targets are achievable and must retain a stable reproduction margin. Its measured result informs feasibility and regression risk; it does not define the specification edge.
- ADC-style and other system-derived limits may be derived from resolution, sampling rate, error budget, application requirements, or another explicit system contract instead of the reference edge. The derivation must be stated.
- Every scored metric must measure the intended capability and distinguish a correct design from a plausible shortcut. Redundant metrics are acceptable only when they close a distinct loophole or make a distinct failure observable.
- A physically better result must not fail because it lies beyond a sweep boundary or because of incorrect interpolation, censoring, polarity, or sign handling.
- An open-loop gain, CMRR or PSRR measurement must set the amplifier's DC operating point with DC feedback (a very large inductor from the output to the inverting input with the AC drive through a large capacitor, or a low-pass DC servo), never by biasing every input from a source. Otherwise a systematic or mismatch offset multiplied by the open-loop gain pushes the output toward a rail, and a higher-gain design reads worse.
- Settling and recovery measurements must use the real commanded target or another externally defined target. A submitted output tail must not define its own target.

## Coverage and test cost

- Coverage must match the task's claim. A task that claims complete Sky130 PVT coverage must actually cover the complete declared Cartesian product; for the common five-corner, three-supply, three-temperature matrix this is 45 unique combinations, not 15 paired points.
- A representative subset is allowed when it is technically justified and described honestly. No task is required to use a fixed number of runs merely for uniformity.
- Every relevant dimension must be covered. Depending on the task this may include DC polarity and range, AC stability and bandwidth, both transient directions, startup and recovery, load and input range, noise, distortion, Monte Carlo, PVT, or state-sequence behavior.
- Reuse one simulation result for multiple metrics when possible. Combine analyses or benches when doing so preserves independence, numerical robustness, and fair evaluation. Do not multiply ngspice runs only to mirror the report structure.
- The verifier timeout (`[verifier] timeout_sec = 3600`) is only a watchdog. Tests should still be reduced whenever feasible; wall-clock duration is never an electrical score.
- A routine complete verifier run must finish within 15 minutes. Shorter is better when it costs no coverage; this limit is never part of electrical scoring.
- Wall-clock time is diagnostic evidence for test cost only. It must never be an electrical performance score.
- Hidden coverage is required. If the public benches are the same as the final test, agents can tune against the grader and the task has no hidden coverage; this is a coverage defect, not a style issue.
- One frequency point is enough for PSRR, noise, or a similar metric when the task does not claim a band.

## Verifier fairness and robustness

- Every hard score must come from a published electrical or simulation metric measured by a testbench. A topology name may describe the design problem, but topology recognition must never contribute to pass/fail or partial credit. The sole exception is an explicitly labeled fixed-topology sizing or parameter-optimization task: it may use a separate public eligibility gate that compares against the shipped baseline, provided the fixed instances/connectivity and every editable parameter are stated publicly and gate failure yields zero before simulation.
- Public development benches must provide directly runnable examples for every scored capability and its measurement definition. They use the same published interface, metric semantics, and physical assumptions as final signoff, but select distinct, complementary diagnostic scenarios rather than reproducing the complete hidden signoff. Hidden benches provide the declared full coverage; public benches remove ambiguity about how to develop and measure.
- Every task must run `check_circuit.py` before electrical simulation. This is a mandatory submission-eligibility gate, not an optional diagnostic. A submission that fails it receives zero reward and must not proceed to ngspice.
- The checker may and should parse netlist text and leaf-device types to enforce the published submission contract: allowed SPICE syntax, PDK models and subcircuits, task-permitted primitive classes, finite and process-legal parameters, the required top-level interface, safe include paths, and the absence of forbidden control/analysis/model directives or ideal/behavioral/controlled sources unless explicitly allowed.
- The repository-wide device checker must not recognize or require a particular circuit topology, instance naming, private nodes, hierarchy, bias lineage, or internal connectivity. Exact counts, dimensions, and passive budgets may be checked only when they are explicit public submission constraints rather than a hidden preferred implementation. A fixed-topology sizing task may add its own public contract checker under the narrow exception above; that checker must not be reused as a hidden topology preference in ordinary design tasks.
- For submissions that pass `check_circuit.py`, acceptance and partial credit come from published finite electrical measurements. The mandatory legality gate and the electrical score serve different purposes and must not be conflated.
- Partial credit is allowed; a 0/1 reward is not required. The score must be monotonic: a better design must never score lower. An empty submission, a crash, or a failed simulation scores 0.
- Boundary, polarity, monotonicity, coverage, and representative electrically bad designs should be tested wherever those failure modes could create a false pass or false rejection. Ship these bad designs with the task. Each must score below full reward; it need not score exactly zero.
- Ship mutants in `tests/mutants/`: copies of the reference with one deliberate defect each (a swapped connection, a wrong device type, a missing branch, a mis-sized bias). Each must score below full reward. For tasks where the agent writes a testbench or checker, its deliverable must catch every mutant and still pass the reference.
- Ship at least one alternative correct design in `tests/alts/`: a design that meets the public contract with a different topology or sizing from the reference. Each must get full reward. This proves the grader does not reward only the reference's own implementation. Mutants and alternatives stay in the task's `tests/` folder for review and are not copied into the verifier image.
- ngspice does not provide a reliable PSS/PNoise or intrinsic-MOS transient-noise flow for oscillator signoff. Oscillator phase noise, phase-noise FoM, and flicker-noise corner must not be hard-scored from transient FFT results.
- External clocks should normally use a finite 50 ohm source resistance so abnormal drive current and clock-delivery energy remain observable. Any exception must preserve an equivalent electrical guardrail.
- The same applies to external reference voltages and bias inputs: give them a finite source impedance and measure the current they deliver, so a design cannot draw unmetered power from an ideal source.

## Reference physical plausibility

- Review the reference solution for finite, process-valid dimensions, sensible finger counts, current density, matching intent, parasitics, and layout plausibility.
- This is a reference-quality review, not a submission-scoring mechanism. The verifier must not parse or score submitted `L`, `W`, `nf`, multiplicity, device counts, or passive budgets.

## Contract consistency and repository content

- `instruction.md`, starter material, hidden verifier, and reference solution must describe the same current contract.
- Every released formal task uses one positive verifier entrypoint: `tests/test.sh -> check_circuit.py -> tests/verify.py -> simulator/checks -> reward.json`. `test.sh` performs artifact handoff, the mandatory legality gate, and zero-reward fallback before invoking `verify.py` once. `verify.py` owns simulation orchestration, finite-measurement handling, acceptance, and reward/report writing.
- `runner.py`, `test_runner.py`, a separate signoff/scoring executable, a raw/CSV/JSON handoff between separate verifier programs, and a private `tests/verifier.toml` are forbidden. Put task constants in the single verifier and keep publicly meaningful limits aligned with `instruction.md`. `utils.py` may contain only shared, task-agnostic execution and result-writing helpers.
- `instruction.md` is short, like a ticket or hand-off note: the goal, the deliverable and its path, and where the specification is. A few paragraphs, not a full datasheet.
- The detailed public specification (interface, operating conditions, limits, measurement definitions, permitted abstractions) lives in specification documents shipped to the agent, for example `environment/starter/spec/*.md`. Each rule the grader enforces must appear in the instruction or in these documents, in exactly one place.
- `task_spec.toml`, duplicated copies of the specification, and instruction generators are forbidden.
- Public wording must distinguish complete coverage from representative coverage and must state material test abstractions or exclusions.
- Task files describe only the current task, current calibration, and current known limitations. Repair history, previous defects, PR numbers, worker transcripts, and before/after narratives belong in Git history, PRs, or external audit reports, not in the task directory.

## Harbor 1.3 packaging and isolation

`task.toml` follows this shape. Values may change when a task needs it; fields not shown are omitted.

```toml
schema_version = "1.3"
artifacts = ["/app/circuit.spi"]

[task]
name = "analog-arena/<task-id>"
description = "<one sentence>"
authors = [{ name = "<author>" }]

[metadata]
category = "Hardware, Embedded, and Low-Level Systems"
subcategory = "Analog and mixed-signal circuit design"
task_objective = ["Implement", "Validate"]   # Implement, Fix, Test, Validate
artifact_type = ["Hardware or firmware artifact"]

[agent]
timeout_sec = 7200.0
user = "agent"

[verifier]
timeout_sec = 3600.0
user = "root"
environment_mode = "separate"

[verifier.environment]
network_mode = "no-network"
build_timeout_sec = 900.0
cpus = 4
memory_mb = 8192
storage_mb = 10240

[environment]
network_mode = "no-network"
build_timeout_sec = 900.0
cpus = 4
memory_mb = 8192
storage_mb = 10240
workdir = "/app"
```

- `[metadata]` holds only the four taxonomy fields above. Do not add maturity levels, maturity notes, display titles or descriptions, checker flags, or other repository-private keys; status and evidence belong in PRs and audit reports, and checker flags belong in `tests/test.sh`.
- The agent runs as an unprivileged user (`agent`, uid 1000) that owns `/app`; the verifier runs as root. The environment image creates that user.
- Benchmark runs use mini-swe-agent with a 2-hour agent timeout (`[agent] timeout_sec = 7200`) and a limit of 100 steps (one model call is one step), with no cost limit. Harbor's `task.toml` has no step field, so the step limit is set when the run starts, and only this command gives a comparable run. Use Harbor 0.25.0 (`uv tool install "harbor==0.25.0"`); `<provider>/<model>` is a LiteLLM model name, and the model's API key and endpoint come from `.env` (never write a key into a command or a file in the repository). Other agents and settings are not benchmark runs.

  ```bash
  harbor run \
    --agent mini-swe-agent \
    --model '<provider>/<model>' \
    --agent-kwarg 'config={"agent": {"step_limit": 100, "cost_limit": 0}}' \
    --agent-kwarg cost_limit=0 \
    --env-file .env \
    --path <task-directory>
  ```

- Every `task.toml` must parse with the pinned upstream Harbor `harbor.models.task.config.TaskConfig`; a repository-written checker does not replace that parser. The Harbor release is 0.25.0. Validation records the exact Harbor commit and version.
- Upstream schema failures and Analog Arena policy failures are reported separately as `upstream_schema_error` and `repository_policy_error`.
- `environment/` and `tests/` are independent Docker contexts and both must build on their own. The verifier must not inherit the agent image.
- Use open-source tools and PDKs that anyone can obtain. Pinning tool or base-image versions is not required. Do not depend on a site-private server, service, or locally built image. Sky130 tasks build on the public simulator image `ghcr.io/arcadia-1/circuit-bench-sky130-ngspice`, pinned by digest in each Dockerfile.
- The agent environment contains only `environment/starter/` and other explicitly public material. Hidden benches, verifier implementation, reference circuits, and golden fixtures must not enter that image.
- The tests context contains the hidden verifier but no reference/golden solution. It receives only declared artifacts at runtime. Repository-level verifier self-tests that need a golden belong outside the submitted-design scoring path.
- Analog Arena declares the agent's deliverable directly, for example `artifacts = ["/app/circuit.spi"]`. Harbor transfers a declared artifact to the same path in the fresh verifier container. Use a `verifier.collect` hook only when a task genuinely needs to snapshot runtime state that is not already a deliverable file.
- Both agent and verifier use `network_mode = "no-network"` (not the deprecated `allow_internet`) and declare the build timeout, CPU, memory, and storage they need. Omit default OS, GPU, MCP, and empty-environment fields unless the task actually requires them.
- `solution/solve.sh` and `tests/test.sh` are executable. The oracle must install the reference into the same `/app` location an agent edits, and the independent verifier must award reward 1 using the declared artifact at that same path.
- Release evidence comprises exact upstream parsing, repository profile auditing, and the task-relevant implementation evidence available for the current change. Docker builds, image publication, and oracle/golden execution are separate operations, not default prerequisites.

## Not required

These are not quality requirements and must not be raised as fix items:

- difficulty calibration against frontier models (solve rate, saturation);
- clean-room sourcing; tasks may come from papers, public repositories, or contests;
- anti-contamination canary strings;
- human-written instructions, or any README in the task;
- task or directory naming rules;
- a red-team pass with the full package; the shipped counterexamples are enough;
- pinned tool or image versions;
- separate reference netlists for a design task and its repair variant.

## Release interpretation

A task is release-ready when it satisfies this standard and the available current evidence supports the reference and verifier contract. Audit findings may be classified as:

- `pass`: no change is required for release;
- `minor_fix`: a localized correction is required without redesigning the task;
- `major_fix`: reference validity, coverage, fairness, calibration, topology, or cost needs substantive work;
- `reject`: the premise or evaluation cannot be made useful and fair without replacing the task.
