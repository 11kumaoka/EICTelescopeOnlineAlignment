# Telescope ZeroMQ transport prototype

This layer sends the existing `ETFB` FlatBuffers timeframe without changing
its EDM mapping. It depends on libzmq/cppzmq, while
`TelescopeTimeframeSerialization` remains independent of ZeroMQ and JANA2.

Transport protocol versions 2 and 3 are single-producer, single-consumer event
streams:

- one PODIO timeframe is one two-frame ZeroMQ multipart DATA message;
- frame 0 is a 32-byte network-order transport header;
- frame 1 is the unmodified FlatBuffers payload;
- DATA messages use consecutive sequence numbers `0..N-1`;
- one explicit empty EOS with sequence `N` terminates the whole stream;
- an idle timeout between complete messages is recoverable, while a partial
  multipart message, sequence gap, stream-ID change, or malformed EOS is fatal.

Version 2 uses PUSH/PULL and does not acknowledge delivery. Version 3 uses
REQ/REP: the receiver validates each transport envelope and replies with an ACK
for the same stream ID and sequence before the sender can send the next DATA or
EOS. This event-by-event handshake provides deterministic backpressure and
prevents a finite ROOT sender from closing with messages still queued locally.
If an ACK times out, the sender uses `ZMQ_REQ_RELAXED` plus
`ZMQ_REQ_CORRELATE` to resend the identical request a bounded number of times
(three retries by default). The receiver compares an immediately repeated
sequence, header, and payload byte-for-byte, re-ACKs an exact retry, and never
delivers it upstream twice. A retry with different content is a fatal protocol
error.
The ACK means the receiver transport owns the complete message; it does not
mean FlatBuffers deserialization or downstream JANA factories have completed.

The receiver also accepts legacy protocol version 1 streams containing exactly
`DATA(0), EOS(1)`. The old single-file sender syntax continues to emit this v1
form so existing single-event receivers remain usable.

The header contains `ETZ1`, protocol version, message kind, payload encoding,
stream ID, sequence number, and payload size. The transport receiver validates
the multipart shape, version, sequence, encoding, and size limit. The CLI and
JEventSource then use the serialization layer to verify FlatBuffers, EDM
collection types, and PODIO schema versions before publishing decoded data.

| Header bytes | Field | Encoding |
| --- | --- | --- |
| 0–3 | magic | ASCII `ETZ1` |
| 4–5 | protocol version | unsigned 16-bit, network order; versions 1, 2, or 3 |
| 6 | message kind | `DATA=1`, `EOS=2`, `ACK=3` |
| 7 | payload encoding | `NONE=0`, `ETFB=1` |
| 8–15 | stream ID | unsigned 64-bit, network order |
| 16–23 | sequence | unsigned 64-bit, network order |
| 24–31 | payload size | unsigned 64-bit, network order |

PUSH/PULL v1/v2 only confirms that ZeroMQ accepted DATA and EOS; sender shutdown
can therefore discard a remaining local queue. Use acknowledged v3 for finite
bulk ROOT streams. Version 3 detects a missing or malformed ACK and fails the
sender after its bounded retry budget, but it does not provide durable storage,
process-crash recovery, or downstream processing acknowledgement. The receiver
closes after delivering EOS to its caller, so loss of the final EOS ACK can
still make the sender report failure even though all DATA was received; output
comparison remains the end-to-end success criterion. Do not attach multiple
peers: v1/v2 could load-balance DATA and EOS to different consumers, while
REQ/REP v3 deliberately models exactly one sender and receiver. The configured
timeout applies separately to each ZeroMQ frame or ACK attempt; it is not an
overall stream deadline. `TransportOptions::acknowledgementRetries` controls the
library retry budget.

`TelescopeZmqTransport` deliberately treats payload bytes as opaque. The CLI
sender verifies ETFB before transport, and the CLI receiver verifies and
deserializes ETFB before publishing output. Future direct library callers must
perform the same serialization-layer validation.

## Build

```bash
cmake -S streaming/zeromq -B build/streaming_zeromq \
  -DCMAKE_BUILD_TYPE=Release
cmake --build build/streaming_zeromq -j2
ctest --test-dir build/streaming_zeromq --output-on-failure
```

From the telescope parent project, opt in with both
`-DTELESCOPE_BUILD_STREAMING_SERIALIZATION=ON` and
`-DTELESCOPE_BUILD_ZMQ_TRANSPORT=ON`. Both are off by default so older Alignment
environments without FlatBuffers or ZeroMQ continue to build unchanged.

## Send serialized timeframes

Start the receiver first. Every output path must be new:

```bash
build/streaming_zeromq/TelescopeZmqReceiver \
  ipc:///tmp/telescope-stream.ipc received.fb reconstructed.root 5000
```

If needed, generate `timeframe.fb` from one ROOT event with the serialization
tool built in the same dependency environment:

```bash
build/streaming_zeromq/serialization/TelescopeFrameRoundTrip \
  INPUT.root 0 timeframe.fb local-check.root serialization-report.json 1000
```

Send a previously generated FlatBuffers message:

```bash
build/streaming_zeromq/TelescopeZmqSender \
  ipc:///tmp/telescope-stream.ipc timeframe.fb 5000
```

The command above preserves the legacy one-event v1 framing. To send several
timeframes in one v2 stream, list them in event order:

