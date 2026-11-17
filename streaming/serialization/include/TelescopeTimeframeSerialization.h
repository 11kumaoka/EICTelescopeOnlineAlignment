#pragma once

#include <podio/Frame.h>

#include <cstddef>
#include <cstdint>
#include <span>
#include <string>
#include <string_view>
#include <vector>

namespace eic::telescope::streaming {

inline constexpr std::uint32_t kWireSchemaVersion = 1;
inline constexpr std::string_view kFileIdentifier = "ETFB";

struct BuilderAllocationStats {
  std::size_t allocationCalls{};
  std::size_t deallocationCalls{};
  std::size_t totalRequestedBytes{};
  std::size_t peakLiveBytes{};
};

struct TimeframeMetadata {
  std::string category;
  std::uint64_t sourceEntry{};
  std::vector<std::string> collectionNames;
  std::size_t eventHeaderObjects{};
  std::size_t rawTrackerHitObjects{};
};

struct SerializedTimeframe {
  std::vector<std::uint8_t> bytes;
  BuilderAllocationStats builderAllocations;
  TimeframeMetadata metadata;
};

struct DecodedTimeframe {
  podio::Frame frame;
  TimeframeMetadata metadata;
};

SerializedTimeframe serializeTimeframe(const podio::Frame& frame,
                                       std::string_view category,
                                       std::uint64_t sourceEntry);

TimeframeMetadata inspectTimeframe(std::span<const std::uint8_t> bytes);

DecodedTimeframe deserializeTimeframe(std::span<const std::uint8_t> bytes);

// Throws on any collection-name/type/schema/order/value difference. Collection
// lookup order inside podio::Frame is not a public ordering contract; hit order
// and the collection order stored in the FlatBuffer are exact.
void requireEquivalentFrames(const podio::Frame& expected,
                             const podio::Frame& actual);

} // namespace eic::telescope::streaming
