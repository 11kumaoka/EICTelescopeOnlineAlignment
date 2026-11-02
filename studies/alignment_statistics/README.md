# Alignment accuracy versus track statistics

Reusable offline tools for the existing five-parameter study: L1 Global
Y/Rx/Ry/Rz and L2 Global Y. They use the same Controller, native reconstruction
and official Pede settings as the public workflow. Truth is read only after
fits for validation. A completed calibration is not uncertainty-coverage or
material-aware physics validation.

The separate [linearized diagnostics guide](../../docs/LINEARIZED_DIAGNOSTICS.md)
describes read-only 18-parameter fits, 42-parameter information rank, cohort
planning and ROOT plots. These do not add Controller alignment modes or apply
Geometry Corrections. Their formal errors and selected-cohort statistics must
not be interpreted as validated Pede errors or independent-seed coverage.

## Methodology and historical sample

The preserved producer generated 12,000 events with seed `2026102503`, a 1 GeV
electron gun along Global +X, broad beam widths (0,12,10) mm and uniform theta
in pi/2±0.08 rad, phi in ±0.08 rad. Injected values were L1 Y=+0.080 mm,
L2 Y=−0.060 mm and L1 Global rotation vector (1,0.5,−0.5) mrad. Geant4 includes
configured material; the ACTS reconstruction remains zero-field/material-free.

The historical scan has **28 cohorts: 25 new calibrations plus three reused
prefix baselines**. Multiplicities are:

| Track count N | 100 | 200 | 300 | 400 | 500 | 700 | 1000 | 1250 | 1500 | 2000 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| Cohorts | 10 | 5 | 3 | 2 | 2 | 2 | 1 | 1 | 1 | 1 |

Within N, cohort r selects accepted-file interval `[r*N,(r+1)*N)`. Cohorts
overlap across different N and all originate from one simulation campaign,
not independent random-seed replicas. Earlier N=100/500/1000/5000/10000 plots
use nested samples, with separately recorded selection changes.

The historical Raw-only projection retained 11,516 events. A later explicitly
reviewed tracking selection excluded three events with outlier/incomplete
tracks, yielding 11,513. The older 10,000-track analysis additionally excluded
one high-local-chi2 event after a Pede rejection. These outcome-dependent
research selections can affect bias, tails and coverage; they are not a general
automatic rejection policy. Historical failed attempts and selection ledgers
remain preserved externally. A new sample must not reuse those event exclusions
merely to reproduce historical results.

## Produce new data

Complete the [Quick Start](../../docs/QUICKSTART.md) setup/build. Choose a new
study directory; these commands intentionally stop before full calibration:

```bash
bash environment/run.sh python3 -B -m calibration.workflow prepare \
  --config calibration/config/workflow_multi.json --events 12000 \
  --build build/native_telescope --output-dir result/statistics_new
bash environment/run.sh python3 -B -m calibration.workflow simulate --run-dir result/statistics_new
bash environment/run.sh python3 -B -m calibration.workflow digitize --run-dir result/statistics_new
```

This generates new data with recorded settings; it does not promise byte-identical
historical ROOT containers or selection outcomes. Inspect the actual projected
count in `raw/provenance.json`, Simulation/Digitization diagnostics and every
acceptance/failure. Before any research selection, reconstruct the full projected
sample using `geometry.operations.reconstruct`. For example, inside
the fixed container, replacing `ACTUAL_PROJECTED_COUNT` with that recorded count:

```python
from pathlib import Path
from geometry.operations import reconstruct

study = Path('/work/result/statistics_new')
reconstruct(Path('/work/build/native_telescope'), study / 'track_prescan',
            study / 'preparation/initial.xml', study / 'raw/projection/channels.root',
            study / 'configuration/alignment.json', events=ACTUAL_PROJECTED_COUNT,
            cut=1000., input_origin='geant4', geometry_aware_holes=True)
```

`scan_statistics.py select` replays an explicit researcher-reviewed JSON ledger;
it does not infer exclusions from Truth, residuals or fit/Pede outcomes. Bind
the exact full ROOT and canonical payload SHA256 values from provenance:

```json
{
  "schema": "explicit-event-selection-v1",
  "source_raw_root_sha256": "EXACT_SOURCE_ROOT_SHA256",
  "source_channel_payload_sha256": "EXACT_CHANNEL_PAYLOAD_SHA256",
  "exclude": []
}
```

An empty list explicitly retains all source events. If justified, each exclusion
must record integer `run_number`, `event_number` and a nonempty scientific
`reason`; missing/duplicate event identities or mismatched hashes are refused.
Complete EventHeaders and integer RawHit values are cloned and verified.

```bash
bash environment/run.sh python3 -B studies/alignment_statistics/scan_statistics.py select \
  --study result/statistics_new \
  --selection-ledger result/statistics_new/reviewed_ledger.json
```

