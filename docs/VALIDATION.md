# Validation

## Candidate acceptance record

The initial 142-file curated candidate passed fresh installation, build and
end-to-end technical validation on **2026-10-27**. It started with its own
empty Git repository: no inherited history, initial commit, old build,
dependency installation or private ROOT dataset. The README commands ran in
this actual candidate with the pinned Docker image and only this checkout
mounted at `/work`.

This is an independent **fresh-source/setup** test, not a literal Git clone:
the user requires review before the first commit, and an unborn repository has
no committed tree to clone. A post-commit clone check remains deliberately
deferred. The Controller records `head: null`, `git_head_state: unborn` and a
deterministic relative source-set SHA256 instead of fabricating a commit.
Its existing complete source/build/input/checkpoint guards remain mandatory.

The candidate acceptance gates are a genuinely fresh dependency installation,
all native targets, new 64-event Geant4 workflow, real Geometry/navigation,
unchanged RawHit/prefix provenance, official Mille decoding/Pede, corrected
reconstruction and ROOT readback. Retained tests must run without original
datasets, excluded modules/configurations or old installations. No scientific
assertion is weakened to accommodate curation.

| Executed check | Actual candidate result |
| --- | --- |
| Fresh official Mille/Millepede-II installation | Pinned sources cloned from their official URLs, built and installed under the candidate's own `external/install/` |
| Native CMake configure and complete build | All Plugin, model, Geometry, navigation, derivative, serialization and independent-reference targets passed in `build/native_telescope/` |
| Portable validation tests | **228 passed**, zero skips: 224 retained tests plus four new unborn-source tests |
| Study unit tests | **13 passed**, zero skips |
| CLI/documentation audit | 14 actual Docker help interfaces passed; all documented Python flags match; 60 Python, one Shell and 19 JSON syntax checks passed |
| New real Geant4 smoke | 64 generated events; 63 Raw-topology selections and 63 accepted tracks in both fits; one correction; converged |
| Physical Geometry | Actual DD4hep/ACTS reloads, solid containment/overlaps, identities/frames and real Navigator checks passed before reconstruction/correction publication |
| Independent smoke readback | All 115 executable/configuration source identities, actual build libraries, two checkpoints and 40 producer seals passed; 320 workflow files remained unchanged |
| Official Mille inspection | Both fits: 63 records, 630 scalar measurements, expected label/coefficients; independent Schur/rank/data-only and stabilized covariance checks passed |
| Runtime linkage | 14 actual `ldd`/`readelf` checks across seven targets; fresh `/work/external/install/lib/libMille2.so`, correct official dependency pins/origins |
| ROOT workflow report | All 39 saved objects read successfully |
| Synthetic L1/L2 closure | 32 tracks per pass; +149.14/+203.18 µm, exact reference ACTS ratios below |
| Consolidated scientific regressions | **17 command exits passed**: new standalone Geometry/tracking builds, real one-event factory/ROOT relations, native/legacy math, official mathematical Pede, all retained unit/study checks |
| Actual 16-of-63 prefix | Both 16-track fits converged after one correction; complete full Raw file preserved, interval `[0,16)`, exact Headers/channels/associations, current guards and both official decodes (16 records/160 scalars) passed |
| Exact linked Hit analysis | 63 events, 315 Raw cells and 316 actual SimHit links, including one two-contributor cell; all 60 histograms/3,780 entries independently recomputed; 66 named objects, 126 key cycles and five canvases read back |
| Study selection helper | New explicit empty ledger retained all 63 fresh source events; no outcome-dependent exclusion or historical cohort was invented |

The independent smoke evidence is
`result/curation_validation/runtime_independent_01/`; prefix evidence is
`result/curation_validation/prefix_independent_01/`. Independent consolidated,
ledger and linked Hit evidence is
`result/curation_validation/auxiliary_independent_03/`. Builds, logs and actual
command/exit records remain generated local evidence, not distributed source.
The original outer install/build/run receipts accompany the local curation
handoff. Repeated unit tests inside the consolidated suite are repeat executions,
not additional distinct tests or statistical samples.

