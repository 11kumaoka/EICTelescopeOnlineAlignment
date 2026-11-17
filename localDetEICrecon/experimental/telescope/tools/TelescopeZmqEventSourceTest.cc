#include "streaming/TelescopeZmqEventSource.h"

#include "TelescopeTimeframeSerialization.h"
#include "TelescopeZmqTransport.h"

#include <JANA/JApplication.h>
#include <JANA/JEvent.h>
#include <JANA/JEventProcessor.h>
#include <edm4eic/RawTrackerHitCollection.h>
#include <edm4hep/EventHeaderCollection.h>
#include <podio/Frame.h>

#include <array>
#include <atomic>
#include <bit>
#include <chrono>
#include <cstdint>
#include <exception>
#include <filesystem>
#include <iostream>
#include <memory>
#include <stdexcept>
#include <string>
#include <thread>
#include <vector>

namespace {

constexpr std::size_t kEventCount = 64;
constexpr std::uint64_t kFirstEventNumber = 91;

podio::Frame fixture(std::size_t index) {
  podio::Frame frame;
  edm4hep::EventHeaderCollection headers;
  auto header = headers.create();
  header.setEventNumber(kFirstEventNumber + index);
  header.setRunNumber(17 + index);
  header.setTimeStamp(1234567890 + index);
  header.setWeight(-0.0);
  header.addToWeights(0.75 + static_cast<double>(index));
  frame.put(std::move(headers), "EventHeader");

  edm4eic::RawTrackerHitCollection hits;
  auto first = hits.create();
  first.setCellID(0x123456789abcdef0ULL + index);
  first.setCharge(-37 - static_cast<std::int32_t>(index));
  first.setTimeStamp(81 + static_cast<std::int32_t>(index));
  auto second = hits.create();
  second.setCellID(42 + index);
  second.setCharge(105 + static_cast<std::int32_t>(index));
  second.setTimeStamp(-901 - static_cast<std::int32_t>(index));
  frame.put(std::move(hits), "TelescopeChannels");

  edm4eic::RawTrackerHitCollection timingHits;
  auto timing = timingHits.create();
  timing.setCellID(0xffffffffffffffffULL - index);
  timing.setCharge(-11 - static_cast<std::int32_t>(index));
  timing.setTimeStamp(29 + static_cast<std::int32_t>(index));
  frame.put(std::move(timingHits), "TimingRawHits");
  return frame;
}

class CollectionAudit final : public JEventProcessor {
public:
  std::atomic<int> processed{};
  std::array<std::atomic_bool, kEventCount> seen{};

  void Process(const std::shared_ptr<const JEvent> &event) override {
    // Keep one worker deliberately slower than the receiver so the test
    // crosses both the ZeroMQ and application FIFO high-water marks.
    std::this_thread::sleep_for(std::chrono::milliseconds(1));
    const auto eventNumber = event->GetEventNumber();
    if (eventNumber < kFirstEventNumber ||
        eventNumber >= kFirstEventNumber + kEventCount) {
      throw std::runtime_error("JEventのevent番号が範囲外です");
    }
    const auto index =
        static_cast<std::size_t>(eventNumber - kFirstEventNumber);
    if (event->GetRunNumber() != 17 + index) {
      throw std::runtime_error("JEventのevent/run番号が一致しません");
    }
    const auto *headers =
        event->GetCollection<edm4hep::EventHeader>("EventHeader");
    const auto *hits =
        event->GetCollection<edm4eic::RawTrackerHit>("TelescopeChannels");
    const auto *timingHits =
        event->GetCollection<edm4eic::RawTrackerHit>("TimingRawHits");
    if (headers == nullptr || headers->size() != 1 || hits == nullptr ||
        hits->size() != 2 || timingHits == nullptr || timingHits->size() != 1) {
      throw std::runtime_error("JEventのPODIO collectionが一致しません");
    }
    const auto header = (*headers)[0];
    if (header.getEventNumber() != kFirstEventNumber + index ||
        header.getRunNumber() != 17 + index ||
        header.getTimeStamp() != 1234567890 + index ||
        std::bit_cast<std::uint64_t>(header.getWeight()) !=
            std::bit_cast<std::uint64_t>(-0.0) ||
        header.weights_size() != 1 ||
        std::bit_cast<std::uint64_t>(header.getWeights(0)) !=
            std::bit_cast<std::uint64_t>(0.75 + static_cast<double>(index))) {
      throw std::runtime_error("EventHeader metadataが変化しました");
    }
    if ((*hits)[0].getCellID() != 0x123456789abcdef0ULL + index ||
        (*hits)[0].getCharge() != -37 - static_cast<std::int32_t>(index) ||
        (*hits)[0].getTimeStamp() != 81 + static_cast<std::int32_t>(index) ||
        (*hits)[1].getCellID() != 42 + index ||
        (*hits)[1].getCharge() != 105 + static_cast<std::int32_t>(index) ||
        (*hits)[1].getTimeStamp() != -901 - static_cast<std::int32_t>(index)) {
      throw std::runtime_error(
          "JEventのRawTrackerHit値または順序が変化しました");
    }
    if ((*timingHits)[0].getCellID() != 0xffffffffffffffffULL - index ||
        (*timingHits)[0].getCharge() !=
            -11 - static_cast<std::int32_t>(index) ||
        (*timingHits)[0].getTimeStamp() !=
            29 + static_cast<std::int32_t>(index)) {
      throw std::runtime_error("追加RawTrackerHit collectionが変化しました");
    }
    const auto *frame = event->GetSingle<podio::Frame>();
    const std::vector<std::string> expectedNames{
        "EventHeader", "TelescopeChannels", "TimingRawHits"};
    if (frame == nullptr || frame->getAvailableCollections() != expectedNames) {
      throw std::runtime_error("JEventがPODIO Frameを所有していません");
    }
    if (seen[index].exchange(true)) {
      throw std::runtime_error("同じeventを重複処理しました");
    }
    ++processed;
  }
};

std::string uniqueIpcEndpoint() {
  const auto ticks =
      std::chrono::steady_clock::now().time_since_epoch().count();
  const auto path = std::filesystem::temp_directory_path() /
                    ("tzjes-" + std::to_string(ticks) + ".ipc");
  return "ipc://" + path.string();
}

} // namespace

