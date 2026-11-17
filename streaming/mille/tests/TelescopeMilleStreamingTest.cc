#include "MilleCollector.h"
#include "MilleRecordOutput.h"
#include "MilleTrackRecordSerialization.h"
#include "MilleZmqRecordOutput.h"
#include "TelescopeZmqTransport.h"

#include <Mille/MilleFactory.h>
#include <Mille/MilleRecord.h>

#include <chrono>
#include <filesystem>
#include <fstream>
#include <future>
#include <iterator>
#include <stdexcept>
#include <string>
#include <thread>
#include <unistd.h>
#include <vector>

namespace {

class CapturingRecord final : public Mille::MilleRecord {
public:
  CapturingRecord() : Mille::MilleRecord(true, false) {}
  void writeRecord() override {}
  void flushOutputFile() override {}
  void resetOutputFile() override {}
};

void fillRecord(Mille::MilleRecord &record, double offset) {
  const std::vector<unsigned int> localLabels{1, 2, 3, 4};
  const std::vector<int> globalLabels{700102, 700104};
  record.addData(0.125 + offset, 1., localLabels,
                 std::vector<double>{1., 0., 2., 0.}, globalLabels,
                 std::vector<double>{0.25, -0.5});
  record.addData(-0.375 - offset, 1., localLabels,
                 std::vector<double>{0., 1., 0., 2.}, globalLabels,
                 std::vector<double>{-0.75, 0.125});
}

std::vector<char> bytes(const std::filesystem::path &path) {
  std::ifstream input(path, std::ios::binary);
  if (!input) {
    throw std::runtime_error("test output cannot be opened");
  }
  return {std::istreambuf_iterator<char>(input),
          std::istreambuf_iterator<char>()};
}

} // namespace

int main() {
  const auto directory = std::filesystem::temp_directory_path() /
      ("telescope-mille-streaming-test-" + std::to_string(::getpid()));
  if (!std::filesystem::create_directory(directory)) {
    throw std::runtime_error("test directory already exists");
  }
  const auto directPath = directory / "direct.dat";
  const auto collectedPath = directory / "collected.dat";
  const auto reportPath = directory / "collector.json";

  constexpr std::uint64_t streamId = 0x123456789abcdef0ULL;
  const std::string model(64, 'a');
  const std::string configuration(64, 'b');
  const std::string geometry(64, 'c');
  std::vector<eic::telescope::streaming::mille::SerializedTrackRecord>
      payloads;
  {
    auto direct = Mille::spawnMilleRecord(directPath.string(), true, false);
    if (!direct) {
      throw std::runtime_error("official direct writer unavailable");
    }
    for (std::uint64_t sequence = 0; sequence < 2; ++sequence) {
      CapturingRecord capture;
      fillRecord(capture, static_cast<double>(sequence));
      fillRecord(*direct, static_cast<double>(sequence));
      direct->writeRecord();
      const eic::telescope::streaming::mille::TrackRecordMetadata metadata{
          streamId, sequence, 17, 100 + sequence, model, configuration,
          geometry, 2};
      auto serialized =
          eic::telescope::streaming::mille::serializeTrackRecord(
              capture.getBuffer(), metadata);
      const auto decoded =
          eic::telescope::streaming::mille::deserializeTrackRecord(
              serialized.bytes);
      if (decoded.metadata.sequence != sequence ||
          decoded.buffer.intBuffer != capture.getBuffer().intBuffer ||
          decoded.buffer.doubleBuffer != capture.getBuffer().doubleBuffer) {
        throw std::runtime_error("EMIL exact round trip failed");
      }
      payloads.push_back(std::move(serialized));
    }
    direct->flushOutputFile();
  }

  auto receiverOptions =
      eic::telescope::streaming::transport::TransportOptions{};
  receiverOptions.timeout = std::chrono::milliseconds(2000);
  receiverOptions.deliveryMode = eic::telescope::streaming::transport::
      DeliveryMode::acknowledgedRequestReply;
  receiverOptions.deferAcknowledgement = true;
  std::promise<std::string> endpointPromise;
  auto endpointFuture = endpointPromise.get_future();
  std::promise<void> collectedPromise;
  auto collectedFuture = collectedPromise.get_future();
  std::thread collector([&] {
    try {
      eic::telescope::streaming::transport::TimeframeReceiver receiver(
          "tcp://127.0.0.1:*", receiverOptions);
      endpointPromise.set_value(receiver.boundEndpoint());
      eic::telescope::streaming::mille::CollectorConfig config{
          collectedPath, reportPath, payloads.size(), model, configuration,
          geometry, 3};
      (void)eic::telescope::streaming::mille::collectMilleRecords(receiver,
                                                                  config);
      collectedPromise.set_value();
    } catch (...) {
      try {
        endpointPromise.set_exception(std::current_exception());
      } catch (...) {
      }
      collectedPromise.set_exception(std::current_exception());
    }
  });

  try {
    auto output = eicrecon::telescope::makeZmqMilleRecordOutput(
        {endpointFuture.get(), streamId, model, configuration, geometry, 2000,
         3});
    for (std::uint64_t sequence = 0; sequence < payloads.size(); ++sequence) {
      auto &record = output->beginRecord({17, 100 + sequence});
      fillRecord(record, static_cast<double>(sequence));
      output->commitRecord(2);
    }
    output->finish();
    collectedFuture.get();
  } catch (...) {
    collector.join();
    throw;
  }
  collector.join();

  if (!std::filesystem::is_regular_file(reportPath) ||
      bytes(directPath) != bytes(collectedPath)) {
    throw std::runtime_error(
        "official direct and collected Mille binaries are not byte-identical");
  }
  std::filesystem::remove_all(directory);
  return 0;
}
