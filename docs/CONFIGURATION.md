# Configuration

## Public examples

Use `calibration.workflow run --config FILE --output-dir NEW_RUN --build BUILD`
inside the fixed Docker launcher. `prepare` accepts the same initialization
options and later stages use the immutable `--run-dir` snapshot.

| Configuration | Generation and intended use |
| --- | --- |
| `calibration/config/native_workflow_smoke.json` | 64-event pencil beam, seed 2026102511, L1 Y=+0.080 mm; engineering smoke |
| `calibration/config/native_workflow_example.json` | 256-event broad beam, seed 2026102512, same single-L1 Y example |
| `calibration/config/native_workflow_multi.json` | 2304-event broad beam, seed 2026102503; L1 Y/Rx/Ry/Rz and L2 Y; also the basis for the explicitly overridden 12,000-event study |

These public configurations are distinct from internal parameter-map fixtures
under `validation/telescope_alignment/`. Do not infer
support for an arbitrary configuration merely because JSON can represent it.

## Workflow schema

The exact schema is `native-workflow-v1`; unknown keys are refused.

| Section | Fields and units |
| --- | --- |
| `geometry` | `source`: `native_barrel` or explicit XML; `initial`: `nominal` or explicit XML; optional `truth` XML, otherwise `global_y_mm` and `global_rotation_vector_rad` keyed by L1/L2/L3 |
| `simulation` | Explicit integer `events` and positive 31-bit `seed`; `particle`, `energy_gev`, `beam`, `position_mm`, `direction`; optional `vertex_sigma_mm`, `theta_range_rad`, `phi_range_rad` |
| `alignment` | Exact supported parameter `config` path and finite positive `chi2_cut` |
| `controller` | `validation_mode`: `strict` or `diagnostic`; `maximum_corrections`: 1–3 |
| `environment` | `kind`: `fixed-docker`; pinned `image` digest |

An explicit Truth XML and injected displacements are mutually exclusive.
`--geometry`, `--initial-geometry`, `--alignment-config`, `--events`, `--seed`,
`--beam`, `--particle`, `--energy-gev`, `--position-mm`, `--direction`,
`--vertex-sigma-mm`, `--theta-range-rad`, `--phi-range-rad`,
`--validation-mode` and `--max-iterations` override a new run's settings.
`--max-iterations` maps to the maximum number of applied Geometry corrections,
plus a final fresh evaluation fit. Overrides are recorded; they cannot change
an existing run's frozen configuration.

The complete reconstruction workflow currently requires `e-` at 1 GeV because
the existing electron seed prior is fixed. Standalone `simulation.native`
supports a wider explicit gun contract; that is not additional validated
tracking support.

## Detector Geometry and beam

Native preparation reads the protected nominal resource
`geometry/nominal/seven_layer_telescope.xml`, then generates a separate real
barrel-compatible XML using installed `DD4hep_SiTrackerBarrel`. There are seven
physical planes at Global X=250,300,…,550 mm, with five SiMAPS planes followed
by MPGD and AC-LGAD. Each sensor has 100×100 mm transverse area. Geometry
identities, readouts, materials, segmentation and sensitive dimensions are
audited; none is inferred from layer order alone.

The gun defaults to (249,0,0) mm directed along Global +X. A pencil beam has zero
default vertex width. The broad recipe uses independent Gaussian vertex widths
(0,12,10) mm, uniform theta in pi/2±0.08 rad and phi in ±0.08 rad. Explicit
overrides are checked against the declared recipe and recorded in producer
provenance. Geant4 uses configured material and transport; no ACTS material map
is created or used.

Truth displacements are active Global transformations about each current
sensitive-surface center. The alignment correction uses:

```text
c_new = c_current + (0, dY, 0)                  # mm
R_new = Exp([dRx, dRy, dRz]) R_current          # radians, left Global composition
```

