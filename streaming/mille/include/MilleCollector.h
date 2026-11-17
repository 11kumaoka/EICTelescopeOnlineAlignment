#pragma once

#include <cstddef>
#include <cstdint>
#include <filesystem>
#include <string>

namespace eic::telescope::streaming::transport {
class TimeframeReceiver;
}

namespace eic::telescope::streaming::mille {

struct CollectorConfig {
  std::filesystem::path outputPath;
  std::filesystem::path reportPath;
  std::size_t expectedRecords{};
  std::string modelId;
  std::string configurationId;
  std::string geometryId;
  int maximumConsecutiveTimeouts{10};
};

struct CollectorResult {
  std::uint64_t streamId{};
  std::size_t records{};
  std::size_t scalarMeasurements{};
  std::uintmax_t outputBytes{};
};

// Receives exactly one stream. DATA is acknowledged only after the canonical
// official Mille record is flushed and fsync'd. EOS is acknowledged only after
// exact-count validation and exclusive publication of output and report.
CollectorResult collectMilleRecords(transport::TimeframeReceiver &receiver,
                                    const CollectorConfig &config);

} // namespace eic::telescope::streaming::mille
