# Structural refactor validation

Run label: `20261101`; final readback completed on 2026-11-02 (Asia/Tokyo).
Repository: `/Users/tkumaoka/eic/EICTelescopeOnlineAlignment`.
Feature branch: `refactor/calibration-layout`.
Baseline and unchanged `main`: `70f9ecbba8dfd766d101f79114e6adc20a98178c`.
No new commit, push, merge or Geometry activation was performed.

## Architecture and migration

See [STRUCTURE_MIGRATION.md](STRUCTURE_MIGRATION.md) for the before/after tree
and exact 49-file move table, and [structure_migration.json](structure_migration.json)
for the four module extractions. [ARCHITECTURE.md](ARCHITECTURE.md) describes every
retained module, installed component and remaining telescope-specific adaptation.
The maintained inventory has **159 files**; this includes the migration documents,
package markers and extracted runtime modules, not generated outputs.

Simulation and Digitization now share `simulation/` but remain independent
stages. Calibration coordinates `reconstruction/` and `alignment/`, with physical
Geometry operations shared under `geometry/` by preparation and correction.
Production Python imports no implementation from `validation/`. Independent
scientific fixtures and cross-checks remain there. C++ plugins remain in one
CMake project with `geometry/` and `tools/` holding required runtime code; the
interfaces, targets, upstream files and model are unchanged.

## Actual final validation

| Check | Result |
| --- | --- |
| Fresh CMake configure and complete build | PASS in `build/layout_20261101`; no inherited cache |
| Validation suite | 228 tests PASS, zero skips |
| Study suite | 36 tests PASS, zero skips; 264 baseline test functions retained |
| Consolidated regression | 17 command exits PASS; fresh standalone Geometry/one-track builds, real factory/ROOT association checks, mathematical native/legacy equivalence, official Mille/Pede fixture, unit/study checks |
| Fresh real Geant4 workflow | 64 generated; 63 accepted tracks in each fit; zero rejected; one correction; converged |
| Independent stage CLIs | prepare/simulate/digitize/calibrate/report each PASS in a separate invocation; reuses the checked immutable Simulation producer rather than generating another sample |
| Physical Geometry / navigation / serialization | Actual reloads, containment/overlap, identities, installed navigation and one-track factory readback PASS in workflow and consolidated regressions |
| Derivatives / official Binary / Pede | Mandatory gates and official decoding/profile/solution/covariance checks PASS; 63 records, 630 scalar rows per smoke fit |
| Source and Controller integrity | Final-source manifests, both checkpoints, RawHit input identities, build/library hashes and actual linkage PASS |
| CLI and syntax | 17 actual help interfaces plus real status/verify PASS; all maintained Python/JSON parse and Shell syntax PASS |
| Paths and configuration | Absolute/relative inputs resolve identically; all three public workflows and explicit parameter resources load; launcher mounts the correct checkout at `/work` |
| Historical offline diagnostics | All 446 cohorts and statistics exactly equal; entire 18D and 42D summaries equal to preserved reference summaries |
| Rank-deficient pencil beam | Four cohorts at rank 12/18, all estimates/formal errors null; 42D rank diagnostics preserved |
| ROOT readback | Every saved key/object read in eight new workflow/closure/offline ROOT files; counts below |
| Scientific source preservation | 32 C++ source/header files byte-identical; Geometry XML unchanged; 16 moved configurations identical except workflow resource paths; 39 numerical function/class ASTs identical after import/docstring/path normalization |
| Historical result preservation | All 2,291 pre-existing result files still match their original hashes |
| Git diff whitespace | `git diff --check` PASS; current classification CSV uses LF, historical exclusion CSV remains byte-identical |

### Numerical comparison

| Quantity | Validated reference | Final refactor |
| --- | ---: | ---: |
| Smoke tracks before / after | 63 / 63 | 63 / 63 |
| Smoke ACTS chi2/stored-NDF before | 5.643925909012083 | 5.643925909012083 |
| Smoke ACTS chi2/stored-NDF after | 1.2900174523156787 | 1.2900174523156787 |
| Initial L1 Global Y update [mm] | +0.078865 | +0.078865 |
| Remaining L1 Y update [mm] | +0.00000022638 | +0.00000022638 |
| 32-track L1 / L2 update [um] | +149.14 / +203.18 | +149.14 / +203.18 |
| 32-track ACTS ratio before / after | 37.16731567382813 / 0.6908491410315036 | identical |

