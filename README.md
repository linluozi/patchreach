# patchreach

**Does your benchmark actually execute the lines your patch changed?** `patchreach` reads a unified diff, runs a Python workload under a CPython line tracer, and emits a diff-scoped reach certificate: for every added executable line it reports how many `LINE` events fired, whether the line was reached at all, and whether it was *hot* (reached at least `--hot-count` times). The result is a JSON certificate with a content-addressed SHA-256 and a hard pass/fail gate. An optional `--time` mode adds an Amdahl upper bound from `cProfile` cumtime, which can falsify an over-claimed speedup.

Upstream: none

This is an original measurement tool, not a fork or a patch to another project. It makes **no speedup claim**; it measures whether a benchmark reaches the code a patch changes.

## Why this exists

A performance patch can be real and still be untested by the benchmark that is supposed to justify it. The common failure modes are:

- the patch adds a fast path for an input shape the benchmark never produces;
- the patch changes a helper that the benchmark imports but never calls;
- the benchmark's timing improves because of an unrelated change in the same patch;
- the "changed lines" include comments and blank lines, so a naive coverage ratio is misleading.

Generic line coverage (`coverage.py`) tells you what a *test suite* executed. It does not slice by diff, does not count per-line executions, does not gate on a hot threshold, and does not tie a claimed speedup to the time share of the changed functions. `patchreach` does exactly that, with an explicit denominator: added lines in the diff **intersected with** the lines the interpreter can report (`code.co_lines()`).

## Install and run

Requires Python 3.10+ (developed and recorded on CPython 3.12.3; the `sys.settrace` fallback is present but not exercised here — see *Limitations*).

```bash
git clone https://github.com/linluozi/patchreach.git
cd patchreach
python3 -m pip install -e .          # optional: provides the `patchreach` console script
python3 -m unittest discover -s tests -v
```

Run the bundled negative control (a patch with a fast path the workload never reaches):

```bash
python3 examples/demo/make_patches.py
python3 -m patchreach check \
  --patch examples/demo/patch.diff \
  --root examples/demo \
  --script workload_squares.py \
  --min-reach 0.8 --min-hot 0.5
# exit 1: GATE: FAIL — reach 0.5000 < min_reach 0.8000
```

Run the bundled positive control (both changed functions are exercised):

```bash
python3 -m patchreach check \
  --patch examples/demo/patch.diff \
  --root examples/demo \
  --script workload_both.py
# exit 0: GATE: PASS
```

Command shape:

```
python3 -m patchreach check --patch PATCH --root ROOT (--script FILE | --module MOD) [-- args...]
python3 -m patchreach explain --patch PATCH --root ROOT
```

Exit codes: `0` pass (or not-applicable), `1` gate fail, `2` usage/config error, `3` workload error, `4` inconclusive (truncated trace or uncompilable changed Python).

## What is certified

For each added line in the diff:

1. **Executable?** The patched file is compiled and its `co_lines()` are read. Comments, blank lines, and lines that exist only in bytecode metadata are excluded from the denominator. A pure deletion has no new line to trace.
2. **Reached?** At least one `LINE` event on that line during the workload.
3. **Hot?** At least `--hot-count` events (default 10).
4. **Attributable?** With `--time`, a second run under `cProfile` computes the cumtime share of the *root changed functions*. `ceiling_upper = 1 / (1 - share)` is an upper bound on any overall speedup that could come from those functions. A `--claim` above that bound is rejected. The bound is deliberately loose (cumtime includes unchanged callees); it can falsify a claim, not prove one.

The certificate digest covers the diff, per-line hit counts, thresholds, and the gate result. Volatile fields (`elapsed_s`, `events`, `time_ceiling`) are excluded, so the same diff + same workload produces the same digest across runs.

## Evidence (measured on 2026-11-21)

Environment: AMD EPYC 7K62 (4 vCPU visible), 3,723 MB RAM, Linux 6.8.0-124-generic, CPython 3.12.3, git 2.43.0. Tracer: `sys.monitoring` `LINE` events (tool id allocated dynamically). All numbers below come from the recorded runs; `hits` means `LINE` events on the changed lines.

### A. Negative control: cold fast path in the same patch

`examples/demo/patch.diff` adds two independent fast paths: `sum_squares` (exercised) and `normalize` (not exercised by `workload_squares.py`).

