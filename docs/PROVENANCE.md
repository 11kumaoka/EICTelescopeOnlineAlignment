# Provenance

For the structural-refactor layout, see [STRUCTURE_MIGRATION.md](STRUCTURE_MIGRATION.md). The 149-file counts and dated tables below describe the original curated handoff; the current source inventory is `retained_files.txt`. Historical provenance is not rewritten.

## Source chain

This curated candidate derives from local `EICTelescopeOnlineAlignment` initial
commit `0dc01e0`. That import came from
`11kumaoka/OnlineTimeAlignmentTest`, local release branch
`release/native-calibration-20261027`, validated commit
`19bb1e6bfeff63df3b3f84610cc5c021b59ab0b2`.

The original development repository, isolated release worktree, initial import,
historical datasets, failures and external archive remain preserved separately.
This candidate has a new empty Git repository and does not inherit their Git
history. No initial commit, push, publication, merge or Geometry activation is
performed before candidate review and acceptance.

The Controller records an unborn HEAD explicitly during pre-commit validation
and still binds the complete maintained source-set content and actual build/
runtime libraries. Once an approved initial commit exists, the same provenance
record additionally includes its real Git identity. An absent commit identity
does not authorize source changes or relaxed checkpoint verification.

The candidate contains **149 maintained files**: 132 paths retained from the
376-file import, ten initial guides/inventory/test paths, and seven offline
diagnostic/validation/guide additions ported on 2026-10-28. It omits 244 imported
paths without deleting them from the original repositories. Exact inventories:

- [Retained files](retained_files.txt): every distributed path, including this inventory.
- [Excluded files](excluded_files.csv): all 244 omitted paths, source hashes and reasons.
- [Full classification](source_classification.csv): all imported and new paths,
  classified A runtime, B build/dependency, C configuration, D scientific
  validation/documentation, E omitted historical material, or F unresolved.
  No unresolved F files remain.
- [Dependency map](dependency_map.json): local Python imports, CMake sources,
  C++ includes, dynamic parameter maps and subprocess programs.

| Directory | Maintained files |
| --- | ---: |
| Repository root | 5 |
| `alignment/` | 2 |
| `calibration/` | 11 |
| `digitization/` | 2 |
| `docs/` | 11 |
| `environment/` | 3 |
| `geometry/` | 5 |
| `localDetEICrecon/` | 35 |
| `simulation/` | 2 |
| `studies/` | 11 |
| `validation/` | 61 |
| `visualization/` | 1 |
| **Total** | **149** |

The final validation record identifies executed commands and evidence. Historical
reports and unused scenarios are omitted from the collaborator tree, not
deleted from their source repositories. Numerical models, equations, covariance,
Pede settings, physical conventions and scientific assertions are preserved.

Source adaptation is limited to explicit unborn-repository provenance, removing
the consolidated driver's old +Z/private-archive checks while retaining native
safety/scientific tests, and extracting four existing mathematical helper
functions from three modules. Each retained helper is byte-identical to its
source definition. Native C++, model/fit code, numerical operations, Geometry
template, alignment JSON and official-source patches remain byte-identical.
The generated-data ignore rule no longer advertises the omitted legacy input
delivery README. Lifecycle tests use a test-only legacy activation sentinel
instead of importing the excluded service; their activation-forbidden and
checkpoint assertions remain intact. No scientific assertion or selection
threshold was relaxed.

## Offline diagnostic port (2026-10-28)

The original development checkout's 363 pre-release source paths remained
byte-identical to the saved release-preparation snapshot. Its four dirty native
files were already represented by the curated implementation and are not
copied back over the validated Geometry, provenance and covariance fixes.
Eight additional source tools were found in ignored `result/full_dof_tools/`.
They have no Git commit identity; the full source hashes for the five imported
tools are recorded in the classification inventory.

| Original tool | Curated disposition |
| --- | --- |
| `fit_18d_linearized_scan.py` | Offline 18-parameter diagnostic; numerical Schur solve retained, explicit input/Truth and statistical-display guards added |
| `analyze_full_42d_information.py` | Offline normalized information rank only; no 42-parameter fit |
| `plan_full_dof_scan.py` | Pure cohort planner; no simulation, selection or alignment execution |
| `plot_18d_individual_cohorts.py`, `plot_all_18d_scan.C` | ROOT presentation of diagnostic results; singleton variance remains undefined |
| `run_real_18d_scan.py`, `run_18d_iterations.py` | Deferred with user approval: experimental 18-label serialization and XYZ/SO(3) updates outside the validated Controller |
| `preflight_full_dof.py` | Not imported: private-study/build defaults and overlapping capability diagnostics; retained tools accept explicit input files |

The five imported tools live under `studies/alignment_statistics/`, with one
focused test module and one concise guide. Old quick-start reports, partial
Truth examples, backups, caches and datasets were not imported. The previously
excluded `plot_alignment_scan.C` remains outside this distribution. The empty
untracked `python3` and `tail` files in the original checkout remain untouched;
their origin is unconfirmed.

No fitter, native C++, alignment JSON, Pede steering, official Mille writer,
Geometry Correction or Controller mode changes accompany this port. Offline
18-parameter estimates and a 42-column rank calculation do not establish new
operational alignment support. See [the diagnostic guide](LINEARIZED_DIAGNOSTICS.md)
for input provenance and scientific limitations.

## Official-source provenance

The native plugin retains nine files from EICrecon commit
`195966d41899800eaeca6fbafd63cd53735e6543`. Original paths/hashes and exact local
patches are recorded in:

```text
localDetEICrecon/experimental/telescope/provenance.json
localDetEICrecon/experimental/telescope/upstream_diffs/
localDetEICrecon/experimental/telescope/NOTICE.md
localDetEICrecon/experimental/telescope/LICENSE.LGPL3
```

The installed official Geometry services, ACTS provider, SurfaceMap,
TrackingGeometry, Navigator and numerical CKF are reused. They are not locally
vendored replacement implementations. [Architecture](ARCHITECTURE.md) lists the
remaining telescope adapters. The independent legacy Mille writer/model is
retained only where required for meaningful mathematical cross-checks.

## Pinned environment and dependencies

| Component | Source identity |
| --- | --- |
| Docker | `eicweb/eic_xl@sha256:67c914295a92396040c3051360d70786df9c1ee8458264ad0b83cd1a8ccf3d05` |
| Installed EICrecon | `195966d41899800eaeca6fbafd63cd53735e6543` |
| ACTS | 45.3.0 |
| DD4hep | 1.36 |
| Mille | `cec2e880ecaab3d255e08e21c8f70f248c6098d8` |
| Millepede-II | `7e3f4f57d993e0e0098d1a40da72823ac9c3207b` |

`environment/dependencies.json` is the external source lock. Each new run
records actual executable paths/hashes, package-version declarations, generator
sources, invocation, build/cache identities, resolved dynamic libraries,
configuration snapshots, full and selected input checksums and Geometry chain.
Container pinning is not a substitute for inspecting those run-specific records.

## Distribution boundary

Generated ROOT/Mille data, logs, results, builds, dependency installations,
credentials and backups are outside Git. The historical 28-cohort dataset is
not a public input dependency. New data reproduce the documented methodology,
not the exact private historical sample or its selection outcomes.

No project-wide license has been chosen. Existing upstream headers, notices,
license text, hashes and patches are retained. Technical validation does not
resolve project/contributor permissions or upstream license-scope ambiguities;
see [third-party notices](../THIRD_PARTY_NOTICES.md) and
[rights review](RELEASE_RIGHTS.md).
