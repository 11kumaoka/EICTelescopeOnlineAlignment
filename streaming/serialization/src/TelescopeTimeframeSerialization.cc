#include "TelescopeTimeframeSerialization.h"

#include "TelescopeTimeframe_generated.h"

#include <edm4eic/RawTrackerHitCollection.h>
#include <edm4hep/EventHeaderCollection.h>
#include <flatbuffers/flatbuffers.h>
#include <flatbuffers/verifier.h>

#include <algorithm>
#include <bit>
#include <cstring>
#include <limits>
#include <memory>
#include <set>
#include <stdexcept>
#include <string>
#include <utility>
#include <vector>

namespace eic::telescope::streaming {
namespace {

namespace wire = eic::telescope::streaming::fb;

class CountingAllocator final : public flatbuffers::Allocator {
public:
  std::uint8_t* allocate(std::size_t size) override {
    ++m_stats.allocationCalls;
    m_stats.totalRequestedBytes += size;
    m_liveBytes += size;
    m_stats.peakLiveBytes = std::max(m_stats.peakLiveBytes, m_liveBytes);
    return new std::uint8_t[size];
  }

  void deallocate(std::uint8_t* pointer, std::size_t size) override {
    ++m_stats.deallocationCalls;
    if (size > m_liveBytes) {
      std::terminate();
    }
    m_liveBytes -= size;
    delete[] pointer;
  }