| Quantity | Initial reconstruction | After correction |
| --- | ---: | ---: |
| Smoke accepted tracks | 63 | 63 |
| Smoke ACTS chi2/stored-NDF | 5.643925909012083 | 1.2900174523156787 |
| Smoke Pede chi2/NDF | 812.6993289610494 / 377 | 812.6993304768503 / 377 |
| Smoke Pede L1 Y update [mm] | +0.078865 | +0.00000022638 |
| 32-track reference ACTS chi2/stored-NDF | 37.16731567382813 | 0.6908491410315036 |

Smoke Pede status is 1 (high-chi2 advisory), with zero rejected local fits and
the suggested scale **1.47 unapplied**. The data-only L1 Y formal sigma is
0.001505845828432972 mm; the rounded Pede sigma is 0.0015058 mm. The configured
demonstration resolution target remains unmet and visible. Engineering success
and integrity pass, while physics quality is `not_validated`, approval remains
`unapproved` and Geometry activation is false.

The first portable-suite attempt ran all 228 tests and had five errors: a
lifecycle fixture tried to patch the excluded legacy Geometry manager. It now
supplies a test-only activation tripwire before applying the original mock.
All original activation-forbidden/checkpoint assertions remain; the second
suite and consolidated repeat pass. The failed first log is retained.

Two supplementary readback attempts encountered a ROOT directory-ownership
error in the auditor: fetching another histogram cycle invalidated a previously
cached object. Both failed logs are retained. The final reader snapshots each
cycle's numerical bins/statistics before fetching the next, with unchanged
comparison assertions. All duplicate-cycle statistics, linked residuals and
canvases pass; the maintained macro and its output files were not changed.

The linked L1 Global Y RecHit−SimHit mean changes from
−0.07895175648728397 to −0.00008675420827672103 mm. Its recovered pose still
differs by −1.135 µm from the offline-known +0.080 mm injection. Nearly zero
mean Hit Residuals therefore do not establish exact sensor alignment.

The 12,000-event/28-fit campaign was not rerun. The reusable methodology, all
study unit tests and a real new-data ledger replay are preserved; exact
historical reports still require external data. GUI interaction was not tested
in the headless environment; batch ROOT creation/readback passed. Always pass
the explicit native ROOT filename to `visualization/browse.C`, as documented.
The linked Hit macro validates link weights but does not persist per-link
rows/weights; the original digitization ROOT remains their authoritative source.

## Independent scientific checks

The synthetic closure input is an explicit mathematical/Geometry fixture, not
a Geant4 physics sample. With a fresh native build and unused output path:

```bash
bash environment/run.sh python3 -B validation/telescope_closure/closure.py \
  --build build/native_telescope --result result/native_reference32 \
  --events 32 --chi2-cut 1000 --step all
bash environment/run.sh python3 -B validation/telescope_closure/closure.py \
  --build build/native_telescope --result result/native_reference32 \
  --events 32 --chi2-cut 1000 --step regressions --validation-name curated_checks
bash environment/run.sh python3 -B -m unittest discover -s validation -p 'test_*.py'
bash environment/run.sh python3 -B -m unittest \
  studies.alignment_statistics.test_statistics -v
```

