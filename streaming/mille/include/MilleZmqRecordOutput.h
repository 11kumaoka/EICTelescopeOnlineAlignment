#pragma once

#include <cstdint>
#include <memory>
#include <string>

namespace eicrecon::telescope {
class MilleRecordOutput;

struct MilleZmqOutputConfig {
  std::string endpoint;
  std::uint64_t streamId{};
  std::string modelId;
  std::string configurationId;
  std::string geometryId;
  int timeoutMilliseconds{30000};
  int acknowledgementRetries{3};
};

std::unique_ptr<MilleRecordOutput>
makeZmqMilleRecordOutput(MilleZmqOutputConfig config);

} // namespace eicrecon::telescope
