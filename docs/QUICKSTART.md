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
  -B /work/build/telescope -DCMAKE_BUILD_TYPE=Release \
  -DTELESCOPE_BUILD_MILLE_PROBE=ON
bash environment/run.sh cmake --build /work/build/telescope -j2
```

The installer fetches the exact Mille/Millepede-II commits in
`environment/dependencies.json` and installs only under `external/install/`.
It refuses an existing dependency directory and uses the same Fortran compiler
for both projects: `FC` if set inside the container, otherwise GFortran. Use
`--fortran-compiler` to select another compatible compiler explicitly. This
avoids mixing compiler-specific Fortran modules; OpenMP remains enabled.

CMake, reconstruction, Pede and runtime provenance share one prefix selection:

1. `MILLEPEDE_PREFIX`, if set (relative paths are relative to the checkout root).
2. `external/current`, if present, for a locally selected replacement.
3. `external/install`, the default for a fresh clone.

An incomplete selection fails; it never silently falls back to an older library.
`external/` is ignored by Git. Do not commit dependency binaries or local paths.
For an alternate installation, use a new workspace and pass the same override to
both configuration and execution:

```bash
bash environment/run.sh python3 -B environment/setup_dependencies.py \
  --workspace external/replacement
export MILLEPEDE_PREFIX=external/replacement/install
# Repeat the configure/build commands above and keep the override for runtime.
```

The launcher forwards `MILLEPEDE_PREFIX` into Docker. Prefer checkout-relative
paths so the selection works with the container mount at `/work`. Remove the
override with `unset MILLEPEDE_PREFIX`. Do not reinstall over research dependencies.
For a fresh setup use a new build directory. After an intentional dependency
switch, explicitly reconfigure and rebuild; do not reuse sealed run outputs.
Dependencies must be built against the ROOT in the selected container; a binary
built for ROOT 6.38 is not a replacement for one built for ROOT 6.40.

`environment/run.sh` passes arguments without shell evaluation. Its default
image is pinned by digest. An explicitly selected `TESTBENCH_CONTAINER` must
actually mount this checkout at `/work`; leave it unset for a clean setup.
Do not change image or implementation sources during a run: checkpoints bind
source, build, library, input and Geometry identities.

## One-command example

```bash
bash environment/run.sh python3 -B -m calibration.workflow run \
  --config calibration/config/workflow_smoke.json \
  --output-dir result/calibration_smoke --build build/telescope \
  --validation-mode diagnostic
```

The smoke configuration generates 64 electrons at 1 GeV, starting at
(249,0,0) mm along Global +X, with seed `2026102511`. It simulates the displaced
Truth Geometry and starts reconstruction from nominal Geometry. The
Controller infers corrections from reconstructed measurements, not Truth.
Use a new, whitespace-free child of `result/` for every independent run.

## Independent stages and input/output

The same operation can be run step by step. Do not run these commands into the
already completed `calibration_smoke` directory:

```bash
bash environment/run.sh python3 -B -m calibration.workflow prepare \
  --config calibration/config/workflow_smoke.json \
  --output-dir result/calibration_staged --build build/telescope \
  --validation-mode diagnostic
bash environment/run.sh python3 -B -m calibration.workflow simulate --run-dir result/calibration_staged
bash environment/run.sh python3 -B -m calibration.workflow digitize --run-dir result/calibration_staged
bash environment/run.sh python3 -B -m calibration.workflow calibrate --run-dir result/calibration_staged
bash environment/run.sh python3 -B -m calibration.workflow report --run-dir result/calibration_staged
```

| Stage | Input | Main output beneath the run directory | Success check |
| --- | --- | --- | --- |
| `prepare` | Workflow/alignment JSON, generated barrel or explicit XML | `configuration/`; `preparation/{initial.xml,truth.xml}` and real Geometry audits | Sealed preparation, stable detector identities, physical checks |
| `simulate` | Truth XML, gun settings and recorded seed | `simulation/transport/events.edm4hep.root` | Simulation inspection and completed producer provenance |
| `digitize` | Bound Simulation and physical XML | `raw/digitization/digitized.root`; `raw/projection/channels.root`; `raw/provenance.json` | Official digitization, exact Raw-only selection and payload checksums |
| `calibrate` | Immutable channels/provenance, initial XML and alignment configuration | `controller/jobs/`; checkpoints; `controller/final/candidate.xml` | Full-rank solver, derivatives, physical Geometry, unchanged channels and fresh fits |
| `report` | Completed or failed stage artifacts | `report/{summary.json,report.md,diagnostics.root}`; `run_manifest.json` | Read explicit integrity, engineering, convergence and warning fields |

Geometry Correction and corrected reconstruction are separate Controller
operations inside `calibrate`; each writes a fresh sealed stage and reloads
Geometry in a fresh process. `calibration.controller` also exposes
`start`, `resume`, `status`, `verify` and explicit `recover-lock` commands:

```bash
bash environment/run.sh python3 -B -m calibration.controller status --run result/calibration_smoke/controller
bash environment/run.sh python3 -B -m calibration.controller verify --run result/calibration_smoke/controller
```

Resume only an intact run with exactly the original implementation, build and
input identities. Preserve failed and partial attempts. Do not delete a lock
to bypass recovery checks; `recover-lock` requires an explicit recorded reason.

## ROOT inspection

`report/diagnostics.root` contains actual track residuals, chi2 and parameter
updates. The JSON reports preserve units, fit status and warnings. On a machine
or container with a working ROOT GUI/display, open:

```bash
root --web=off -l 'visualization/browse.C("result/calibration_smoke/report/diagnostics.root")'
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