Initial equation/event audits are exactly equal to the preserved baseline after
checking the sole expected difference: each event names its own new derivative
gate filename. Both expected gate paths, successful gate results and identical
model hashes are required before comparing every other field exactly. No
floating-point tolerance was introduced for this comparison.

### Warnings and scientific limits

Both real Pede fits return status **1**, `Ended with warnings (bad measurements)`.
The chi2/NDF values are `812.6993289610494 / 377` and
`812.6993304768503 / 377`. Rejected local-fit counts are zero throughout.
The suggested input-error scale **1.47 is not applied**. The configured formal
resolution target remains unmet and is recorded as `Selected resolution target
not met`. All warnings remain in the original logs and final diagnostic report.

Engineering integrity and convergence pass; physics quality remains
`not_validated`, approval remains `unapproved`, and activation is false.
Geant4 transport includes configured material; ACTS fitting remains zero-field
and material-free. No uncertainty-coverage or material-aware physics claim is
made. 18D/42D tools remain offline diagnostics, not operational alignment modes.
Historical checkpoints are intentionally not resumed against relocated sources.
Resume parsing and state-machine behavior are covered by the preserved tests;
no live interrupted-run recovery campaign was performed.

## Exact final commands

Run from the repository root. Each output path below was new when executed.
The checked-reuse stage commands refer to the retained first intermediate
Simulation producer, whose settings, bytes and physical poses are verified.

### configure

```bash
env -u TESTBENCH_CONTAINER bash environment/run.sh cmake -S /work/localDetEICrecon/experimental/telescope -B /work/build/layout_20261101 -DCMAKE_BUILD_TYPE=Release -DTELESCOPE_BUILD_MILLE_PROBE=ON
```

### build

```bash
env -u TESTBENCH_CONTAINER bash environment/run.sh cmake --build /work/build/layout_20261101 -j2
```

### final_units

```bash
env -u TESTBENCH_CONTAINER bash environment/run.sh env PYTHONDONTWRITEBYTECODE=1 python3 -B -m unittest discover -s validation -p 'test_*.py'
```

### final_study_units

```bash
env -u TESTBENCH_CONTAINER bash environment/run.sh env PYTHONDONTWRITEBYTECODE=1 python3 -B -m unittest discover -s studies/alignment_statistics -p 'test*.py'
```

### final_smoke

```bash
env -u TESTBENCH_CONTAINER bash environment/run.sh env PYTHONDONTWRITEBYTECODE=1 python3 -B -m calibration.workflow run --config calibration/config/workflow_smoke.json --output-dir result/layout_20261101_smoke_02 --build build/layout_20261101 --validation-mode diagnostic
```

### final_reference32

```bash
env -u TESTBENCH_CONTAINER bash environment/run.sh env PYTHONDONTWRITEBYTECODE=1 python3 -B validation/telescope_closure/closure.py --build build/layout_20261101 --result result/layout_20261101_reference32_02 --events 32 --chi2-cut 1000 --step all
```

### final_consolidated_02

```bash
env -u TESTBENCH_CONTAINER bash environment/run.sh env PYTHONDONTWRITEBYTECODE=1 python3 -B validation/telescope_closure/closure.py --build build/layout_20261101 --result result/layout_20261101_reference32_02 --events 32 --chi2-cut 1000 --validation-name final_layout --step regressions
```

### final_staged_prepare

```bash
env -u TESTBENCH_CONTAINER bash environment/run.sh env PYTHONDONTWRITEBYTECODE=1 python3 -B -m calibration.workflow prepare --config /work/calibration/config/workflow_smoke.json --output-dir /work/result/layout_20261101_staged_02 --build /work/build/layout_20261101 --validation-mode diagnostic --simulation-stage /work/result/layout_20261101_smoke_01/simulation
```

### final_staged_simulate