The fresh output is `raw/track_accepted/`. An additional reviewed selection can
use `--source-root` and a new `--output-dir`; its ledger must bind that exact
source. Controller tracking/Pede rejection checks are unchanged. Insufficient
or rejected tracks stop a cohort rather than silently reducing N.

## Plan and run cohorts

For a new study without historical prefix baselines:

```bash
bash environment/run.sh python3 -B studies/alignment_statistics/scan_statistics.py prepare \
  --study result/statistics_new --fresh-calibrations
bash environment/run.sh python3 -B studies/alignment_statistics/scan_statistics.py run \
  --study result/statistics_new --max-jobs 2
```

`--fresh-calibrations` creates 28 new calibration jobs with no reused baseline.
Default preparation preserves the historical 25-new/3-reused behavior and
requires verified `accuracy_N100`, `accuracy_N500` and `accuracy_N1000_filtered`
jobs. It is inappropriate for a fresh producer without those inputs.

`--max-jobs` bounds new **sequential jobs in this invocation**, not parallel
workers. Repeat `run` to continue unused cohorts. Each cohort keeps separate
immutable Raw input, producer hashes, event intervals/identities, Geometry,
source/build identity, solver diagnostics and Controller stages in
`analysis/scan_2000/`. Do not edit sources during a campaign or discard partial
failures to restart them. The declared protocol uses diagnostic policy, CKF cut
1000 and exactly one correction followed by a fresh fit of identical RawHits.

## ROOT reporting

For completed jobs, choose unused outputs:

```bash
bash environment/run.sh python3 -B studies/alignment_statistics/scan_statistics.py report \
  --study result/statistics_new --output-dir result/statistics_scan_plots
```

The output includes `alignment_scan_summary.json` and ROOT graphs/canvases for
each selected parameter. `individual_*` uses first-fit data-only curvature
errors. `mean_*` shows mean and sample SD only where at least two cohorts exist;
`single_cohort_*` marks undefined between-cohort variance separately.
`rms_error_*` is RMS error about zero; for one cohort it is an absolute error,
not measured variance. Singleton SD/SEM are JSON null. SEM=SD/sqrt(cohort count)
differs from sample SD and formal fit uncertainty.

`plot_geometry_accuracy.py` retains the earlier five nested-sample analysis. It
requires the corresponding completed `accuracy_N*` jobs and their historical
selection variants; these are not created by the 28-cohort `prepare` command.
With those jobs available, use `--study STUDY --output NEW_FILE.root`.

The pose error is final corrected Y−Truth Y in micrometers and
`log(R_corrected @ R_truth.T)` in Global mrad. Displayed first-fit formal errors
are reference uncertainties, not propagated final cumulative-SO(3) covariance
or calibrated pull denominators. They differ from stabilized/rounded Pede
errors and empirical between-cohort variation. No errors from successive fits
on identical RawHits are added as independent observations.

## Separate linked Hit Residuals

`compare_hit_residuals.py` follows RecHit→RawHit and official RawHit→SimHit
relations using exact complete EventHeader and CellID/charge/time identity.
Native reconstruction's empty Truth-association collection is not a match.
All actual SimHit contributors remain explicit, with separate single-contributor
plots. Link weights are read and validated, but the current macro does not export
per-link rows or weights; original weights remain in the preserved digitization
ROOT file. Histograms use one unweighted entry per actual link. No nearest-neighbour
or track-Truth guess is used.

For the smoke run from the main README:

```bash
bash environment/run.sh python3 -B studies/alignment_statistics/compare_hit_residuals.py \
  --digitized result/native_smoke/raw/digitization/digitized.root \
  --before-reco result/native_smoke/controller/jobs/attempt_000/iteration_000/reconstruct/artifacts/reco.root \
  --after-reco result/native_smoke/controller/jobs/attempt_000/iteration_001/reconstruct/artifacts/reco.root \
  --output-dir result/native_smoke_hit_residuals
```

The default digitization provenance binds that exact digitized file. Output
contains `hit_residuals.root` and `report.json`: Global X/Y/Z RecHit−linked-SimHit
histograms in mm, contributor counts and provenance. These are distinct from
track-fit residuals and sensor-pose accuracy. The macro does not write Geometry
or alter alignment selection. Open ROOT files with TBrowser as described in
the [Quick Start](../../docs/QUICKSTART.md).

## Tests and data boundary

```bash
bash environment/run.sh python3 -B -m unittest \
  studies.alignment_statistics.test_statistics -v
```

These tests exercise summaries, explicit ledgers/refusals, sealed results and
singleton semantics. They do not establish coverage. The new distribution can
generate and analyze new studies without private data; it does not include
the preserved historical 12,000-event ROOT sample, 28 sealed jobs or failure
artifacts. Exact historical comparison requires those external records and
their original identities. Cross-N correlations, conditional selection and
one-campaign limitations remain even when every technical stage succeeds.
