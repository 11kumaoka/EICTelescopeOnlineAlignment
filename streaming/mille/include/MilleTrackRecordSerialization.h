#pragma once

#include <Mille/MilleDataStructures.h>

#include <cstdint>
#include <span>
#include <string>
#include <vector>

namespace eic::telescope::streaming::mille {

inline constexpr std::uint32_t kWireSchemaVersion = 1;

struct TrackRecordMetadata {
  std::uint64_t streamId{};
  std::uint64_t sequence{};
  std::uint64_t runNumber{};
  std::uint64_t eventNumber{};
  std::string modelId;
  std::string configurationId;
  std::string geometryId;
  std::uint32_t scalarMeasurements{};
};

struct SerializedTrackRecord {
  std::vector<std::uint8_t> bytes;
};

struct DecodedTrackRecord {
  TrackRecordMetadata metadata;
  Mille::MilleBuffer buffer;
};

SerializedTrackRecord serializeTrackRecord(const Mille::MilleBuffer &buffer,
                                           const TrackRecordMetadata &metadata);
DecodedTrackRecord deserializeTrackRecord(std::span<const std::uint8_t> bytes);

} // namespace eic::telescope::streaming::mille
