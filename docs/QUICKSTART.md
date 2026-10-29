# Quick Start

## Environment and build

Run commands from the repository root with Docker installed and running.
The fixed image contains EICrecon, ACTS 45.3.0, DD4hep, Geant4/npsim,
ROOT/PyROOT, podio, the EDM packages and NumPy. The host does not need a separate
EICrecon checkout or local ROOT installation for batch work.

```bash
unset TESTBENCH_CONTAINER
bash environment/run.sh python3 -B environment/setup_dependencies.py
bash environment/run.sh cmake -S /work/localDetEICrecon/experimental/telescope \
  -B /work/build/native_telescope -DCMAKE_BUILD_TYPE=Release \
  -DTELESCOPE_BUILD_MILLE_PROBE=ON
bash environment/run.sh cmake --build /work/build/native_telescope -j2
```

The installer fetches the exact Mille/Millepede-II commits in
`environment/dependencies.json` and installs only under `external/install/`.
It refuses an existing dependency directory. Use a new checkout for a new
installation; do not reinstall over research dependencies. CMake must use a
new build directory rather than an inherited cache.

`environment/run.sh` passes arguments without shell evaluation. Its default
image is pinned by digest. An explicitly selected `TESTBENCH_CONTAINER` must
actually mount this checkout at `/work`; leave it unset for a clean setup.
Do not change image or implementation sources during a run: checkpoints bind
source, build, library, input and Geometry identities.

## One-command example

```bash
bash environment/run.sh python3 -B -m calibration.workflow run \
  --config calibration/config/native_workflow_smoke.json \
  --output-dir result/native_smoke --build build/native_telescope \
  --validation-mode diagnostic
```

The smoke configuration generates 64 electrons at 1 GeV, starting at
(249,0,0) mm along Global +X, with seed `2026102511`. It simulates the displaced
Truth Geometry and starts reconstruction from nominal Geometry. The
Controller infers corrections from reconstructed measurements, not Truth.
Use a new, whitespace-free child of `result/` for every independent run.

## Independent stages and input/output

The same operation can be run step by step. Do not run these commands into the
already completed `native_smoke` directory:

```bash
bash environment/run.sh python3 -B -m calibration.workflow prepare \
  --config calibration/config/native_workflow_smoke.json \
  --output-dir result/native_staged --build build/native_telescope \
  --validation-mode diagnostic
bash environment/run.sh python3 -B -m calibration.workflow simulate --run-dir result/native_staged
bash environment/run.sh python3 -B -m calibration.workflow digitize --run-dir result/native_staged
bash environment/run.sh python3 -B -m calibration.workflow calibrate --run-dir result/native_staged
bash environment/run.sh python3 -B -m calibration.workflow report --run-dir result/native_staged
```

| Stage | Input | Main output beneath the run directory | Success check |
| --- | --- | --- | --- |
| `prepare` | Workflow/alignment JSON, native or explicit XML | `configuration/`; `preparation/{initial.xml,truth.xml}` and real Geometry audits | Sealed preparation, stable detector identities, physical checks |
| `simulate` | Truth XML, gun settings and recorded seed | `simulation/transport/events.edm4hep.root` | Simulation inspection and completed producer provenance |
| `digitize` | Bound Simulation and physical XML | `raw/digitization/digitized.root`; `raw/projection/channels.root`; `raw/provenance.json` | Official digitization, exact Raw-only selection and payload checksums |
| `calibrate` | Immutable channels/provenance, initial XML and alignment configuration | `controller/jobs/`; checkpoints; `controller/final/candidate.xml` | Full-rank solver, derivatives, physical Geometry, unchanged channels and fresh fits |
| `report` | Completed or failed stage artifacts | `report/{summary.json,report.md,diagnostics.root}`; `run_manifest.json` | Read explicit integrity, engineering, convergence and warning fields |

Geometry Correction and corrected reconstruction are separate Controller
operations inside `calibrate`; each writes a fresh sealed stage and reloads
Geometry in a fresh process. `calibration.native_controller` also exposes
`start`, `resume`, `status`, `verify` and explicit `recover-lock` commands:

```bash
bash environment/run.sh python3 -B -m calibration.native_controller status --run result/native_smoke/controller
bash environment/run.sh python3 -B -m calibration.native_controller verify --run result/native_smoke/controller
```

Resume only an intact run with exactly the original implementation, build and
input identities. Preserve failed and partial attempts. Do not delete a lock
to bypass recovery checks; `recover-lock` requires an explicit recorded reason.

## ROOT inspection

`report/diagnostics.root` contains actual track residuals, chi2 and parameter
updates. The JSON reports preserve units, fit status and warnings. On a machine
or container with a working ROOT GUI/display, open:

```bash
root --web=off -l 'visualization/browse.C("result/native_smoke/report/diagnostics.root")'
```

Batch ROOT creation/readback does not require a GUI. The separate
`studies/alignment_statistics/compare_hit_residuals.py` compares RecHits to
actually linked SimHits using exact event/RawHit identities; see the study guide.
That Truth-based check is offline and never supplies alignment corrections.

## Troubleshooting

| Symptom | Action |
| --- | --- |
| Output already exists | Choose a new run/build path; retain old results |
| Wrong `/work` mount | Unset `TESTBENCH_CONTAINER` and use the pinned launcher |
| Missing Mille/Pede or `libMille2.so` | Confirm a successful fresh dependency installation and a fresh CMake cache |
| Zero/incomplete tracks | Inspect `execution/`, reconstruction audits, topology exclusions and CKF diagnostics; do not silently discard failed tracks |
| Pede high-chi2 advisory | Read the warning; strict mode stops, diagnostic mode permits only the recognized advisory with all integrity checks intact |
| Changed source/build/input identity | Start a new run; do not reseal an old checkpoint |
| Containment, overlaps, rank or association failure | Keep diagnostics and investigate; these are mandatory scientific/integrity gates |

The full workflow currently requires the existing 1 GeV electron seed contract.
Standalone Simulation supports additional explicit settings; that does not
establish full reconstruction support for those particles or energies.