```bash
env -u TESTBENCH_CONTAINER bash environment/run.sh env PYTHONDONTWRITEBYTECODE=1 python3 -B -m calibration.workflow simulate --run-dir result/layout_20261101_staged_02
```

### final_staged_digitize

```bash
env -u TESTBENCH_CONTAINER bash environment/run.sh env PYTHONDONTWRITEBYTECODE=1 python3 -B -m calibration.workflow digitize --run-dir result/layout_20261101_staged_02
```

### final_staged_calibrate

```bash
env -u TESTBENCH_CONTAINER bash environment/run.sh env PYTHONDONTWRITEBYTECODE=1 python3 -B -m calibration.workflow calibrate --run-dir result/layout_20261101_staged_02
```

### final_staged_report

```bash
env -u TESTBENCH_CONTAINER bash environment/run.sh env PYTHONDONTWRITEBYTECODE=1 python3 -B -m calibration.workflow report --run-dir result/layout_20261101_staged_02
```

### offline18

```bash
docker run --rm --platform linux/amd64 -e PYTHONDONTWRITEBYTECODE=1 -v /Users/tkumaoka/eic/EICTelescopeOnlineAlignment:/work -v /Users/tkumaoka/eic/OnlineTimeAlignmentTest/result/full_18d_geant4_001:/source_study:ro -w /work/result/.environment eicweb/eic_xl@sha256:67c914295a92396040c3051360d70786df9c1ee8458264ad0b83cd1a8ccf3d05 bash -lc 'cd /work; exec "$@"' testbench python3 -B studies/alignment_statistics/fit_18d_linearized_scan.py --input /source_study/analysis/mille_first_20000_tracks.json --truth /source_study/preparation/truth_18d.json --output result/layout_20261101_linearized18
```

### offline42

```bash
docker run --rm --platform linux/amd64 -e PYTHONDONTWRITEBYTECODE=1 -v /Users/tkumaoka/eic/EICTelescopeOnlineAlignment:/work -v /Users/tkumaoka/eic/OnlineTimeAlignmentTest/result/full_18d_geant4_001:/source_study:ro -w /work/result/.environment eicweb/eic_xl@sha256:67c914295a92396040c3051360d70786df9c1ee8458264ad0b83cd1a8ccf3d05 bash -lc 'cd /work; exec "$@"' testbench python3 -B studies/alignment_statistics/analyze_full_42d_information.py --input /source_study/analysis/mille_first_20000_tracks.json --output result/layout_20261101_information42
```

### rank18

```bash
env -u TESTBENCH_CONTAINER bash environment/run.sh env PYTHONDONTWRITEBYTECODE=1 python3 -B studies/alignment_statistics/fit_18d_linearized_scan.py --input result/layout_20261101_smoke_01/controller/jobs/attempt_000/iteration_000/reconstruct/artifacts/mille.json --counts 16,32 --output result/layout_20261101_rank18
```

### rank42

```bash
env -u TESTBENCH_CONTAINER bash environment/run.sh env PYTHONDONTWRITEBYTECODE=1 python3 -B studies/alignment_statistics/analyze_full_42d_information.py --input result/layout_20261101_smoke_01/controller/jobs/attempt_000/iteration_000/reconstruct/artifacts/mille.json --counts 16,32 --output result/layout_20261101_rank42
```

### individual_plots

```bash
env -u TESTBENCH_CONTAINER bash environment/run.sh env PYTHONDONTWRITEBYTECODE=1 python3 -B studies/alignment_statistics/plot_18d_individual_cohorts.py --summary result/layout_20261101_linearized18/fit_18d_linearized_summary.json --output result/layout_20261101_individual
```

### rms_plots

```bash
env -u TESTBENCH_CONTAINER bash environment/run.sh env PYTHONDONTWRITEBYTECODE=1 root -l -b -q 'studies/alignment_statistics/plot_all_18d_scan.C("result/layout_20261101_linearized18/fit_18d_linearized.root","result/layout_20261101_rms")'
```

The complete 17-command consolidated trace is beneath
`result/layout_20261101_reference32_02/final_layout_regressions/`, with per-command
`command.json`, `stdout.log` and `exit.json`. To inspect Controller integrity:

