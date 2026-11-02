# Architecture

## Workflow and implementation boundaries

```text
simulation/geant4.py                 installed npsim / DD4hep / Geant4
  → simulation/digitization.py       TelescopeDigitization / SiliconTrackerDigi
  → immutable EventHeader + TelescopeChannels RawHits
calibration/workflow.py
  → calibration/controller.py       supervised iterations and checkpoints
      → geometry/operations.py      actual Geometry, containment/navigation gates
      → calibration/reconstruction/operations.py
          → EICrecon TelescopeTracking → ACTS states and measurements
          → TelescopeMille → official Mille Binary and equation audit
      → calibration/alignment/derivatives.py   actual-surface finite differences
      → calibration/alignment/operations.py    official Pede and consistency gates
      → geometry/operations.py                candidate XML correction + reload
      → reconstruct the identical RawHits with the corrected Geometry
      → convergence.py / policy.py → unapproved candidate and sealed reports
  → JSON / Markdown / ROOT diagnostics
validation/ and studies/             verification and offline scientific studies
```

Calibration is the overall workflow. Reconstruction provides tracks, measured
local coordinates and actual ACTS states; Alignment estimates the configured
shared Geometry corrections from those equations. Calibration supervises
subprocesses, preserves provenance, evaluates convergence and applies quality
policy. It introduces no information into the alignment fit. Successive fits
on the same RawHits are not independent experiments.

## Maintained modules

| Module | Responsibility |
| --- | --- |
| `simulation/geant4.py` | Recorded gun/transport configuration, npsim execution, simulation inspection and producer provenance |
| `simulation/digitization.py` | Official Digitization and independent Raw-only projection; simulated inputs only |
| `geometry/prepare.py` | Construct the existing barrel-compatible telescope XML resources in fresh result directories |
| `geometry/operations.py` | Validate real sensor identities, displace Truth Geometry during preparation, apply inferred corrections and reload candidates |
| `geometry/rotations.py`, `transforms.py` | Existing sensitive-center finite SO(3) serialization, matrix conventions and pose checks |
| `geometry/safety.py` | Exact approved canonical-layout and preserved-clearance Air-envelope contract |
| `calibration/workflow.py` | Public prepare/simulate/digitize/calibrate/report stages and artifact index |
| `calibration/controller.py` | Bounded iterations, source/build/input identity, seals, checkpoint chains, start/resume/status/verify |
| `calibration/runner.py`, `queue_manager.py` | Supervised process groups, exclusive run lock and recovery protection |
| `calibration/convergence.py`, `policy.py` | Unchanged convergence gates; distinguish engineering integrity, physics quality and approval |
| `calibration/reconstruction/operations.py` | EICrecon invocation, plugin environment, RawHit payload checks and reconstruction provenance |
| `calibration/alignment/operations.py`, `pede.py` | Exact official Pede steering/execution, parsing and independent solution/covariance checks |
| `calibration/alignment/mille.py` | Official Mille decoding and four-local-parameter profiling |
| `calibration/alignment/sensors.py`, `modes.py`, `config/` | Explicit physical labels, detector identities, supported parameter selections and XML identity checks |
| `calibration/alignment/information.py`, `observability.py` | Read-only profiled information, rank, nuisance/gauge diagnostics and runtime numerical gates |
| `calibration/alignment/derivatives.py` | Required finite-difference gate using actual DD4hep/ACTS Geometry perturbations |
| `environment/` | Pinned Docker launcher and official external dependency setup |
| `studies/alignment_statistics/` | Offline independent-cohort statistics, Hit Residual analysis and linearized information diagnostics |
| `visualization/browse.C` | ROOT/TBrowser inspection; workflow ROOT summaries remain in `workflow.py` |

## C++ plugins and official components

The physical C++ directory remains
`localDetEICrecon/experimental/telescope/`. Keeping a single CMake project
preserves plugin loading, shared headers, ACTS/EICrecon include paths and
upstream licensing/provenance. It is not an additional Python workflow.

