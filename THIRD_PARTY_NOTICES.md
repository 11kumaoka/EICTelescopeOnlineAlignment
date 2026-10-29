# Third-party notices

This records source provenance and unresolved rights questions. It does not
grant a project-wide license. No project-wide license has been selected; see
[the rights review](docs/RELEASE_RIGHTS.md) before distribution or publication.

## Retained EICrecon source

The native plugin retains nine files from EICrecon commit
`195966d41899800eaeca6fbafd63cd53735e6543`. Exact upstream paths/SHA256 values are
in [provenance.json](localDetEICrecon/experimental/telescope/provenance.json).
The following paths are relative to `localDetEICrecon/experimental/telescope/`.
Original copyright and SPDX headers remain authoritative.

| File | Local adaptation |
| --- | --- |
| `algorithms/tracking/TrackerMeasurementFromHits.h` | Telescope namespace and optional B0-detector state |
| `algorithms/tracking/TrackerMeasurementFromHits.cc` | Installed-provider include, optional B0 constant, missing-context refusal and actual sensor-axis covariance projection |
| `algorithms/tracking/CKFTracking.h` | Telescope namespace, installed types and delegation to installed numerical CKF |
| `algorithms/tracking/CKFTracking.cc` | Telescope namespace, installed CKF, seed-centered perigee, electron hypothesis and consistent reference-surface extrapolation |
| `algorithms/tracking/CKFTrackingConfig.h` | Telescope namespace and explicit electron-hypothesis option |
| `factories/tracking/TrackerMeasurementFromHits_factory.h` | Adapted namespace/include paths |
| `factories/tracking/CKFTracking_factory.h` | Adapted namespace/includes, configured selector and electron-hypothesis parameter |
| `extensions/edm4eic/EDM4eicToActs.h` | Unchanged dependency missing from installed headers |
| `upstream_support/algorithms/meta/CollectionCollector.h` | Unchanged dependency missing from installed headers |

[Nine retained patches](localDetEICrecon/experimental/telescope/upstream_diffs/)
describe exact changes; two are empty because those copies are unchanged. The
source release reproduced all nine upstream hashes/patches from pinned Git
objects. That is provenance evidence, not blanket licensing clearance.

Six copied files have explicit `LGPL-3.0-or-later` SPDX headers. The copied
configuration and two factory headers lack per-file SPDX in the inspected
upstream revision. The retained [NOTICE.md](localDetEICrecon/experimental/telescope/NOTICE.md)
and [LICENSE.LGPL3](localDetEICrecon/experimental/telescope/LICENSE.LGPL3) match
that revision. The upstream NOTICE refers to `LICENSE.md`, while the inspected
tree contains `LICENSE.LGPL3`; its wording and scope require clarification.
It is not a public-domain dedication by this project's contributors. No
upstream notice or header has been rewritten to resolve that ambiguity.

Installed EICrecon Geometry, Digitization, Hit Reconstruction, ACTS provider/
SurfaceMap, numerical CKF and ActsToTracks retain their own terms. Their use
does not relicense local contributions. The historical +Z plugin/constructor
and their copied source notices are not part of this curated native package.
The retained local BeamTestMilleWriter/reference mathematics are original local
contributions and remain subject to the project-license decision.

## External alignment dependencies

Official dependencies are fetched into ignored `external/`, not vendored as
generated installations. Immutable pins are in
[environment/dependencies.json](environment/dependencies.json).

| Dependency | Repository and pin | Inspected source evidence |
| --- | --- | --- |
| Mille 01.01.02 | `https://gitlab.desy.de/millepede/Mille.git`; `cec2e880ecaab3d255e08e21c8f70f248c6098d8` | Headers/docs state GNU Library GPL version 2 or later; README identifies LGPLv2 |
| Millepede-II 05.01.06 | `https://gitlab.desy.de/millepede/millepede-ii.git`; `7e3f4f57d993e0e0098d1a40da72823ac9c3207b` | Headers/docs state GNU Library GPL version 2 or later; `COPYING.LIB` contains version 2 |

The pinned Mille checkout refers to `COPYING.LIB` without containing a standalone
file of that name. Preserve official sources/locks and review this discrepancy
before packaging dependency sources, binaries or installations.

## Runtime environment

`environment/run.sh` pins
`eicweb/eic_xl@sha256:67c914295a92396040c3051360d70786df9c1ee8458264ad0b83cd1a8ccf3d05`.
ACTS, DD4hep, Geant4/npsim, ROOT, EICrecon, JANA2, EDM4hep/EDM4eic, podio,
NumPy and other image contents retain their own terms. This source candidate
does not publish installed libraries, generated data or a modified image.
A later binary/image/data distribution requires a separate notice and rights
review. Pinning an image is not permission to relicense it.
