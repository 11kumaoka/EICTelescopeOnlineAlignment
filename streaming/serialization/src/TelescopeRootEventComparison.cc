#include "TelescopeRootEventComparison.h"

#include "TelescopeTimeframeSerialization.h"

#include <podio/Frame.h>
#include <podio/ROOTReader.h>

#include <cstdint>
#include <stdexcept>
#include <string>
#include <string_view>
#include <utility>
#include <vector>

namespace eic::telescope::streaming {
namespace {

[[noreturn]] void fail(const std::string &message) {
  throw std::runtime_error("Telescope ROOT event comparison: " + message);
}

podio::Frame readEvent(podio::ROOTReader &reader, unsigned entry,
                       std::string_view label) {
  auto data = reader.readEntry("events", entry);
  if (data == nullptr) {
    fail(std::string(label) +
         " ROOTのevents entryを読み込めません: " + std::to_string(entry));
  }
  return podio::Frame(std::move(data));
}

} // namespace

RootEventComparisonSummary
requireEquivalentRootEventFiles(std::string_view expectedPath,
                                std::string_view actualPath) {
  if (expectedPath.empty() || actualPath.empty()) {
    fail("入力ROOTパスが空です");
  }

  unsigned expectedEntries{};
  std::vector<std::vector<std::uint8_t>> expectedMessages;
  {
    // Do not keep two ROOTReaders alive simultaneously. In particular, old
    // PODIO files can require process-global ROOT schema-evolution state.
    podio::ROOTReader expectedReader;
    expectedReader.openFile(std::string(expectedPath));
    expectedEntries = expectedReader.getEntries("events");
    if (expectedEntries == 0) {
      fail("expected ROOTのevents categoryが空です");
    }
    expectedMessages.reserve(expectedEntries);
    for (unsigned entry = 0; entry < expectedEntries; ++entry) {
      auto expected = readEvent(expectedReader, entry, "expected");
      if (!expected.getParameters().empty()) {
        fail("wire schema v1で保持できないexpected Frame "
             "GenericParametersがあります: " +
             std::to_string(entry));
      }
      expectedMessages.push_back(
          serializeTimeframe(expected, "events", entry).bytes);
    }
  }

  podio::ROOTReader actualReader;
  actualReader.openFile(std::string(actualPath));
  const auto actualEntries = actualReader.getEntries("events");
  if (expectedEntries != actualEntries) {
    fail("events entry数が一致しません: expected=" +
         std::to_string(expectedEntries) +
         ", actual=" + std::to_string(actualEntries));
  }

  for (unsigned entry = 0; entry < actualEntries; ++entry) {
    auto actual = readEvent(actualReader, entry, "actual");
    if (!actual.getParameters().empty()) {
      fail("wire schema v1で保持できないactual Frame "
           "GenericParametersがあります: " +
           std::to_string(entry));
    }
    try {
      auto expected = deserializeTimeframe(expectedMessages[entry]);
      requireEquivalentFrames(expected.frame, actual);
    } catch (const std::exception &error) {
      fail("events entry " + std::to_string(entry) +
           " が一致しません: " + error.what());
    }
  }

  return {expectedEntries};
}

} // namespace eic::telescope::streaming
