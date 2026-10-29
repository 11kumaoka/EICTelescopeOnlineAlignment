# EIC Telescope Alignment

An offline research package for reconstructing and aligning a seven-plane
telescope with installed EICrecon/ACTS components and official Millepede-II.

```text
npsim / Geant4 → official EICrecon Digitization → immutable RawHits
→ native ACTS tracking → official Mille Binary / Pede → Geometry Correction
→ fresh reconstruction of the same RawHits → Controller and ROOT validation
```

The public example is a 1 GeV electron beam along Global +X. Five SiMAPS planes
provide two-dimensional tracking measurements; MPGD and AC-LGAD planes remain
part of the physical Geometry. The current alignment scopes select Global Y
and, where configured, all three rotation components on SiMAPS L1/L2/L3.
This is a bounded research workflow, not a real-time calibration service.

## Quick Start

Use Docker from this checkout's root. The launcher selects a pinned
`linux/amd64` image and mounts this checkout at `/work`. Start with unused
`external/`, build and result paths:

```bash
unset TESTBENCH_CONTAINER
bash environment/run.sh python3 -B environment/setup_dependencies.py
bash environment/run.sh cmake -S /work/localDetEICrecon/experimental/telescope \
  -B /work/build/native_telescope -DCMAKE_BUILD_TYPE=Release \
  -DTELESCOPE_BUILD_MILLE_PROBE=ON
bash environment/run.sh cmake --build /work/build/native_telescope -j2
bash environment/run.sh python3 -B -m calibration.workflow run \
  --config calibration/config/native_workflow_smoke.json \
  --output-dir result/native_smoke --build build/native_telescope \
  --validation-mode diagnostic
```

This generates 64 new Geant4 events with seed `2026102511` and an L1 Global Y
displacement of +0.080 mm. No private dataset is needed. The output directory
must not exist. Read `result/native_smoke/report/summary.json`,
`report/report.md`, `report/diagnostics.root` and `run_manifest.json`.

Technical completion, convergence and physics quality are separate report
fields. The example intentionally uses offline `diagnostic` policy: recognized
high-chi2 Pede advisories remain visible and suggested error scaling is never
applied. Integrity, observability and physical Geometry checks remain mandatory.
Candidates remain unapproved and are never activated automatically.

## Documentation

| Guide | Purpose |
| --- | --- |
| [Quick Start](docs/QUICKSTART.md) | Environment, independent stages, output inspection and troubleshooting |
| [Architecture](docs/ARCHITECTURE.md) | Actual official components, local adapters and dependency flow |
| [Configuration](docs/CONFIGURATION.md) | Geometry, beam, supported parameters and Controller policy |
| [Validation](docs/VALIDATION.md) | Executed checks, scientific interpretation and reference-data boundaries |
| [Provenance](docs/PROVENANCE.md) | Curated source origin and retained/excluded inventories |
| [Statistics study](studies/alignment_statistics/README.md) | Reproduce the 12,000-event/28-cohort methodology with new data |
| [Linearized diagnostics](docs/LINEARIZED_DIAGNOSTICS.md) | Offline 18-parameter estimates, 42-parameter information rank and ROOT plots; no operational alignment-mode extension |

The runtime sources under `validation/telescope_closure/`,
`validation/telescope_alignment/` and `validation/telescope_geometry/` are
required by the Controller and build; their directory name does not make them
optional. Generated `result/`, `results/`, `build/` and `external/` contents stay
outside Git. Historical data, failed runs and development reports remain in the
preserved source repositories rather than this collaborator distribution.

Geant4 transports through configured material. ACTS fitting is zero-field and
material-free; same-data convergence does not validate material-aware physics
performance, bias or uncertainty coverage.

No project-wide license has been assigned. Review
[third-party notices](THIRD_PARTY_NOTICES.md) and the
[rights inventory](docs/RELEASE_RIGHTS.md) before distribution or publication.
