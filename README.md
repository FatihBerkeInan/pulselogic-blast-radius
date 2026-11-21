# Blast Radius: measured dependency selection

Original entry for the Builders Buildathon RWX track.

The entry builds per-test source-line and fixture-read maps from the unmodified
SQLGlot commit `eb11aec4be8cb2122afe713a4816662c3f0fb2fa`. At runtime it reads
patches and selects tests using those measured dependencies. Runtime edits use statement coverage when control flow is unchanged, with function
and file fallbacks. Initialization edits retain broad selection. Class and
dialect changes use references in baseline source and fixtures, including
inherited dialects and generic dialect tests. Shared class-setup reads expand
to the tests in the class. Serialization boundaries have explicit dependency
edges derived from baseline process submissions and reduction methods. Unsupported changes fall
back to all tests. Python changes with identical parsed syntax trees select
no tests, including actual comment edits but not edits inside strings.

While collecting baseline dependencies, process-pool tasks run synchronously
through an instrumented harness with pickle round trips. Static reduction-method
references also cover the serialization boundary. No pinned SQLGlot source file
is modified.

For each selected set it compares one through eight workers using the published
integer-millisecond test durations and two-second worker overhead.

## Candidate verification

The verifier runs inexpensive candidate tests against a patched source overlay.
Every patch uses a fresh interpreter; unchanged files share a read-only source
tree on Linux. A test is removed only after successful execution and module/class cleanup. Linux verifies multiple candidates in separate forked processes, including each
test's setup and cleanup. Platforms without fork retain a failed module's candidate group.
Failures, failed subtests, skips, interrupted runs and untested candidates stay
selected. An adaptive planner prices module imports, measured child CPU, passing likelihood and worker scheduling. It learns costs within each request without storing mutation outcomes.
The default per-patch wall budget is six seconds, with a 120-second overall
verification deadline. Unfinished work keeps its static selection.

Scalar class settings use measured coverage at named attribute reads, except
when the attribute is consumed during initialization or the class shape changes.
Enums, decorated classes and unsupported assignment forms retain broad selection.

`VERIFY=0` disables verification. `VERIFY_WALL` adjusts the per-patch wall budget.
Python network operations are rejected inside the verifier. The official Docker
run must still use `--network none` to enforce the restriction at the OS level.

The bundled comparison profile was generated with network access disabled in the
pinned Linux Python 3.13 image. All 1,620 baseline tests passed (one skipped),
with 25,440 passing subtests. It records raw operand relationships, never mutation
outcomes. Primitive subclasses are accepted only when both equality methods are
unchanged built-ins; custom comparison methods remain uncertain. The profile is
used only with matching Python major/minor, the correct source pin, and a clean
baseline test result. `Dockerfile.profile` reproduces it offline after dependency
installation.

Private-local renames can be recognized by compiler output. Parameter names,
bytecode, constants, closure metadata, exception handling and line mappings must
match. Reflective tests are retained, and renames involving dynamic namespace
access or potentially unbound locals are rejected.

The more aggressive CPU-calibrated verification planner remains available behind
`VERIFY_CALIBRATE=1`, but is disabled by default because its measured total cost
was worse. The current version does not yet implement query-level execution
profiles or full import-state fingerprints.

No competitor source files or precomputed maps are copied. Public design
explanations informed the comparison; the implementation and generated maps are ours. No patch hashes, patch categories, or answer
lookups are used. The selector never reads `sample/answers.json`; the separate
validation script reads it only after the selector has exited.

## Build and validate

`docker build --platform linux/amd64 -t blast-radius-entry .`

`make sample` runs the official container restrictions and official sample scorer.

`python -m unittest -v test_entry test_improvements` verifies patch reconstruction, conservative
fallbacks, dependency selection, and worker overhead behavior.

For native development, install the test dependencies plus coverage 7.17.4,
clone SQLGlot into `sqlglot/` at the pinned commit, run `python build_map.py`,
then `python validate_local.py`. Native Windows results are provisional because
the official environment is Linux Python 3.13 with pinned dependency versions.
`Dockerfile.map` regenerates dependencies in the exact official base image.
The GitHub Actions workflow runs that build, then tests the entry, and saves the
Linux map, operand profile and predictions as artifacts. Copy the validated Linux map and profile into the
repository before submitting that commit. The main Dockerfile uses the committed
map so competition builds do not repeat the expensive test suite. Maps generated
on Windows are provisional until replaced by the Linux artifact.

## Sources and pre-existing components

- Official Builders Blast Radius starter: `selector.py`, `score.py`, `Makefile`,
  `requirements.lock`, `sample/`, `SCORING.md`, and `STARTER_README.md`.
  The Dockerfile is adapted from that starter.
- SQLGlot, MIT licensed, downloaded from its official repository at the pinned commit.
- Python standard library, pytest and coverage from public package registries.

## Limits

Coverage is an approximation of impact, especially for dynamic imports,
initialization effects and subprocess execution. Zero misses on public samples
would not establish zero misses on hidden changes. Linux container build, cgroup CPU accounting and resource restrictions are checked in CI. Hidden scoring remains a separate evaluation.

Public sample baseline: 1,210.470 simulated seconds, zero missed commits.
The Linux comparison workflow runs unit checks, public samples, and fresh scoped mutation checks. Private validation reports are not runtime inputs.

The owner has authorized publication if required for competition submission.