The explicit `--chi2-cut 1000` is part of the reference command. Omitting it
uses a different capture window and may reject misaligned tracks. Do not change
the numerical selection, errors or scientific assertions to reproduce a result.
These are the executed argument sets; the receipt wrapper additionally unset
`TESTBENCH_CONTAINER` and set `PYTHONDONTWRITEBYTECODE=1`. Output names above
already exist in the validated candidate: choose new names when repeating them.
The exact smoke setup/build/run commands are in the README. The actual prefix
command is in [Configuration](CONFIGURATION.md#immutable-inputs-and-controller-policy),
and the linked Hit command is in the [study guide](../studies/alignment_statistics/README.md#separate-linked-hit-residuals).

The distribution deliberately excludes seven +Z unit modules (34 tests):
`test_calibration_controller`, `test_geometry_correction`, `test_raw_metadata`,
`test_rotation`, `test_so3_alignment`, `test_tilt_alignment` and `test_z_alignment`.
Their backend, delivery/Controller and old Geometry/scenario dependencies are
outside the native +X package. The original tests remain preserved upstream.
The old +Z CLI-refusal and private-archive preservation scripts are also omitted.
The consolidated driver retains every native Geometry, independent tracking,
derivative, Mille, Pede and mathematical-reference assertion. Its retained native
unit suite checks input safety, Geometry and checkpoints; study tests replace
the archive-specific subprocess calls. Four additional tests validate honest
unborn-repository identity and refusal of changed/new sources or parent Git roots.

The maintained tests distinguish software-unit fixtures, real DD4hep/ACTS
Geometry, actual-surface analytical/finite-difference derivatives, native-state
equations versus official Mille records, local profiling/Pede consistency,
independent native/legacy mathematical comparison and Controller provenance/
safety. The retained legacy writer is an independent scientific reference;
the historical +Z reconstruction backend is not the public workflow.

## Validated upstream reference

The source release `19bb1e6bfeff63df3b3f84610cc5c021b59ab0b2` recorded:

| Check | Upstream result |
| --- | --- |
| Independent fresh installation/native build | Passed with pinned dependencies and actual library-link checks |
| New 64-event Geant4 workflow | 63 accepted tracks; one correction; convergence |
| Smoke ACTS chi2/stored-NDF | 5.643925909012083 → 1.2900174523156787 |
| Smoke L1 Global Y correction | +0.078865 mm for a physical +0.080 mm displacement |
| Synthetic 32-track native reference | L1/L2 +149.14/+203.18 micrometers |
| Native-reference ACTS ratio | 37.16731567382813 → 0.6908491410315036 |
| Release unit/study tests | 258 + 13 passed, zero skips; this includes source-release tests not all distributed here |
| Physical Geometry | Real solids/frames/IDs, containment, overlap, actual Navigator and separate ACTS bounds checks |
| Preserved statistical scan | 28 cohorts, 25 new fits + 3 reused baselines; historical data audited without rerunning the campaign |

These are historical engineering/numerical references. The candidate must report
its own executed values and any reproducibility differences. ROOT metadata and
timestamps are not promised byte-identical across independent simulations.

## Chi2 and uncertainties

ACTS 45.3.0 stored NDF accumulates calibrated measurement dimensionality: ten
for five 2D measurements. It is not the classical straight-line fit's six
degrees of freedom per track. For N tracks, four local parameters per track and
G fitted globals, the corresponding Pede count is `N*(10-4)-G`. ACTS also has a
seed prior; the Pede local-profile equations do not add that prior. Their chi2
ratios therefore have different definitions and need not coincide.

For data-only profiled information H, `inverse(H)` is a data-only formal
covariance. Pede's pre-sigma stabilization corresponds to
`inverse(H + diag(presigma^-2))` for the retained settings. Rounded printed
Pede errors are checked at their printed precision against the appropriate
stabilized quantity. Neither covariance is an empirical coverage measurement.
No successive-fit errors on identical channels are combined as independent
uncertainties, and high-chi2 scaling suggestions are never applied automatically.

The smoke source release reported a Pede warning status with chi2/NDF about
812.6993/377 and zero rejected local fits. Diagnostic continuation preserved
the warning and unapplied scaling factor. New runs must retain their actual
warnings, rejected-track counts and exit/status records rather than replacing
them with those reference values.

## Scientific limits

- Geant4 includes configured material transport. ACTS fitting is zero-field and
  material-free; no material-aware physics performance is certified.
- Successful Geometry/derivative checks validate conventions and equations,
  not uncertainty coverage or negligible statistical bias.
- Convergence and small mean RecHit−SimHit residuals do not establish unbiased
  recovered sensor poses. Pixel quantization, finite depth and multiple SimHit
  contributors remain relevant.
- The 28 cohorts are selected subsets of one simulation campaign. They are
  disjoint within each N, correlated across N, and partly reuse baselines.
  Tracking/Pede-outcome exclusions make historical inference conditional.
  They are not 28 independent random-seed simulations.
- Current supported selections fix references and Global X/Z. Full unconstrained
  sensor six-DOF observability is not claimed.

## Offline diagnostic port verification (2026-10-28)

The maintained inventory now has 149 files. Five offline tools, their focused
tests and one guide were selectively ported from ignored research sources.
No native C++, fitter, alignment JSON, Pede steering, Geometry Correction or
Controller mode changed. Operational 18-parameter runners remain outside this
package with explicit user approval; see [Provenance](PROVENANCE.md) and the
[diagnostic guide](LINEARIZED_DIAGNOSTICS.md).

| Executed port check | Result |
| --- | --- |
| Retained validation suite | 228 passed, zero skips |
| All statistics tests | 36 passed, zero skips: 13 retained plus 23 new mathematical/input/actual-ROOT/plotting tests |
| Numerical kernel identity | Four original Schur/solve/information/rank function definitions retained byte-for-byte |
| Historical diagnostic parity | All 446 cohorts and every original summary statistic exactly match stored estimates, formal errors, ranks and conditions |
| ROOT diagnostic readback | 78 fit, 76 individual-cohort, 43 RMS-overlay and four information-rank objects read; all 18 parameter series, nine canvases and 17 images independently checked |
| Information rank | All 446 historical cohorts have rank 21 of 42; unmeasured L5/L6 columns remain zero |
| Syntax and inventory | 65 Python, 19 JSON and one Shell file passed; all 149 maintained paths and local dependency targets checked |
| Clean native build | All targets passed in the fresh build; all 35 compiled-source/CMake entries stayed byte-identical through the Python-only export fix |
| Final-source native workflow | 64 new Geant4 events, 63 accepted tracks in both fits, one correction and convergence; ACTS ratios 5.643925909012083 → 1.2900174523156787 |
| Final-source synthetic reference | 32 tracks, L1/L2 +149.14/+203.18 micrometers, ACTS ratios 37.16731567382813 → 0.6908491410315036 |
| Independent final runtime readback | 120 source identities, 40 producer seals, two checkpoints, immutable RawHit bytes/complete Headers/intrinsics/associations, four official Mille decodes, Schur/covariance/Pede checks, seven actual physical Geometry/navigation reports, 14 linkage checks and 39 ROOT objects passed |
| Public no-Truth diagnostics | Both documented CLIs passed on the new 63-track input at N=16,32; independent SVD confirms rank 12/18 and null estimates in all four cohorts, rank 21/42, and valid empty-series ROOT export |

The first public-input diagnostic exposed a PyROOT overload defect: a sized
graph constructor given zero selected a null-histogram overload and failed
serialization. Its numerical JSON correctly retained rank 12/18 and null
estimates. The failed partial files and fatal log were preserved; only that
owned failed test container was stopped, with recorded exit 137. Default graph
construction and nonempty drawing now cover deficient, singleton-only and
repeated-only series. Three actual PyROOT tests exercise file write/readback;
rank thresholds, estimates and uncertainties were not altered. Fresh final
source runs use the `_02` paths below; the earlier successful calibration and
failed diagnostic outputs remain separate.

The first independent runtime reader also encountered an integer/string label-key
comparison mismatch between an in-memory dictionary and saved JSON. That failed
audit was retained. Only the reader's dictionary-key normalization changed;
the rerun and final-source audit passed with the same scientific assertions and
numerical tolerances. No research output or reference value was edited.

The fresh smoke Pede update is +0.078865 mm, followed by +2.2638e-7 mm.
High-chi2 warning status 1 and suggested scale 1.47 remain recorded and unapplied;
there are zero rejected local fits. Physics quality remains `not_validated`,
approval `unapproved`, and activation false. Current fixed-seed values match
the previous candidate references. The final executable/configuration source
fingerprint is `3f24460e657632a5f05342cdd9ad8861f0791d565e1e7a40edcc3eae9a3a4c39`.

The historical input contains 20,000 explicitly selected accepted tracks from
a parent report with 23,986 accepted and 14 rejected tracks out of 24,000.
Its parent status remains false in the diagnostic provenance. Every selected
event was independently matched to the parent's first 20,000 accepted rows;
selection is conditional, not an all-accepted producer certification or an
independent-seed ensemble. The input SHA256 is
`735b1f4688615f11b94b34c531f0a085450c5fa595934687a2b4bb7d5afc7079`.
All 18 supplied Truth components were independently checked against the saved
DD4hep/ACTS center differences and Global rotation logarithms. The historical
input was mounted read-only, was not copied into the distribution, and is not
required for the public smoke example.

The fresh native check used the preserved validated `external/install/` prefix,
a new `build/port_native_20261028/`, a new 64-event Geant4 run at
`result/port_native_smoke_20261028_02/`, and a new synthetic reference at
`result/port_reference32_20261028_02/`. No dependency reinstall or old checkpoint
reuse was performed. Exact command overrides were:

```bash
bash environment/run.sh cmake -S /work/localDetEICrecon/experimental/telescope \
  -B /work/build/port_native_20261028 -DCMAKE_BUILD_TYPE=Release \
  -DTELESCOPE_BUILD_MILLE_PROBE=ON
bash environment/run.sh cmake --build /work/build/port_native_20261028 -j2
bash environment/run.sh python3 -B -m unittest discover -s validation -p 'test_*.py'
bash environment/run.sh python3 -B -m unittest discover \
  -s studies/alignment_statistics -p 'test*.py' -v
bash environment/run.sh python3 -B -m calibration.workflow run \
  --config calibration/config/native_workflow_smoke.json \
  --output-dir result/port_native_smoke_20261028_02 \
  --build build/port_native_20261028 --validation-mode diagnostic
bash environment/run.sh python3 -B validation/telescope_closure/closure.py \
  --build build/port_native_20261028 --result result/port_reference32_20261028_02 \
  --events 32 --chi2-cut 1000 --step all
```

These output paths now exist; choose fresh names when repeating the commands.
The unchanged fixed-seed numerical references above remain the comparison
criteria. Diagnostic numerical equivalence and Geometry checks do not validate
18-parameter operational closure, material-aware physics or uncertainty coverage.

## External reference data and omitted capabilities

New Simulation, smoke, synthetic closure and portable tests require no private
ROOT dataset. Historical exact 99-track +Z closure, earlier z=-500 mm results,
failed attempts, sealed 12,000-event products and 28 historical Controller jobs
remain outside this distribution in preserved source repositories/archives.
Reproducing those exact historical checks requires their original immutable
ROOT/provenance/XML/checkpoint records and original source/build identities.

The curated package reproduces the statistics methodology from newly generated
data; it does not invent replacement cohorts and label them historical. A new
28-fit campaign is deliberately not part of the 64-event acceptance smoke.
Historical preservation manifests, archive-specific safety checks, the +Z
backend and its Controller delivery/activation features are intentionally not
fresh-clone tests here. Exact excluded files and reasons are recorded by the
curation inventory linked from [Provenance](PROVENANCE.md).

## Preservation and review boundary

The imported 376-file repository remains clean at `0dc01e0`; its snapshot and
backup retain exact contents, sizes, modes and mtimes. All 375 release files
match `19bb1e6`, with the local review patch preserved. The original development
checkout retains its 363 source baselines, all 13,984 study files and the
separately preserved user C macro with exact recorded identities.

The original checkout's strict inventory check did detect two additional empty
untracked root files, `python3` and `tail`. It is recorded as a failure, not
silently relaxed. No baseline file is missing or modified; both additions were
preserved/backed up and excluded from the candidate. Their origin is unresolved
at the time of this report. This is separate from the successful candidate
runtime/scientific checks and does not justify altering original files.

Only final Markdown evidence/clarifications change after the executed source
snapshot. No executable, configuration, numerical model or checkpoint was
modified or resealed after testing. No candidate commit, push, merge, public
release or Geometry activation was performed. The technical candidate is ready
for collaborator review; licensing decisions in [RELEASE_RIGHTS.md](RELEASE_RIGHTS.md)
remain unresolved before publication.
