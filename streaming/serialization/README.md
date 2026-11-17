# Telescope FlatBuffers serialization prototype

This prototype maps one PODIO ROOT `events` entry to one logical timeframe. It
has no JANA2, ZeroMQ, EICrecon, ACTS, or DD4hep dependency. The only supported
version-1 collection payloads are `edm4hep::EventHeaderCollection` and one or
more `edm4eic::RawTrackerHitCollection` objects. Unsupported collections are
rejected so the prototype cannot silently lose event data.

## Exact field mapping

| EDM field | C++ type | FlatBuffers field | FlatBuffers type |
| --- | --- | --- | --- |
| `EventHeader.eventNumber` | `uint64_t` | `event_number` | `ulong` |
| `EventHeader.runNumber` | `uint32_t` | `run_number` | `uint` |
| `EventHeader.timeStamp` | `uint64_t` | `time_stamp` | `ulong` |
| `EventHeader.weight` | `double` | `weight_bits` | `ulong` IEEE-754 bits |
| `EventHeader.weights` | `vector<double>` | `weights` | `[double]` |
| `RawTrackerHit.cellID` | `uint64_t` | `cell_id` | `ulong` |
| `RawTrackerHit.charge` | `int32_t` | `charge` | `int` |
| `RawTrackerHit.timeStamp` | `int32_t` | `time_stamp` | `int` |

Each collection envelope also stores its original collection name, fully
qualified EDM collection type, and PODIO schema version. Collection order is
stored in the FlatBuffers vector and RawTrackerHit order is stored in its struct
vector. New detector collections using RawTrackerHit require no schema change;
future payload types can be added to the `CollectionPayload` union.

`weight` uses its raw IEEE-754 bits because FlatBuffers table scalars omit a
value equal to their default; without this mapping `-0.0` would become `+0.0`.
The variable-length `weights` vector stores every double element directly.

The PODIO versions used by the supported environments provide ROOT/Arrow I/O
and Frame-to-Arrow-Table conversion, but no public API that directly produces a
self-contained transport byte buffer.
Arrow IPC would add a separate serialization layer and a larger dynamic schema
contract. The explicit FlatBuffers schema is therefore used for this bounded
RawHit prototype. PODIO's Arrow conversion remains relevant for a future
generic, arbitrary-collection transport.

## Build and run

The directory can be built independently from the rest of Alignment:

```bash
cmake -S streaming/serialization -B build/streaming_serialization \
  -DCMAKE_BUILD_TYPE=Release
cmake --build build/streaming_serialization -j2
ctest --test-dir build/streaming_serialization --output-on-failure
```

Run the exact ROOT → FlatBuffers → EDM/PODIO → ROOT check with new output paths:

```bash
build/streaming_serialization/TelescopeFrameRoundTrip \
  INPUT.root 0 timeframe.fb reconstructed.root report.json 1000
```

The tool verifies the FlatBuffer before reading it, compares all supported EDM
values exactly, writes the reconstructed Frame with PODIO, reads that ROOT file
back, and compares it again. The report separates ROOT I/O from serialization
timing and records payload size and FlatBufferBuilder allocation statistics.
PODIO does not expose its internal allocation count, so the report gives the
number of materialized EDM objects instead of an unreliable global estimate.

Version 1 does not serialize arbitrary PODIO Frame `GenericParameters`; a Frame
containing them is rejected rather than silently losing metadata. Required event
metadata is carried by the complete EventHeader object. Add any future metadata
contract explicitly before using it in reconstruction decisions.