| demo | patch | workload | added | exec | reached | hot | hits | reach | gate |
|---|---|---|---:|---:|---:|---:|---:|---:|---|
| A | full | `workload_squares.py` | 6 | 4 | 2 | 2 | 400 | 0.5000 | **FAIL** |
| B | full | `workload_both.py` | 6 | 4 | 4 | 4 | 520 | 1.0000 | **PASS** |

Demo A's cold lines are exactly `pkg/impl.py:16` and `:17` in `normalize`. The non-executable changed lines are 2 (the two added comments), which is why `added=6` but `exec=4`.

### B. Falsifying an over-claimed speedup

`examples/demo/patch_cold.diff` changes only `normalize`. The workload never calls it.

| demo | patch | workload + flags | added | exec | reached | cumtime share | ceiling | gate |
|---|---|---|---:|---:|---:|---:|---:|---|
| C | cold-only | `workload_squares.py --time --claim 1.2 --min-reach 0 --min-hot 0` | 3 | 2 | 0 | 0.0000 | **1.000x** | **FAIL** |

Reason: `claim 1.2000x > measured upper bound 1.0000x from cumtime share 0.0000`.

### C. Real-code positive control: stdlib `fnmatch.translate`

`examples/real_fnmatch/make_fixture.py` reads the local CPython `fnmatch.py`, adds a semantics-preserving literal fast path to `translate`, and writes the patched copy plus a unified diff. The workload translates five literal and four wildcard patterns, 200 times each.

| patch | changed lines | exec | reached | hot | hits | reach | gate |
|---|---:|---:|---:|---:|---:|---:|---|
| `fnmatch` literal fast path | 2 | 2 | 2 | 2 | 4,600 | 1.0000 | **PASS** |

`hits=4,600` is not a call count: the `if` line hosts a generator expression, so it produces two `LINE` events per call (1,800 calls → 3,600 events), and the fast-path `return` produces one per literal (1,000). This is exactly what the certificate reports: line events, not function calls.

Differential correctness of the demo patch: **2,011 unique patterns** (2,000 generated with `random.Random(20261121)` + 11 edge/real patterns, set-deduplicated), **0 byte differences** against the unpatched `translate`. Corpus SHA-256: `796906f50975bf33351f9e9cce7e9214e6d0eb5948514689233dd40aee997930`. Reproduce with `python3 examples/real_fnmatch/verify_equivalence.py`.

### D. Tests

```bash
python3 -m unittest discover -s tests -v
# Ran 35 tests in 0.770s — OK (0 failures)
```

Coverage of the suite: 13 diff-parser tests (add/modify/multi-hunk/new/deleted/rename/no-newline/quoted/CRLF/malformed/mode-only), 6 source-analysis tests (comment/blank exclusion, nested/lambda spans, innermost lookup), 10 certificate tests (pass/fail, cold line, non-executable denominator, claim ceiling, cold ceiling, digest stability, error, truncation, non-Python skip, uncompilable inconclusive), and 6 end-to-end CLI tests (negative control, positive control, cold claim, explain, usage error, real `fnmatch` positive control). The `fnmatch` test is skipped if the local stdlib file is absent.

## Quality gates (G1–G5)

This repository does not patch an upstream library and does not claim a speedup, so **G1–G5 are not applicable**. They are not replaced with fabricated numbers. The equivalent evidence is:

| check | result | reproduce |
|---|---|---|
| tests | 35 passed / 0 failed | `python3 -m unittest discover -s tests -v` |
| negative control (cold line) | reach 0.5000, gate FAIL | demo A command above |
| positive control (both hot) | reach 1.0000, gate PASS | demo B command above |
| claim falsification | 1.2x > 1.0x ceiling, gate FAIL | demo C command above |
| real-code reach | 2/2 changed lines hot, gate PASS | `python3 examples/real_fnmatch/make_fixture.py && python3 -m patchreach check --patch examples/real_fnmatch/work/patch.diff --root examples/real_fnmatch/work --script workload.py --min-reach 1.0 --min-hot 1.0` |
| real-code differential | 2,011 unique inputs, 0 mismatches | `python3 examples/real_fnmatch/verify_equivalence.py` |
| certificate stability | identical SHA-256 on two runs of demo A | run demo A twice with `--json` and compare `certificate_sha256` |

## Reproduce (three commands)

