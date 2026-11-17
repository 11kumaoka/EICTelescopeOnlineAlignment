#include "TelescopeTimeframeSerialization.h"

#include <edm4eic/RawTrackerHitCollection.h>
#include <edm4hep/EventHeaderCollection.h>
#include <podio/Frame.h>

#include <cstdint>
#include <iostream>
#include <limits>
#include <stdexcept>
#include <tuple>
#include <vector>

namespace {

podio::Frame fixture() {
  podio::Frame frame;
  edm4hep::EventHeaderCollection headers;
  auto header = headers.create();
  header.setEventNumber(std::numeric_limits<std::uint64_t>::max() - 7);
  header.setRunNumber(std::numeric_limits<std::uint32_t>::max() - 3);
  header.setTimeStamp(0xfedcba9876543210ULL);
  header.setWeight(-0.0);
  for (const double weight : {0.125, -0.0, 2.25}) {
    header.addToWeights(weight);
  }
  frame.put(std::move(headers), "EventHeader");

  edm4eic::RawTrackerHitCollection telescope;
  for (const auto values :
       std::vector<std::tuple<std::uint64_t, std::int32_t, std::int32_t>>{
           {0, std::numeric_limits<std::int32_t>::min(), -1},
           {std::numeric_limits<std::uint64_t>::max(),
            std::numeric_limits<std::int32_t>::max(),
            std::numeric_limits<std::int32_t>::min()},
           {0x0123456789abcdefULL, 0, std::numeric_limits<std::int32_t>::max()}}) {
    auto hit = telescope.create();
    hit.setCellID(std::get<0>(values));
    hit.setCharge(std::get<1>(values));
    hit.setTimeStamp(std::get<2>(values));
  }
  frame.put(std::move(telescope), "TelescopeChannels");

  edm4eic::RawTrackerHitCollection timing;
  auto timingHit = timing.create();
  timingHit.setCellID(42);
  timingHit.setCharge(-17);
  timingHit.setTimeStamp(23);
  frame.put(std::move(timing), "TimingRawHits");
  return frame;
}

} // namespace

int main() {
  try {
    auto source = fixture();
    const auto expectedOrder = source.getAvailableCollections();
    auto serialized = eic::telescope::streaming::serializeTimeframe(
        source, "events", 19);
    if (serialized.bytes.empty() ||
        serialized.builderAllocations.allocationCalls == 0 ||
        serialized.metadata.collectionNames != expectedOrder ||
        serialized.metadata.eventHeaderObjects != 1 ||
        serialized.metadata.rawTrackerHitObjects != 4) {
      throw std::runtime_error("serialization metadata mismatch");
    }
    auto decoded = eic::telescope::streaming::deserializeTimeframe(serialized.bytes);
    eic::telescope::streaming::requireEquivalentFrames(source, decoded.frame);

    auto truncated = serialized.bytes;
    truncated.resize(truncated.size() / 2);
    bool refused = false;
    try {
      (void)eic::telescope::streaming::deserializeTimeframe(truncated);
    } catch (const std::runtime_error&) {
      refused = true;
    }
    if (!refused) {
      throw std::runtime_error("truncated message was accepted");
    }

    auto parameterized = fixture();
    parameterized.putParameter("unsupported-metadata", 7);
    refused = false;
    try {
      (void)eic::telescope::streaming::serializeTimeframe(
          parameterized, "events", 19);
    } catch (const std::runtime_error&) {
      refused = true;
    }
    if (!refused) {
      throw std::runtime_error("unsupported Frame GenericParameters were ignored");
    }

    std::cout << "FlatBuffers単体往復テストに成功しました\n";
    return 0;
  } catch (const std::exception& error) {
    std::cerr << "FlatBuffers単体往復テストに失敗しました: " << error.what()
              << '\n';
    return 1;
  }
}