int main(int argc, char **argv) {
  try {
    if (argc > 2) {
      throw std::runtime_error(
          "使用法: TelescopeZmqEventSourceTest [NTHREADS]");
    }
    const int threadCount = argc == 2 ? std::stoi(argv[1]) : 1;
    if (threadCount != 1 && threadCount != 4) {
      throw std::runtime_error("NTHREADSは1または4を指定してください");
    }
    std::vector<eic::telescope::streaming::SerializedTimeframe> serialized;
    serialized.reserve(kEventCount);
    for (std::size_t i = 0; i < kEventCount; ++i) {
      auto sourceFrame = fixture(i);
      serialized.push_back(eic::telescope::streaming::serializeTimeframe(
          sourceFrame, "events", i));
    }
    const auto endpoint = uniqueIpcEndpoint();
    const auto resource = "telescope-zmq-ack:" + endpoint;
    const auto legacyResource = "telescope-zmq:" + endpoint;

    JEventSourceGeneratorT<TelescopeZmqEventSource> generator;
    if (generator.CheckOpenable(resource) != 1.0 ||
        generator.CheckOpenable(legacyResource) != 1.0 ||
        generator.CheckOpenable("not-a-zmq-source") != 0.0) {
      throw std::runtime_error("JEventSourceGeneratorのresource判定が不正です");
    }

    JApplication application;
    application.SetParameterValue("jana:loglevel", "off");
    application.SetParameterValue("nthreads", threadCount);
    auto *source = new TelescopeZmqEventSource(resource, &application);
    auto *audit = new CollectionAudit();
    application.Add(source);
    application.Add(audit);

    std::exception_ptr senderError;
    std::thread sender([&] {
      try {
        eic::telescope::streaming::transport::TransportOptions options;
        options.timeout = std::chrono::milliseconds(10000);
        options.deliveryMode = eic::telescope::streaming::transport::
            DeliveryMode::acknowledgedRequestReply;
        eic::telescope::streaming::transport::TimeframeSender transport(
            endpoint, options);
        transport.beginStream(0xabc123ULL);
        for (std::size_t i = 0; i < serialized.size(); ++i) {
          transport.sendTimeframe(serialized[i].bytes);
          if (i == 0) {
            std::this_thread::sleep_for(std::chrono::milliseconds(700));
          }
        }
        transport.endStream();
      } catch (...) {
        senderError = std::current_exception();
      }
    });

    std::exception_ptr applicationError;
    try {
      application.Run();
    } catch (...) {
      applicationError = std::current_exception();
    }
    sender.join();
    if (senderError) {
      std::rethrow_exception(senderError);
    }
    if (applicationError) {
      std::rethrow_exception(applicationError);
    }
    if (audit->processed.load() != kEventCount ||
        source->GetEmittedEventCount() != kEventCount) {
      throw std::runtime_error("JANAが正確に64 eventを処理しませんでした");
    }
    for (const auto &seen : audit->seen) {
      if (!seen.load()) {
        throw std::runtime_error("ZeroMQ streamのeventが欠落しました");
      }
    }

    std::cout << "ZeroMQ複数event JEventSource統合テストに成功しました: "
              << "nthreads=" << threadCount << '\n';
    return 0;
  } catch (const std::exception &error) {
    std::cerr << "ZeroMQ JEventSource統合テストに失敗しました: " << error.what()
              << '\n';
    return 1;
  }
}