```bash
python3 -m unittest discover -s tests -v
python3 examples/demo/make_patches.py && python3 -m patchreach check --patch examples/demo/patch.diff --root examples/demo --script workload_both.py
python3 examples/real_fnmatch/make_fixture.py && python3 examples/real_fnmatch/verify_equivalence.py && python3 -m patchreach check --patch examples/real_fnmatch/work/patch.diff --root examples/real_fnmatch/work --script workload.py --min-reach 1.0 --min-hot 1.0
```

The first demo (negative control) exits 1 by design; see the evidence table for its exact command.

## Environment

- CPU: AMD EPYC 7K62 48-Core Processor, 4 vCPU visible (`lscpu`)
- RAM: 3,723 MB total (`free -m`)
- Kernel: 6.8.0-124-generic (`uname -r`)
- Python: 3.12.3 (GCC 13.3.0), single interpreter
- git: 2.43.0
- Tracer: `sys.monitoring` `LINE` events (3.12); `sys.settrace` fallback for older interpreters
- Differential seed: `random.Random(20261121)`
- No PyPy, no 3.10/3.11 on this machine: **cross-interpreter runs were not possible and are not claimed**. The fallback path exists in code but was not exercised by the recorded runs.

## 本轮创新

Existing tooling answers two different questions: `coverage.py` answers "which lines did the test suite execute?", and the team's earlier tools answer "is the wall-clock number real?" (`speedgap`: synthetic vs real load), "are the samples independent?" (`samplemeter`, `diffcert`), "where did the GC time go?" (`gcphase`), "what is the per-call allocation peak?" (`peakgate`), or "what did the interpreter specialise?" (`deoptscope`). None of them answers the more basic question for a performance patch: **did this benchmark execute the specific lines the patch added, and how many times?**

`patchreach` makes that question a first-class, diff-scoped certificate:

- the denominator is `added lines ∩ co_lines()`, so comments/blank lines and pure deletions never masquerade as "uncovered";
- the output is per-line `LINE` counts with a hot threshold, not a binary coverage map;
- an optional `--time` mode turns the cumtime share of the changed functions into an Amdahl **upper bound**, so a claim like "1.2x" can be rejected outright when the changed function is cold (measured here: share 0.0000, ceiling 1.000x);
- the JSON certificate has a content-addressed SHA-256 that excludes volatile timing fields, so two runs of the same diff/workload agree.

What it overturns: the bundled negative control is a concrete counterexample to the "same patch, so the speedup is attributable to all of it" assumption. A patch that adds two fast paths can pass a naive timing benchmark while one of them is never executed; `patchreach` measures the cold line at 0 hits and fails the gate. The real-code control shows the opposite, positive case on an unmodified stdlib function: 2 changed lines, 4,600 line events, 0 differential mismatches on 2,011 unique patterns.

Why this is not a re-skin of the ledger: `diffcert`/`samplemeter` validate the *inputs* of a differential harness; `patchreach` validates the *reach* of a performance workload. They are complementary, and the README keeps the distinction explicit.

## Limitations (honest boundaries)

- **Python only.** Non-Python changed files are reported and excluded from the denominator; a changed Python file that cannot be compiled makes the gate `inconclusive`, never a silent pass.
- **LINE events, not call counts.** A source line that also hosts a generator/comprehension can emit more than one event per call (observed: 2× on the `fnmatch` `if` line). The certificate is about line reach, not function-call counts.
- **`--time` runs the workload twice**, so side effects repeat. The ceiling uses root changed functions' cumtime, which includes unchanged callees: it is an upper bound, useful for falsification, not proof.
- **Single-threaded workloads.** The callback shares a dict without locks; concurrent threads are not synchronised.
- **C extension lines are invisible.** Only Python `LINE` events are traced.
- **In-process child.** A workload that calls `os._exit` bypasses the report; the parent then reports a tracer error rather than a pass.
- **The `fnmatch` patch is a demo, not an upstream submission.** It is applied to a local copy of the stdlib file at runtime; the stdlib source is not redistributed. The 2,011-pattern differential certifies the demo patch, not a claim about upstream `fnmatch` performance.
- **No cross-interpreter claim.** Only CPython 3.12.3 is present on this machine.

## License

MIT. See `LICENSE`. The `examples/real_fnmatch/` scripts read the local CPython `fnmatch.py` at runtime (PSF-licensed); no stdlib source is committed to this repository.
