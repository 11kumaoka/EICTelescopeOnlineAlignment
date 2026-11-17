#pragma once

#include <cstdint>
#include <string_view>

namespace eic::telescope::streaming {

struct RootEventComparisonSummary {
  std::uint64_t entries{};
};

// Compare every frame in the PODIO "events" category. This deliberately
// rejects GenericParameters because wire schema v1 cannot preserve them.
RootEventComparisonSummary
requireEquivalentRootEventFiles(std::string_view expectedPath,
                                std::string_view actualPath);

} // namespace eic::telescope::streaming
