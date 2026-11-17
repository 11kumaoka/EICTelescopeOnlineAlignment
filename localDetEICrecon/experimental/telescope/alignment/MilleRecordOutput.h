#pragma once

#include <cstdint>
#include <memory>
#include <string>

namespace Mille {
class MilleRecord;
}

namespace eicrecon::telescope {

struct MilleRecordMetadata {
  std::uint64_t runNumber{};
  std::uint64_t eventNumber{};
};

// Both backends receive exactly the same official MilleRecord::addData calls;
// no derivative, whitening, label, or residual calculation is duplicated.
class MilleRecordOutput {
public:
  virtual ~MilleRecordOutput() = default;
  virtual Mille::MilleRecord &beginRecord(MilleRecordMetadata metadata) = 0;
  virtual void commitRecord(std::uint32_t scalarMeasurements) = 0;
  virtual void finish() = 0;
};

std::unique_ptr<MilleRecordOutput>
makeFileMilleRecordOutput(const std::string &path);

} // namespace eicrecon::telescope