```bash
bash environment/run.sh python3 -B -m calibration.controller status --run result/layout_20261101_smoke_02/controller
bash environment/run.sh python3 -B -m calibration.controller verify --run result/layout_20261101_smoke_02/controller
```

## Output evidence

- Fresh workflow: `result/layout_20261101_smoke_02/`.
- Reference closure and consolidated suite: `result/layout_20261101_reference32_02/`.
- Independent stage workflow: `result/layout_20261101_staged_02/`.
- Final CLI checks: `result/layout_20261101_cli_checks_final/`.
- Independent numerical/ROOT/linkage readback: `result/layout_20261101_readback_02/`.
- Historical 18D/42D reruns and plots: `result/layout_20261101_{linearized18,information42,individual,rms}/`.
- Rank-deficient examples: `result/layout_20261101_{rank18,rank42}/`.
- Outer execution receipts and before/after source/result audits:
  `/private/tmp/eic-alignment-layout-20261101/` (local diagnostics, not another repository).

| ROOT file | Saved keys read |
| --- | ---: |
| `result/layout_20261101_smoke_02/report/diagnostics.root` | 39 |
| `result/layout_20261101_reference32_02/validation/closure.root` | 42 |
| `result/layout_20261101_linearized18/fit_18d_linearized.root` | 78 |
| `result/layout_20261101_information42/information_42d.root` | 4 |
| `result/layout_20261101_individual/individual_18d_linearized.root` | 76 |
| `result/layout_20261101_rms/linearized18_rms.root` | 43 |
| `result/layout_20261101_rank18/fit_18d_linearized.root` | 59 |
| `result/layout_20261101_rank42/information_42d.root` | 4 |

The 20,000-track historical input is outside this Git repository at
`OnlineTimeAlignmentTest/result/full_18d_geant4_001/analysis/mille_first_20000_tracks.json`.
It was mounted read-only, not copied or modified. Input SHA256:
`735b1f4688615f11b94b34c531f0a085450c5fa595934687a2b4bb7d5afc7079`.
The public 64-event example requires no private dataset.

## Preserved intermediate failures

No failed output was deleted or overwritten:

1. `units_01`: stale imports/package aliases, moved config paths and a duplicated
   import prefix; corrected as relocation defects.
2. `units_02`: one dynamically formatted old workflow filename; updated without
   changing the assertion.
3. `consolidated`: direct-script Mille checker lacked the repository import path
   after decoder extraction; fixed its entry-point setup only.
4. `final_consolidated`: existing standalone build basename was correctly refused.
   Reran using the documented `--validation-name final_layout` in new directories.
5. `cli`: temporary external auditor lacked its `/work` import path; fixed only
   the auditor, with final-source CLI checks rerun separately.
6. `readback`: the external whole-event equality comparison included run-specific
   derivative-gate filenames. Investigation found exactly 63 path differences,
   no numerical differences. Final auditor explicitly verifies both paths and
   their gate/model identities before exact comparison of all other fields.

Intermediate `_01` workflows predate the final standalone-entry-point fix. They
remain source-bound historical evidence and are not resealed as current runs.
The final `_02` workflows bind the final implementation. The old temporary outer
receipt launcher was unavailable at final readback; a small replacement auditor
was placed in this task's existing temporary audit directory, not in the source
repository.

## Repository review before merge

The working tree is deliberately uncommitted. `main` and `HEAD` retain the
baseline commit; only the local feature branch and worktree changes exist.
Git reports 22 modified tracked paths, 52 old-path deletions and 62 untracked
new paths. These represent 49 file moves, four responsibility-based extractions,
two removed empty package markers, and updated documentation/inventories.
The full new source set is listed in `retained_files.txt`.

Remaining old module paths occur only in migration/baseline audit records;
production Python imports no old namespace or `validation` implementation.
Versioned `native-*` schemas/fields, `native_barrel`, C++ model/target names,
reference comparisons and historical diagnostic wording are deliberately retained.
Review the migration table, actual new files and source diff before authorizing
any commit or merge. No commit/push/merge is performed by this task. Rights
questions in `RELEASE_RIGHTS.md` remain unresolved and are not changed here.