  BuilderAllocationStats stats() const { return m_stats; }

private:
  BuilderAllocationStats m_stats{};
  std::size_t m_liveBytes{};
};

[[noreturn]] void fail(const std::string& message) {
  throw std::runtime_error("Telescope timeframe serialization: " + message);
}

const wire::Timeframe* verifiedRoot(std::span<const std::uint8_t> bytes) {
  if (bytes.empty()) {
    fail("empty message");
  }
  flatbuffers::Verifier verifier(bytes.data(), bytes.size());
  if (!wire::VerifyTimeframeBuffer(verifier)) {
    fail("FlatBuffers verifier rejected the message");
  }
  const auto* root = wire::GetTimeframe(bytes.data());
  if (root == nullptr || root->wire_schema_version() != kWireSchemaVersion) {
    fail("unsupported wire schema version");
  }
  if (root->category() == nullptr || root->category()->empty()) {
    fail("missing PODIO category");
  }
  if (root->collections() == nullptr) {
    fail("missing collection vector");
  }
  return root;
}

std::uint32_t currentHeaderSchemaVersion() {
  return edm4hep::EventHeaderCollection{}.getSchemaVersion();
}

std::uint32_t currentRawHitSchemaVersion() {
  return edm4eic::RawTrackerHitCollection{}.getSchemaVersion();
}

TimeframeMetadata validateSemantics(const wire::Timeframe& root) {
  TimeframeMetadata metadata;
  metadata.category = root.category()->str();
  metadata.sourceEntry = root.source_entry();
  std::set<std::string> names;
  std::size_t headerCollections = 0;
  std::size_t rawCollections = 0;

  for (flatbuffers::uoffset_t i = 0; i < root.collections()->size(); ++i) {
    const auto* collection = root.collections()->Get(i);
    if (collection == nullptr || collection->name() == nullptr ||
        collection->name()->empty() || collection->edm_type() == nullptr ||
        collection->edm_type()->empty()) {
      fail("invalid collection envelope");
    }
    const std::string name = collection->name()->str();
    if (!names.insert(name).second) {
      fail("duplicate collection name: " + name);
    }
    metadata.collectionNames.push_back(name);

    switch (collection->payload_type()) {
    case wire::CollectionPayload::EventHeaderPayload: {
      const auto* payload = collection->payload_as_EventHeaderPayload();
      if (payload == nullptr || payload->entries() == nullptr ||
          payload->entries()->size() != 1) {
        fail("EventHeader payload must contain exactly one object");
      }
      if (collection->edm_type()->string_view() !=
              edm4hep::EventHeaderCollection::typeName ||
          collection->podio_schema_version() != currentHeaderSchemaVersion()) {
        fail("EventHeader type or PODIO schema version mismatch");
      }
      ++headerCollections;
      ++metadata.eventHeaderObjects;
      break;
    }
    case wire::CollectionPayload::RawTrackerHitPayload: {
      const auto* payload = collection->payload_as_RawTrackerHitPayload();
      if (payload == nullptr || payload->entries() == nullptr) {
        fail("invalid RawTrackerHit payload");
      }
      if (collection->edm_type()->string_view() !=
              edm4eic::RawTrackerHitCollection::typeName ||
          collection->podio_schema_version() != currentRawHitSchemaVersion()) {
        fail("RawTrackerHit type or PODIO schema version mismatch");
      }
      ++rawCollections;
      metadata.rawTrackerHitObjects += payload->entries()->size();
      break;
    }
    default:
      fail("unsupported collection payload type");
    }
  }

  if (headerCollections != 1) {
    fail("timeframe must contain exactly one EventHeader collection");
  }
  if (rawCollections == 0) {
    fail("timeframe must contain at least one RawTrackerHit collection");
  }
  return metadata;
}

bool sameDoubleBits(double left, double right) {
  return std::bit_cast<std::uint64_t>(left) ==
         std::bit_cast<std::uint64_t>(right);
}

void requireSameNames(const podio::Frame& expected, const podio::Frame& actual) {
  auto expectedNames = expected.getAvailableCollections();
  auto actualNames = actual.getAvailableCollections();
  std::sort(expectedNames.begin(), expectedNames.end());
  std::sort(actualNames.begin(), actualNames.end());
  if (expectedNames != actualNames) {
    fail("collection names changed during round trip");
  }
}

} // namespace

SerializedTimeframe serializeTimeframe(const podio::Frame& frame,
                                       std::string_view category,
                                       std::uint64_t sourceEntry) {
  if (category.empty()) {
    fail("PODIO category must not be empty");
  }
  if (!frame.getParameters().empty()) {
    fail("PODIO Frame GenericParameters are not supported by wire schema version 1");
  }

  CountingAllocator allocator;
  flatbuffers::FlatBufferBuilder builder(1024, &allocator, false);
  std::vector<flatbuffers::Offset<wire::Collection>> wireCollections;
  const auto collectionNames = frame.getAvailableCollections();
  wireCollections.reserve(collectionNames.size());
  std::size_t headerCollections = 0;
  std::size_t rawCollections = 0;

  for (const auto& name : collectionNames) {
    const auto* base = frame.get(name);
    if (base == nullptr) {
      fail("collection disappeared while serializing: " + name);
    }
    const auto nameOffset = builder.CreateString(name);
    const auto typeOffset = builder.CreateString(base->getTypeName());

    if (const auto* headers =
            dynamic_cast<const edm4hep::EventHeaderCollection*>(base)) {
      if (headers->size() != 1) {
        fail("EventHeader collection must contain exactly one object");
      }
      std::vector<flatbuffers::Offset<wire::EventHeaderEntry>> entries;
      entries.reserve(headers->size());
      for (const auto header : *headers) {
        std::vector<double> weights(header.weights_begin(), header.weights_end());
        const auto weightsOffset = builder.CreateVector(weights);
        entries.push_back(wire::CreateEventHeaderEntry(
            builder, header.getEventNumber(), header.getRunNumber(),
            header.getTimeStamp(),
            std::bit_cast<std::uint64_t>(header.getWeight()), weightsOffset));
      }
      const auto payload = wire::CreateEventHeaderPayload(
          builder, builder.CreateVector(entries));
      wireCollections.push_back(wire::CreateCollection(
          builder, nameOffset, typeOffset, base->getSchemaVersion(),
          wire::CollectionPayload::EventHeaderPayload, payload.Union()));
      ++headerCollections;
      continue;
    }

    if (const auto* hits =
            dynamic_cast<const edm4eic::RawTrackerHitCollection*>(base)) {
      std::vector<wire::RawTrackerHitEntry> entries;
      entries.reserve(hits->size());
      for (const auto hit : *hits) {
        entries.emplace_back(hit.getCellID(), hit.getCharge(),
                             hit.getTimeStamp());
      }
      const auto payload = wire::CreateRawTrackerHitPayload(
          builder, builder.CreateVectorOfStructs(entries));
      wireCollections.push_back(wire::CreateCollection(
          builder, nameOffset, typeOffset, base->getSchemaVersion(),
          wire::CollectionPayload::RawTrackerHitPayload, payload.Union()));
      ++rawCollections;
      continue;
    }

    fail("unsupported collection type " + std::string(base->getTypeName()) +
         " for collection " + name);
  }

  if (headerCollections != 1 || rawCollections == 0) {
    fail("source frame requires one EventHeader and one or more RawTrackerHit collections");
  }

  const auto root = wire::CreateTimeframe(
      builder, kWireSchemaVersion, builder.CreateString(category), sourceEntry,
      builder.CreateVector(wireCollections));
  wire::FinishTimeframeBuffer(builder, root);

  SerializedTimeframe result;
  result.bytes.assign(builder.GetBufferPointer(),
                      builder.GetBufferPointer() + builder.GetSize());
  result.builderAllocations = allocator.stats();
  result.metadata = inspectTimeframe(result.bytes);
  if (result.metadata.collectionNames != collectionNames) {
    fail("FlatBuffer collection order differs from the source frame order");
  }
  return result;
}

TimeframeMetadata inspectTimeframe(std::span<const std::uint8_t> bytes) {
  const auto* root = verifiedRoot(bytes);
  return validateSemantics(*root);
}

DecodedTimeframe deserializeTimeframe(std::span<const std::uint8_t> bytes) {
  const auto* root = verifiedRoot(bytes);
  auto metadata = validateSemantics(*root);
  podio::Frame frame;

  for (flatbuffers::uoffset_t i = 0; i < root->collections()->size(); ++i) {
    const auto* collection = root->collections()->Get(i);
    const std::string name = collection->name()->str();
    switch (collection->payload_type()) {
    case wire::CollectionPayload::EventHeaderPayload: {
      edm4hep::EventHeaderCollection headers;
      const auto* entries = collection->payload_as_EventHeaderPayload()->entries();
      for (flatbuffers::uoffset_t j = 0; j < entries->size(); ++j) {
        const auto* entry = entries->Get(j);
        auto header = headers.create();
        header.setEventNumber(entry->event_number());
        header.setRunNumber(entry->run_number());
        header.setTimeStamp(entry->time_stamp());
        header.setWeight(std::bit_cast<double>(entry->weight_bits()));
        if (entry->weights() != nullptr) {
          for (const double weight : *entry->weights()) {
            header.addToWeights(weight);
          }
        }
      }
      frame.put(std::move(headers), name);
      break;
    }
    case wire::CollectionPayload::RawTrackerHitPayload: {
      edm4eic::RawTrackerHitCollection hits;
      const auto* entries = collection->payload_as_RawTrackerHitPayload()->entries();
      for (flatbuffers::uoffset_t j = 0; j < entries->size(); ++j) {
        const auto* entry = entries->Get(j);
        auto hit = hits.create();
        hit.setCellID(entry->cell_id());
        hit.setCharge(entry->charge());
        hit.setTimeStamp(entry->time_stamp());
      }
      frame.put(std::move(hits), name);
      break;
    }
    default:
      fail("unsupported collection payload during materialization");
    }
  }

  return {std::move(frame), std::move(metadata)};
}

void requireEquivalentFrames(const podio::Frame& expected,
                             const podio::Frame& actual) {
  requireSameNames(expected, actual);
  std::size_t headerCollections = 0;
  std::size_t rawCollections = 0;

  for (const auto& name : expected.getAvailableCollections()) {
    const auto* expectedBase = expected.get(name);
    const auto* actualBase = actual.get(name);
    if (expectedBase == nullptr || actualBase == nullptr ||
        expectedBase->getTypeName() != actualBase->getTypeName() ||
        expectedBase->getSchemaVersion() != actualBase->getSchemaVersion()) {
      fail("collection type/schema changed: " + name);
    }

    if (const auto* expectedHeaders =
            dynamic_cast<const edm4hep::EventHeaderCollection*>(expectedBase)) {
      const auto* actualHeaders =
          dynamic_cast<const edm4hep::EventHeaderCollection*>(actualBase);
      if (actualHeaders == nullptr ||
          expectedHeaders->size() != actualHeaders->size()) {
        fail("EventHeader collection size changed: " + name);
      }
      for (std::size_t i = 0; i < expectedHeaders->size(); ++i) {
        const auto left = (*expectedHeaders)[i];
        const auto right = (*actualHeaders)[i];
        if (left.getEventNumber() != right.getEventNumber() ||
            left.getRunNumber() != right.getRunNumber() ||
            left.getTimeStamp() != right.getTimeStamp() ||
            !sameDoubleBits(left.getWeight(), right.getWeight()) ||
            left.weights_size() != right.weights_size()) {
          fail("EventHeader scalar metadata changed: " + name);
        }
        for (std::size_t j = 0; j < left.weights_size(); ++j) {
          if (!sameDoubleBits(left.getWeights(j), right.getWeights(j))) {
            fail("EventHeader weights changed: " + name);
          }
        }
      }
      ++headerCollections;
      continue;
    }

    if (const auto* expectedHits =
            dynamic_cast<const edm4eic::RawTrackerHitCollection*>(expectedBase)) {
      const auto* actualHits =
          dynamic_cast<const edm4eic::RawTrackerHitCollection*>(actualBase);
      if (actualHits == nullptr || expectedHits->size() != actualHits->size()) {
        fail("RawTrackerHit collection size changed: " + name);
      }
      for (std::size_t i = 0; i < expectedHits->size(); ++i) {
        const auto left = (*expectedHits)[i];
        const auto right = (*actualHits)[i];
        if (left.getCellID() != right.getCellID() ||
            left.getCharge() != right.getCharge() ||
            left.getTimeStamp() != right.getTimeStamp()) {
          fail("RawTrackerHit value or ordering changed: " + name);
        }
      }
      ++rawCollections;
      continue;
    }

    fail("unsupported collection in equivalence check: " + name);
  }

  if (headerCollections != 1 || rawCollections == 0) {
    fail("equivalence check did not find the required collections");
  }
}

} // namespace eic::telescope::streaming