| C++ sources / targets | Logical owner and actual role |
| --- | --- |
| `TelescopeDigitization.cc` / `TelescopeDigitization.so` | Simulation: register installed `SiliconTrackerDigi_factory` for the three detector readouts |
| `TelescopeTracking.cc`, `algorithms/`, `factories/` / `TelescopeTracking.so` | Reconstruction: installed `TrackerHitReconstruction`, adapted official measurement/CKF wrappers, local telescope seed, installed `ActsToTracks` |
| `alignment/NativeMilleModel.{cc,h}`, `alignment/TelescopeMille.cc` / `TelescopeMille.so` | Alignment: observe actual ACTS states/surfaces, evaluate the existing local/global equations and write official Mille records |
| `geometry/BarrelMetadata.cc` / `TestbenchBarrelMetadata` | Real detector-layer metadata for installed DD4hep conversion |
| `tools/TelescopeChannels.cc` | Runtime Geometry/sensor probe and synthetic-channel fixture generator; retains its established executable interface |
| `tools/TelescopePhysicalGeometryAudit.cc`, `TelescopeNavigationAudit.cc` | Mandatory real-solid containment/overlap and installed ACTS navigation checks |
| `tools/TelescopeSerializationAudit.cc` | Runtime/integration verification of CellID, SurfaceMap and measurement serialization |
| `tools/TelescopeMilleDerivatives.cc` | Mandatory analytical/finite-difference derivative gate before inference |

Installed EICrecon Geometry services, `ActsGeometryProvider`,
`convertDD4hepDetector`, `TrackingGeometry` and `SurfaceMap` are reused.
The installed ACTS numerical CKF, Navigator, filtering and smoothing are reused;
there is no replacement provider, navigator or fitter. Nine copied/adapted
EICrecon sources retain their original headers, hashes and patches in
`provenance.json`, `upstream_diffs/`, `NOTICE.md` and `LICENSE.LGPL3`.
The local seed and state observer remain telescope-specific. The `experimental`
path and `NativeMilleModel` C++ names are retained stable interfaces, not extra
implementations. Geant4 models material transport; ACTS fitting stays zero-field
and material-free. No material-aware accuracy claim follows from closure.

## Validation ownership

| Retained validation location | Category |
| --- | --- |
| `validation/test_*.py` | Unit and safety/integrity tests; all baseline assertions retained |
| `validation/telescope_alignment/check.py` | Independent official Mille/Pede comparison CLI; imports the maintained Binary decoder |
| `validation/telescope_alignment/{TelescopeMilleFixture,LegacyTelescopeCrosscheck}.cc` | Synthetic solver fixture and independent mathematical reference |
| `validation/telescope_closure/closure.py`, `rotation.py`, `multi_sensor.py` | Synthetic scientific closure drivers, injection fixtures and independent validation gates |
| `validation/telescope_closure/{bias,containment,multi_preflight}.py` | Independent residual, containment and observability/preflight cross-checks |
| `validation/telescope_factories/*.py` | Factory/input fixture, integration readback and upstream-source audit |
| `validation/telescope_geometry/` | Independent Geometry probe/check and standalone CMake regression |
| `validation/telescope_one_track/` | Standalone one-track integration executable/CMake regression |

Production Python modules do not import `validation`. The production CMake
project still builds explicit validation executables for mathematical
cross-checks; normal execution uses the runtime tools listed above. Validation
may import production utilities to exercise them, but it retains its distinct
Truth fixtures and independent checks. `localDetEICrecon/alignment/src/` is kept
only for the independent legacy mathematical reference; it supplies no old
tracking/backend workflow.

## Scientific and provenance contract

Truth displacements occur only in preparation/Simulation. The Controller
receives RawHits, initial Geometry and explicit parameter configuration, never
Truth poses. Corrections retain Global Y translations and finite left-Global
SO(3), composed about each sensitive-surface center. The actual XML is reloaded,
and identity, physical containment, navigation, derivatives, rank, associations
and solver agreement must pass before further reconstruction.

All original Raw ROOT bytes remain immutable across iterations. A source path
move intentionally changes source identity. Fresh runs record the new paths and
hashes; historical manifests/checkpoints are never rewritten or resealed to
appear compatible. Engineering success, physics quality and Geometry approval
remain separate. Candidates are never activated by this package.

See [the exact migration map](STRUCTURE_MIGRATION.md),
[the dependency graph](dependency_map.json) and
[validation evidence](VALIDATION.md). Operational 18D/42D fitting remains excluded.
