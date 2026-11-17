#include "TelescopeTimeframeSerialization.h"

#include <nlohmann/json.hpp>
#include <podio/Frame.h>
#include <podio/ROOTReader.h>
#include <podio/ROOTWriter.h>

#include <algorithm>
#include <chrono>
#include <cstddef>
#include <cstdint>
#include <filesystem>
#include <fstream>
#include <iostream>
#include <limits>
#include <span>
#include <stdexcept>
#include <string>

namespace {

using Clock = std::chrono::steady_clock;

struct TimingSummary {
  std::uint64_t totalNanoseconds{};
  std::uint64_t minimumNanoseconds{std::numeric_limits<std::uint64_t>::max()};
  std::size_t iterations{};
};

template <typename Operation>
TimingSummary measure(std::size_t iterations, Operation operation) {
  TimingSummary result;
  result.iterations = iterations;
  for (std::size_t i = 0; i < iterations; ++i) {
    const auto begin = Clock::now();
    operation();
    const auto elapsed = std::chrono::duration_cast<std::chrono::nanoseconds>(
        Clock::now() - begin).count();
    const auto nanoseconds = static_cast<std::uint64_t>(elapsed);
    result.totalNanoseconds += nanoseconds;
    result.minimumNanoseconds = std::min(result.minimumNanoseconds, nanoseconds);
  }
  return result;
}

nlohmann::json timingJson(const TimingSummary& timing) {
  return {{"iterations", timing.iterations},
          {"total_ns", timing.totalNanoseconds},
          {"mean_ns", static_cast<double>(timing.totalNanoseconds) /
                          static_cast<double>(timing.iterations)},
          {"minimum_ns", timing.minimumNanoseconds}};
}

void requireNew(const std::filesystem::path& path) {
  if (std::filesystem::exists(path)) {
    throw std::runtime_error("出力先は新規パスである必要があります: " + path.string());
  }
}

void writeBytes(const std::filesystem::path& path,
                std::span<const std::uint8_t> bytes) {
  std::ofstream output(path, std::ios::binary | std::ios::out);
  output.exceptions(std::ios::failbit | std::ios::badbit);
  output.write(reinterpret_cast<const char*>(bytes.data()),
               static_cast<std::streamsize>(bytes.size()));
}

podio::Frame readFrame(const std::filesystem::path& path,
                       std::uint64_t entry) {
  podio::ROOTReader reader;
  reader.openFile(path.string());
  if (entry >= reader.getEntries("events")) {
    throw std::runtime_error("指定イベントはROOTファイルに存在しません");
  }
  auto data = reader.readEntry("events", static_cast<unsigned>(entry));
  if (!data) {
    throw std::runtime_error("PODIO ROOTイベントを読み込めませんでした");
  }
  return podio::Frame(std::move(data));
}

} // namespace

