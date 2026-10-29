# Offline linearized alignment diagnostics

These optional research tools read existing native `TelescopeMille` JSON.
They do not change ACTS tracking, write an operational Mille Binary, run Pede,
correct Geometry or extend the maintained Calibration Controller's parameter
scopes. The validated 64-event workflow and existing five-parameter/28-cohort
study remain separate.

The tools were selectively ported from the original repository's ignored
`result/full_dof_tools/`. They are maintained sources here; generated output
belongs under a new `result/` directory. Original sources, historical results
and runner prototypes remain preserved externally.

## Public input and commands

Complete the [Quick Start](QUICKSTART.md). The new smoke run supplies real
native first-fit equations without a private dataset:

```bash
bash environment/run.sh python3 -B studies/alignment_statistics/fit_18d_linearized_scan.py \
  --input result/native_smoke/controller/jobs/attempt_000/iteration_000/reconstruct/artifacts/mille.json \
  --output result/linearized18_smoke --counts 16,32
bash environment/run.sh python3 -B studies/alignment_statistics/analyze_full_42d_information.py \
  --input result/native_smoke/controller/jobs/attempt_000/iteration_000/reconstruct/artifacts/mille.json \
  --output result/information42_smoke --counts 16,32
```

Both require five measured SiMAPS layers L0–L4, four full-rank local track
parameters, valid finite native equations and explicit accepted event identity.
They do not silently select successful tracks. Explicitly prefiltered source
envelopes must declare their parent/source hash and selection consistently;
that recorded parent hash is not independently verified unless its actual
parent is supplied and checked. Parent rejection counts and failed status remain
explicit. Original solver warnings remain in the source workflow logs; these
offline tools neither interpret nor copy them.

The smoke's pencil beam may leave some requested components insufficiently
observable. A rank-deficient cohort is a diagnostic result, not permission to
lower an eigenvalue threshold, invent an estimate or enable a new fit mode.
Use actual returned ranks/conditions rather than assuming this beam validates
18 parameters. Output directories must be unused; failed partial outputs are
preserved and cannot be overwritten by rerunning the same command.

## What each calculation means

| Tool | Calculation and output |
| --- | --- |
| `fit_18d_linearized_scan.py` | Fix L0/L4, profile four local parameters per track, and inspect/solve L1/L2/L3 X/Y/Z/Rx/Ry/Rz information. Writes per-cohort rank, estimates, marginal formal errors and summary/ROOT products |
| `analyze_full_42d_information.py` | Assemble seven six-component blocks with no fit/correction. L5 MPGD and L6 AC-LGAD have no equations and correctly contribute zero columns. Writes normalized information rank/nullity versus cohort size |
| `plan_full_dof_scan.py` | Produce an unbound cohort proposal for an actual master with at least 20,000 accepted tracks. Does not generate data, bind event identities or run a fit |

The 18-column solve keeps the source's physical scaling: 0.1 mm translations
and 0.001 rad rotations. Its default relative eigenvalue cut is `1e-10`.
The 42-column diagnostic normalizes by information-diagonal scales and uses
default relative cut `1e-8`. These different ranks/scales are not interchangeable
with each other or the Controller's requested-parameter observability gates.

The optional `--truth FILE` is for offline scoring only. It must explicitly
declare `units: "mm_rad"` and all 18 finite values `L1_X` through `L3_Rz` in
`values`; missing components are not silently assumed nominal. Independently
verify its physical injection relative to the input reconstruction Geometry.
Translations are Global sensor-center differences; rotation Truth is
`log(R_truth @ R_initial.T)` in Global radians, rather than XML Euler angles.
Without Truth, the tool reports estimates and errors, not accuracy/RMS relative
to a supposed injection. Truth never enters the normal-equation solution.

Reported errors are data-only first-linearized curvature errors, not stabilized
or rounded Pede errors, empirical coverage, or a covariance of a final corrected
SO(3) pose. Linearized estimate-minus-injection differs from actual corrected
Geometry-minus-Truth closure; this tool performs no such physical correction.
No uncertainty rescaling or new Pede steering is applied.

## ROOT plots

The fit produces `fit_18d_linearized.root` and JSON even when no Truth is given;
accuracy plots require independently verified Truth. For a completed fit with
Truth and a fresh plot directory:

```bash
bash environment/run.sh python3 -B studies/alignment_statistics/plot_18d_individual_cohorts.py \
  --summary result/linearized18_with_truth/fit_18d_linearized_summary.json \
  --output result/linearized18_individual
bash environment/run.sh root -l -b -q \
  'studies/alignment_statistics/plot_all_18d_scan.C("result/linearized18_with_truth/fit_18d_linearized.root","result/linearized18_rms")'
```

`linearized18_with_truth` denotes a separately completed diagnostic with the
verified Truth ledger; the no-Truth smoke above does not produce that input.
Both plot commands read existing data, not run a fit. The Python plot writes
ROOT graphs/three canvases and PDF/PNG per sensor. The C++ macro checks explicit
scope/Truth metadata, then saves all 18 RMS/formal-error graph pairs and three
canvases in `linearized18_rms.root`, plus PDF/PNG overlays. It records the source
ROOT filename and native-equation input identity. Open the ROOT files in
TBrowser as described in the Quick Start. GUI interaction is separate from
batch plot creation/readback.

Saved `individual_*` graph points keep exact N, values and formal errors.
Only `individual_display_*` uses a deterministic log-x offset up to ±0.07 for
visibility. `mean_*` graphs contain between-cohort SD only for at least two
full-rank cohorts; `single_cohort_*` markers have no measured SD. Singleton
JSON SD is null, not zero uncertainty. RMS about zero becomes absolute error
for one cohort; it is not a measured ensemble variance.

Plots summarize only the full-rank cohorts recorded by the diagnostic; missing
or deficient cohorts remain in source statistics. Finite/identity/rank guards
do not establish unbiased selection. Cohorts are disjoint within N but overlap
across N and may share one simulation campaign. They are not independent-seed
replicas. Formal errors and cohort scatter do not certify material-aware
performance or uncertainty coverage.

## Planning and deferred functionality

`plan_full_dof_scan.py --help` documents the source's pilot/balanced/full
proposals. An available-event count is a declaration, not a verified dataset.
Its event/hash fields remain unbound until a real runner supplies them; no
large simulation or cohort campaign is launched by these offline tools.

The separate source prototypes `run_real_18d_scan.py` and
`run_18d_iterations.py` remain outside this distribution pending dedicated
scientific/architectural review. They add physical X/Z correction, a standalone
18-label binary bridge, different iteration control and private-data assumptions;
porting them would extend operational alignment beyond this approved offline
scope. Their historical README claims are not current validation evidence.
The old private-data preflight duplicates existing derivative/capability checks
and the new information diagnostic and is not copied. The earlier
`plot_alignment_scan.C` remains outside the curated package as previously
requested; the maintained five-parameter scan already has explicit singleton
graphs and ROOT canvases.

New tests validate profiling/rank/refusals, cohort bookkeeping, Truth separation
and plot semantics. Actual executed results belong in [Validation](VALIDATION.md);
neither a passing test nor successful display enables an 18- or 42-parameter
production correction.