Euler values are only an XML serialization chart, not additive state. Finite
SO(3) composition/inverse and chart ambiguity checks are retained. There is no
arbitrary 10 mrad acceptance cut.

The supported layout has one module in phi and z per layer and canonical zero
`phi0`, `phi_tilt`, `dr` and `z0`. Unsupported layouts are refused. Air module
and layer envelopes may grow monotonically to preserve prior nonnegative
clearance under the exact approved formula. Arbitrary growth is refused;
sensitive dimensions, materials and IDs stay invariant. Actual DD4hep solids,
containment/overlaps, SurfaceMap frames and installed ACTS navigation are
checked on candidate reload before further reconstruction.

## Alignment parameter scopes

The Controller accepts exact retained configurations, including their identities
and conventions. It does not synthesize parameters from arbitrary labels.

| Mode | Free components |
| --- | --- |
| `l1_y` | L1 Global Y |
| `l1_y_rx_ry_rz_l2_y` | L1 Y/Rx/Ry/Rz and L2 Y; five-parameter study |
| `l1_l2_y_rx_ry_rz` | L1/L2 Y/Rx/Ry/Rz |
| `l1_l2_y_rx_ry_rz_l3_y` | L1/L2 Y/Rx/Ry/Rz and L3 Y |
| `l1_l2_l3_y_rx_ry_rz` | L1/L2/L3 Y/Rx/Ry/Rz |

The fixed references and unselected components are explicit in each parameter
JSON. Global X/Z remain fixed. A selected SO(3) update contains all three
rotation components. Labels are `700000 + 100*layer + component`, with
components 2=Y, 4=Rx, 5=Ry, 6=Rz; Y uses mm and rotations use rad.
For example, L1 Y is 700102 and L2 Rz is 700206. These are native physical
labels, distinct from retained independent legacy mathematical references.

The retained synthetic closure driver also has bounded single-axis historical
fixtures. Their existence does not expand the public Controller's mode list
or establish an unconstrained six-DOF fit.

## Immutable inputs and Controller policy

Each accepted event has one complete EventHeader and exactly one valid Raw cell
per SiMAPS layer. The initial Raw-only topology selection uses no fitted track
or Truth information. It cannot guarantee later CKF acceptance. Missing tracks,
outliers, changed track identities or rejected Pede local fits are recorded
failures, not silently discarded data.

`native_controller start --events N` processes the first N frames in file order
without truncating the input ROOT file. Full file/payload hashes, producer's
full accepted count, selected run/event identities and selected payload checksum
are recorded separately. An example after a successful smoke producer is:

```bash
bash environment/run.sh python3 -B -m calibration.native_controller start \
  --run result/native_prefix16 \
  --raw result/native_smoke/raw/projection/channels.root \
  --input-provenance result/native_smoke/raw/provenance.json \
  --geometry result/native_smoke/preparation/initial.xml \
  --config validation/telescope_alignment/l1_global_y.json \
  --build build/native_telescope --events 16 --chi2-cut 1000 \
  --validation-mode diagnostic --geometry-aware-holes \
  --minimum-corrections 1 --maximum-corrections 1
```

Strict policy is the Controller default. Explicit offline diagnostic policy can
continue only after the recognized high-chi2 Pede advisory with zero rejected
local fits, finite estimates and independently checked full-rank equations.
Unknown warnings and integrity failures stop. No steering or nominal error is
rescaled. Actual finite SurfaceMap crossings, rather than a hard-coded hole
count, are checked when `--geometry-aware-holes` is selected by the public CLI.

Convergence uses initial data-only information/errors, strict remaining Y
updates below `2e-5` mm, angular updates below `0.25` initial formal sigma and
joint significance `sqrt(delta.T H0 delta)<0.25`. The scaled-condition bound is
100. The CLI permits tightening those demonstration limits, not relaxing them.
Unchanged track association, physical Geometry and improved chi2/residuals are
also required. Convergence is same-data consistency, not a statistical coverage
claim. No candidate is automatically approved or activated.