```bash
build/streaming_zeromq/TelescopeZmqSender \
  ipc:///tmp/telescope-stream.ipc --stream --timeout-ms 5000 \
  event-000.fb event-001.fb event-002.fb
```

All input files are read and validated before the first DATA message is sent.
To stream every `events` entry directly from one supported PODIO ROOT file,
without creating thousands of intermediate `.fb` files, use:

```bash
build/streaming_zeromq/TelescopeZmqSender \
  ipc:///tmp/telescope-stream.ipc --root channels.root 5000
```

ROOT mode performs a full validation preflight, reopens the file, and then
serializes and sends one event at a time with acknowledged protocol v3. The
matching EICrecon resource must use the `telescope-zmq-ack:` prefix described
below. Memory remains bounded to one PODIO Frame and one FlatBuffers payload
rather than the number of events. The sender checks that the input file
identity, event count, and total payload bytes are unchanged between the two
passes. Unsupported collections or Frame parameters fail before the first DATA
message is sent.

On success, ROOT mode also reports `ack_retries`, the number of request
resends caused by ACK timeouts. A nonzero value is recoverable but should be
monitored as a transport-health signal.

The standalone `TelescopeZmqReceiver` remains a single-timeframe validation
utility; use `TelescopeStreaming` for a multi-event JANA stream.

The receiver writes `.partial-<stream-id>` files beside the requested outputs,
deserializes the exact received bytes to PODIO/EDM, writes one ROOT event,
reads it back, and checks all supported fields exactly. Final output names are
published with a no-replace hard link only after those checks succeed; failed
partial files are retained for diagnosis.
The sender rejects a message whose recorded PODIO collection schema versions do
not match the active EDM libraries. Generate and consume a message with
compatible PODIO/EDM releases; this prevents silent cross-schema decoding.
IPC or loopback TCP is recommended for this unauthenticated prototype. Do not
bind it to an external interface; CURVE authentication must be designed before
remote deployment.

## EICrecon/JANA event source

The optional `TelescopeStreaming` plugin receives each validated timeframe,
moves an independent reconstructed `podio::Frame` into one `JEvent`, sets the
JANA event and run numbers from its `EventHeader`, and exposes every schema-v1
`RawTrackerHitCollection` under its original collection name. This follows the
same `InsertCollectionAlreadyInFrame` ownership model as EICrecon's PODIO file
source; downstream factories use their normal `GetCollection` calls.

The ZeroMQ socket is created, used, and destroyed by one dedicated receiver
thread. A bounded FIFO queue transfers complete DATA payloads, EOS, or a fatal
error to JANA. This keeps socket ownership independent of which JANA worker
calls `Emit()` and preserves transport order while downstream JEvents execute
in parallel. Queue capacity and the ZeroMQ high-water mark use
`telescope_zmq_source:high_water_mark`.

Enable all three opt-in layers when configuring the telescope project:

```bash
cmake -S localDetEICrecon/experimental/telescope \
  -B build/streaming_integration \
  -DTELESCOPE_BUILD_STREAMING_SERIALIZATION=ON \
  -DTELESCOPE_BUILD_ZMQ_TRANSPORT=ON \
  -DTELESCOPE_BUILD_ZMQ_EVENT_SOURCE=ON
cmake --build build/streaming_integration -j2
ctest --test-dir build/streaming_integration --output-on-failure
```

Use `telescope-zmq-ack:` for the acknowledged v3 ROOT workflow:

```bash
export JANA_PLUGIN_PATH="$PWD/build/streaming_integration${JANA_PLUGIN_PATH:+:$JANA_PLUGIN_PATH}"
eicrecon -Pplugins=TelescopeStreaming \
  telescope-zmq-ack:ipc:///tmp/telescope-stream.ipc
```

The original `telescope-zmq:` prefix remains available for legacy v1 and
unacknowledged v2 senders. Socket types must match: an acknowledged ROOT sender
cannot connect to the legacy prefix, and a PUSH sender cannot connect to the
acknowledged prefix.

For a finite acknowledged stream, let EOS terminate JANA and leave
`jana:nevents` unset (zero). Setting it to exactly the number of DATA events
causes JANA to close the source before it asks for EOS, so the sender correctly
reports an EOS-ACK timeout even though every DATA event was processed.

Start EICrecon before sending one or more `.fb` files with
`TelescopeZmqSender`. The
plugin path above is relative to the clone, not to a user-specific dependency
installation. Source parameters are
`telescope_zmq_source:timeout_ms`,
`telescope_zmq_source:max_payload_bytes`, and
`telescope_zmq_source:high_water_mark`. When the FIFO is empty, its bounded wait
returns JANA's recoverable `FailureTryAgain`; it does not end the stream or
advance the event count. Every complete DATA produces one JEvent. A v2/v3 EOS
returns `FailureFinished` only after all earlier DATA have been emitted.
Malformed DATA, sequence gaps, schema mismatch, an unsupported collection type,
or a partial multipart message fails the source rather than emitting a partial
event. A legacy v1 DATA without its required EOS remains fatal. For v2, an idle
period between DATA messages is intentionally indistinguishable from a delayed
producer, so a missing final EOS does not become an automatic timeout error:
the source keeps returning `FailureTryAgain` until EOS arrives or JANA is
stopped externally. Production operation must therefore supervise sender
failure and require explicit EOS for a finite stream. In v3, missing ACK fails
the sender only after bounded exact-sequence retries; an EOS ACK confirms
transport receipt, not completion of downstream Tracking or Mille processing.