int main(int argc, char** argv) {
  if (argc != 6 && argc != 7) {
    std::cerr << "使用法: TelescopeFrameRoundTrip INPUT_ROOT EVENT_INDEX "
                 "NEW_MESSAGE_FB NEW_OUTPUT_ROOT NEW_REPORT_JSON "
                 "[BENCHMARK_ITERATIONS]\n";
    return 64;
  }

  try {
    const std::filesystem::path inputPath = argv[1];
    const auto eventIndex = std::stoull(argv[2]);
    const std::filesystem::path messagePath = argv[3];
    const std::filesystem::path outputRootPath = argv[4];
    const std::filesystem::path reportPath = argv[5];
    const std::size_t iterations = argc == 7 ? std::stoull(argv[6]) : 1000;
    if (iterations == 0) {
      throw std::runtime_error("BENCHMARK_ITERATIONSは1以上が必要です");
    }
    const auto normalizedMessage =
        std::filesystem::absolute(messagePath).lexically_normal();
    const auto normalizedRoot =
        std::filesystem::absolute(outputRootPath).lexically_normal();
    const auto normalizedReport =
        std::filesystem::absolute(reportPath).lexically_normal();
    if (normalizedMessage == normalizedRoot ||
        normalizedMessage == normalizedReport ||
        normalizedRoot == normalizedReport) {
      throw std::runtime_error("3つの出力先には異なるパスが必要です");
    }
    requireNew(messagePath);
    requireNew(outputRootPath);
    requireNew(reportPath);

    auto source = readFrame(inputPath, eventIndex);
    const auto sourceCollectionOrder = source.getAvailableCollections();
    auto serialized = eic::telescope::streaming::serializeTimeframe(
        source, "events", eventIndex);
    auto decoded = eic::telescope::streaming::deserializeTimeframe(serialized.bytes);
    eic::telescope::streaming::requireEquivalentFrames(source, decoded.frame);
    if (decoded.metadata.collectionNames != sourceCollectionOrder) {
      throw std::runtime_error("message内のcollection順序が変化しました");
    }
    writeBytes(messagePath, serialized.bytes);

    {
      podio::ROOTWriter writer(outputRootPath.string());
      writer.writeFrame(decoded.frame, "events");
      writer.finish();
    }
    auto rootReadback = readFrame(outputRootPath, 0);
    eic::telescope::streaming::requireEquivalentFrames(source, rootReadback);

    std::size_t benchmarkChecksum = 0;
    const auto serializationTiming = measure(iterations, [&] {
      auto sample = eic::telescope::streaming::serializeTimeframe(
          source, "events", eventIndex);
      benchmarkChecksum += sample.bytes.size();
    });
    const auto deserializationTiming = measure(iterations, [&] {
      auto sample = eic::telescope::streaming::deserializeTimeframe(
          serialized.bytes);
      benchmarkChecksum += sample.metadata.rawTrackerHitObjects;
    });

    const auto& allocations = serialized.builderAllocations;
    nlohmann::json report = {
        {"schema", "telescope-flatbuffers-roundtrip-v1"},
        {"passed", true},
        {"logical_timeframe", "one PODIO ROOT event"},
        {"input",
         {{"path", inputPath.string()},
          {"category", "events"},
          {"event_index", eventIndex},
          {"collection_order", sourceCollectionOrder}}},
        {"message",
         {{"path", messagePath.string()},
          {"file_identifier",
           std::string(eic::telescope::streaming::kFileIdentifier)},
          {"wire_schema_version",
           eic::telescope::streaming::kWireSchemaVersion},
          {"payload_bytes", serialized.bytes.size()},
          {"collection_order", serialized.metadata.collectionNames},
          {"event_header_objects", serialized.metadata.eventHeaderObjects},
          {"raw_tracker_hit_objects",
           serialized.metadata.rawTrackerHitObjects}}},
        {"output_root", outputRootPath.string()},
        {"exact_checks",
         {{"collection_names", true},
          {"collection_order_in_message", true},
          {"podio_types_and_schema_versions", true},
          {"event_header_all_fields_and_weights_bitwise", true},
          {"frame_generic_parameters_empty", true},
          {"raw_hit_fields_and_order", true},
          {"root_readback", true}}},
        {"timing",
         {{"scope", "ROOT I/O excluded; deserialize includes verifier and EDM materialization"},
          {"serialization", timingJson(serializationTiming)},
          {"deserialization", timingJson(deserializationTiming)}}},
        {"memory",
         {{"flatbuffer_builder_allocation_calls", allocations.allocationCalls},
          {"flatbuffer_builder_deallocation_calls_before_builder_destruction",
           allocations.deallocationCalls},
          {"flatbuffer_builder_total_requested_bytes",
           allocations.totalRequestedBytes},
          {"flatbuffer_builder_peak_live_bytes", allocations.peakLiveBytes},
          {"edm_objects_materialized",
           serialized.metadata.eventHeaderObjects +
               serialized.metadata.rawTrackerHitObjects},
          {"scope",
           "FlatBufferBuilder allocations are counted exactly; PODIO internal allocation calls are not exposed"}}},
        {"benchmark_checksum", benchmarkChecksum},
        {"dependencies",
         {{"transport", "none"}, {"jana2", false}, {"zeromq", false}}}};

    std::ofstream reportOutput(reportPath);
    reportOutput.exceptions(std::ios::failbit | std::ios::badbit);
    reportOutput << report.dump(2) << '\n';
    std::cout << "FlatBuffers往復検証に成功しました: "
              << serialized.bytes.size() << " bytes, "
              << serialized.metadata.rawTrackerHitObjects << " RawTrackerHits\n";
    return 0;
  } catch (const std::exception& error) {
    std::cerr << "FlatBuffers往復検証に失敗しました: " << error.what() << '\n';
    return 1;
  }
}
