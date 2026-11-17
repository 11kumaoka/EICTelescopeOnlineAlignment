# Mille record streaming

This directory adds an optional output backend to the existing native
alignment calculation. It does not change ACTS propagation, measurement
selection, whitening, residuals, derivative labels, row order, Pede settings,
Geometry correction, or convergence policy.

The shared boundary is the official `Mille::MilleRecord` API:

- file mode (the default) keeps `Mille::spawnMilleRecord(path, true, false)`;
- ZeroMQ mode receives the same `addData()` calls in the same order;
- one accepted track becomes one `EMIL` FlatBuffers DATA payload;
- the Collector replays the official encoded buffer through a one-record
  `Mille::IMilleReader`, `MilleRecord::copyRecord()`, and `writeRecord()`.

Using `copyRecord()` is important. A writer already has the next record's
leading `(0,0)` marker after `writeRecord()`; appending with `copyToBuffer()`
would duplicate that marker from the second track onward.

## Wire mapping

`MilleTrackRecord.fbs` preserves one complete official buffer. No EDM or ACTS
value is recalculated by the serialization layer.

| `TrackRecord` field | Source / validation |
| --- | --- |
| `wire_schema_version` | EMIL application schema, currently 1 |
| `stream_id`, `sequence` | Must exactly match the ETZ1 transport envelope |
| `run_number`, `event_number` | Current JANA event identity; duplicate pairs are refused in the current one-track-per-event scope |
| `model_id` | SHA-256 of the compiled `NativeMilleModel` gate |
| `configuration_id` | SHA-256 of the exact alignment configuration file |
| `geometry_id` | SHA-256 of the active compact Geometry XML |
| `precision` | Official Mille storage precision; Telescope native alignment uses Float64 |
| `scalar_measurements` | 10 for the validated five-layer, two-coordinate track |
| `integer_buffer` | Official positional labels, including all zero delimiters and the leading record marker |
| `value_buffer` | Corresponding official float/double values represented as FlatBuffers doubles; Float32 values must round-trip exactly |

The deserializer verifies the FlatBuffers identifier/schema, identities,
vector lengths, finite values, leading marker, and the expected number of
measurement delimiters. The Collector also verifies consecutive sequence,
constant precision and scientific identities, unique event identity, exact
configured track count, and EOS sequence.

## Delivery and publication

Mille uses ETZ1 protocol v3 with payload encoding `EMIL=2`. The sender's ZeroMQ
socket is constructed, used, and destroyed on one dedicated I/O thread, so
JANA lifecycle thread changes cannot violate ZeroMQ socket affinity. Each
`commitRecord()` waits for its ACK and propagates a send failure to EICrecon.

The Collector enables deferred application ACK. For every DATA record it:

1. validates EMIL and all identities;
2. replays it through the official Mille API;
3. flushes, checks the exact canonical byte count, and calls `fsync`;
4. only then acknowledges the transport sequence.

At EOS it requires exactly the configured number of records, closes and
reopens the binary with the official reader, publishes `mille.dat` and the
Collector report with no-replace hard links, fsyncs the directory, and finally
ACKs EOS. Pede is started by the existing Controller only after EICrecon exits
successfully, the Collector exits successfully, its final report is validated,
and the published binary decodes to the exact requested number of ten-row
records. Merely reaching the track threshold is not sufficient.

Failed attempts retain their uniquely named partial file and never publish a
completion report. There is no in-place resume; a supervised Controller retry
uses a new stage. Loss of the final EOS ACK can still make the producer report
failure after the Collector has published complete output. The Controller
keeps that iteration failed rather than guessing success.

## Controller use

Existing runs remain file-based. Select the Collector explicitly:

```bash
python3 -B -m calibration.controller start \
  --run result/NEW_RUN \
  --raw result/INPUT/raw/projection/channels.root \
  --input-provenance result/INPUT/raw/provenance.json \
  --geometry result/INPUT/preparation/initial.xml \
  --config calibration/alignment/config/l1_global_y.json \
  --build build/streaming_integration \
  --events 63 --chi2-cut 1000 \
  --mille-transport zeromq \
  --validation-mode diagnostic --geometry-aware-holes \
  --minimum-corrections 1 --maximum-corrections 1
```

The Collector binds only to an ephemeral `127.0.0.1` TCP endpoint and publishes
that resolved endpoint in a ready receipt. No machine-specific path is stored
in source or build configuration. Each iteration receives its own Collector,
binary, report, stream ID, and scientific identities. Existing Pede,
independent Schur checks, Geometry correction/audits, checkpoint sealing, and
configured convergence loop are then reused unchanged.

For a fresh clone, configure the optional serialization, transport, and Mille
targets together. The paths below are repository-relative and do not encode a
developer-specific installation:

```bash
cmake -S localDetEICrecon/experimental/telescope \
  -B build/streaming_integration \
  -DCMAKE_BUILD_TYPE=Release \
  -DTELESCOPE_BUILD_MILLE_PROBE=ON \
  -DTELESCOPE_BUILD_STREAMING_SERIALIZATION=ON \
  -DTELESCOPE_BUILD_ZMQ_TRANSPORT=ON
cmake --build build/streaming_integration -j2
ctest --test-dir build/streaming_integration --output-on-failure
python3 -B -m unittest \
  validation.test_mille_collector_controller \
  validation.test_calibration_controller
```

The dependency setup and `MILLEPEDE_PREFIX` selection remain the repository's
standard ones described in [`docs/QUICKSTART.md`](../../docs/QUICKSTART.md).

`TelescopeMilleStreamingTest` uses two records to compare the existing official
file writer with the real dedicated-thread ZMQ sink and Collector byte for
byte. The Controller tests also require Collector success before reconstruction
completion and preserve the file-mode command contract.

## Current scope

- The maintained workflow deliberately runs EICrecon with `-Pnthreads=1`, so
  transport order is the canonical file order. A future multi-worker producer
  needs explicit event/track reordering before byte-order equivalence can be
  claimed.
- The present telescope contract is one accepted track per event. Supporting
  multiple tracks per event requires a `track_index` identity.
- The final Pede C-binary uses the local official library's native ABI. Mixed
  endianness or non-32-bit `int` hosts are outside this prototype.
- The loopback transport is unauthenticated. Remote deployment requires a
  separate authentication and authorization design.
- Deliberate deferred-ACK-loss fault injection is not yet covered; ordinary
  retry/duplicate suppression and durable two-record collection are tested.
