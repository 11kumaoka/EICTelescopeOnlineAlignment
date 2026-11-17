#include "MilleCollector.h"

#include "MilleTrackRecordSerialization.h"
#include "TelescopeZmqTransport.h"

#include <Mille/IMilleReader.h>
#include <Mille/MilleFactory.h>
#include <Mille/MilleRecord.h>

#include <nlohmann/json.hpp>

#include <algorithm>
#include <cerrno>
#include <cstring>
#include <fcntl.h>
#include <fstream>
#include <limits>
#include <memory>
#include <set>
#include <stdexcept>
#include <string>
#include <system_error>
#include <tuple>
#include <unistd.h>
#include <utility>
#include <vector>

namespace eic::telescope::streaming::mille {
namespace {

static_assert(sizeof(int) == 4);
static_assert(sizeof(float) == 4);
static_assert(sizeof(double) == 8);
static_assert(std::numeric_limits<float>::is_iec559);
static_assert(std::numeric_limits<double>::is_iec559);

[[noreturn]] void fail(const std::string &message) {
  throw std::runtime_error("Mille Collector: " + message);
}

class BufferReader final : public Mille::IMilleReader {
public:
  explicit BufferReader(const Mille::MilleBuffer &buffer) : m_buffer(buffer) {}

  bool open(const std::string &) override { return true; }
  void close() override {}
  void reset() override { m_consumed = false; }
  void rewindEntry() override { m_consumed = false; }
  void skipEntry() override { m_consumed = true; }

  std::pair<int, int> readToBuffer(double *doubles, float *floats,
                                   int *integers, int capacity) override {
    if (m_consumed) {
      return {0, 0};
    }
    if (!doubles || !floats || !integers || capacity < 0) {
      return {0, -1};
    }
    const auto size = m_buffer.intBuffer.size();
    if (size > static_cast<std::size_t>(std::numeric_limits<int>::max())) {
      return {0, -4};
    }
    if (static_cast<std::size_t>(capacity) < size) {
      return {static_cast<int>(size), -4};
    }
    std::copy(m_buffer.intBuffer.begin(), m_buffer.intBuffer.end(), integers);
    if (m_buffer.doublePrecision) {
      std::copy(m_buffer.doubleBuffer.begin(), m_buffer.doubleBuffer.end(),
                doubles);
    } else {
      std::copy(m_buffer.floatBuffer.begin(), m_buffer.floatBuffer.end(),
                floats);
    }
    m_consumed = true;
    return {static_cast<int>(size), m_buffer.doublePrecision ? 8 : 4};
  }

private:
  const Mille::MilleBuffer &m_buffer;
  bool m_consumed{};
};

void fsyncFile(const std::filesystem::path &path) {
  const int descriptor = ::open(path.c_str(), O_RDONLY);
  if (descriptor < 0) {
    fail("cannot open for fsync: " + path.string() + ": " +
         std::strerror(errno));
  }
  const int result = ::fsync(descriptor);
  const int saved = errno;
  ::close(descriptor);
  if (result != 0) {
    fail("fsync failed: " + path.string() + ": " + std::strerror(saved));
  }
}

void fsyncDirectory(const std::filesystem::path &directory) {
  const int descriptor = ::open(directory.c_str(), O_RDONLY);
  if (descriptor < 0) {
    fail("cannot open output directory for fsync");
  }
  const int result = ::fsync(descriptor);
  const int saved = errno;
  ::close(descriptor);
  if (result != 0) {
    fail("output directory fsync failed: " + std::string(std::strerror(saved)));
  }
}

void reserveNew(const std::filesystem::path &path) {
  const int descriptor =
      ::open(path.c_str(), O_WRONLY | O_CREAT | O_EXCL, 0644);
  if (descriptor < 0) {
    fail("path must be new: " + path.string());
  }
  ::close(descriptor);
}

void publishExclusive(const std::filesystem::path &temporary,
                      const std::filesystem::path &finalPath) {
  std::error_code error;
  std::filesystem::create_hard_link(temporary, finalPath, error);
  if (error) {
    fail("exclusive publication failed for " + finalPath.string() + ": " +
         error.message());
  }
  fsyncDirectory(finalPath.parent_path());
  std::filesystem::remove(temporary, error);
  if (error) {
    fail("cannot remove published temporary file: " + error.message());
  }
}

std::size_t verifyOfficialFile(const std::filesystem::path &path,
                               bool expectedDouble) {
  auto reader = Mille::spawnMilleReader(path.string());
  if (!reader || !reader->open(path.string())) {
    fail("official Mille reader could not open the collected output");
  }
  Mille::MilleBuffer buffer;
  buffer.resize(5000, true);
  std::size_t records = 0;
  for (;;) {
    auto [size, status] = reader->readToBuffer(buffer);
    if (status == -4) {
      buffer.resize(static_cast<std::size_t>(size), true);
      reader->rewindEntry();
      std::tie(size, status) = reader->readToBuffer(buffer);
    }
    if (status == 0) {
      break;
    }
    if (status < 0 || size <= 0 || (status == 8) != expectedDouble) {
      fail("official reader rejected the collected Mille binary");
    }
    ++records;
  }
  reader->close();
  return records;
}

} // namespace

CollectorResult collectMilleRecords(transport::TimeframeReceiver &receiver,
                                    const CollectorConfig &config) {
  if (config.expectedRecords == 0 || config.outputPath.empty() ||
      config.reportPath.empty()) {
    fail("expected record count and output paths are required");
  }
  if (config.outputPath.parent_path() != config.reportPath.parent_path()) {
    fail("output and report must share one publication directory");
  }
  if (std::filesystem::exists(config.outputPath) ||
      std::filesystem::exists(config.reportPath)) {
    fail("final output/report must not already exist");
  }
  const auto suffix = ".partial-" + std::to_string(::getpid());
  const auto partialOutput = config.outputPath.parent_path() /
      (config.outputPath.stem().string() + suffix +
       config.outputPath.extension().string());
  const auto partialReport = config.reportPath.parent_path() /
      (config.reportPath.stem().string() + suffix +
       config.reportPath.extension().string());
  reserveNew(partialOutput);

  std::unique_ptr<Mille::MilleRecord> writer;
  bool precisionSet = false;
  bool doublePrecision = false;
  std::uintmax_t expectedBytes = 0;
  std::uint64_t streamId = 0;
  std::size_t records = 0;
  std::size_t scalarMeasurements = 0;
  std::size_t payloadBytes = 0;
  std::set<std::pair<std::uint64_t, std::uint64_t>> eventIdentities;
  nlohmann::json events = nlohmann::json::array();
  int consecutiveTimeouts = 0;

  for (;;) {
    transport::ReceivedStreamMessage message;
    try {
      message = receiver.receiveNext();
      consecutiveTimeouts = 0;
    } catch (const transport::TransportTimeout &) {
      if (++consecutiveTimeouts >= config.maximumConsecutiveTimeouts) {
        fail("DATA/EOS idle deadline expired");
      }
      continue;
    }
    if (message.kind == transport::StreamMessageKind::endOfStream) {
      if (streamId == 0) {
        streamId = message.streamId;
      }
      if (message.streamId != streamId ||
          message.sequence != config.expectedRecords ||
          records != config.expectedRecords) {
        fail("EOS stream/sequence or exact record count is invalid");
      }
      if (!writer) {
        fail("stream contains no Mille records");
      }
      writer->flushOutputFile();
      writer.reset();
      if (std::filesystem::file_size(partialOutput) != expectedBytes) {
        fail("collected output size differs from canonical record sizes");
      }
      fsyncFile(partialOutput);
      if (verifyOfficialFile(partialOutput, doublePrecision) != records) {
        fail("official reader record count differs after collection");
      }
      publishExclusive(partialOutput, config.outputPath);

      nlohmann::json report = {
          {"schema", "telescope-mille-collector-report-v1"},
          {"passed", true},
          {"error", nullptr},
          {"protocol_version", 3},
          {"stream_id", streamId},
          {"records", records},
          {"records_received", records},
          {"records_written", records},
          {"expected_records", config.expectedRecords},
          {"eos_sequence", message.sequence},
          {"scalar_measurements", scalarMeasurements},
          {"payload_bytes", payloadBytes},
          {"output_bytes", expectedBytes},
          {"partial_path", partialOutput.string()},
          {"final_path", config.outputPath.string()},
          {"published", true},
          {"double_precision", doublePrecision},
          {"model_id", config.modelId},
          {"configuration_id", config.configurationId},
          {"geometry_id", config.geometryId},
          {"events", events}};
      reserveNew(partialReport);
      {
        std::ofstream output(partialReport,
                             std::ios::out | std::ios::trunc | std::ios::binary);
        output << report.dump(2) << '\n';
        output.flush();
        if (!output) {
          fail("collector report write failed");
        }
      }
      fsyncFile(partialReport);
      publishExclusive(partialReport, config.reportPath);
      receiver.acknowledgeLast();
      return {streamId, records, scalarMeasurements, expectedBytes};
    }

    if (message.encoding != transport::PayloadEncoding::milleRecordFlatBuffer) {
      fail("DATA payload is not an EMIL Mille record");
    }
    if (records >= config.expectedRecords) {
      fail("received more records than configured");
    }
    auto decoded = deserializeTrackRecord(message.payload);
    payloadBytes += message.payload.size();
    const auto &metadata = decoded.metadata;
    if (metadata.streamId != message.streamId ||
        metadata.sequence != message.sequence ||
        metadata.sequence != records) {
      fail("transport and FlatBuffers stream sequence differ");
    }
    if (records == 0) {
      streamId = message.streamId;
      doublePrecision = decoded.buffer.doublePrecision;
      precisionSet = true;
      writer = Mille::spawnMilleRecord(partialOutput, doublePrecision, false);
      if (!writer) {
        fail("cannot create official temporary Mille writer");
      }
    }
    if (!precisionSet || decoded.buffer.doublePrecision != doublePrecision ||
        message.streamId != streamId || metadata.modelId != config.modelId ||
        metadata.configurationId != config.configurationId ||
        metadata.geometryId != config.geometryId) {
      fail("record precision, stream, or scientific identity changed");
    }
    const auto identity =
        std::make_pair(metadata.runNumber, metadata.eventNumber);
    if (!eventIdentities.insert(identity).second) {
      fail("duplicate run/event identity");
    }
    BufferReader source(decoded.buffer);
    if (!writer->copyRecord(source)) {
      fail("official Mille copyRecord rejected a received record");
    }
    writer->writeRecord();
    writer->flushOutputFile();
    const auto entries = decoded.buffer.intBuffer.size();
    expectedBytes += sizeof(std::int32_t) +
                     entries * (doublePrecision ? sizeof(double)
                                                : sizeof(float)) +
                     entries * sizeof(std::int32_t);
    if (std::filesystem::file_size(partialOutput) != expectedBytes) {
      fail("temporary output did not reach the canonical flushed size");
    }
    fsyncFile(partialOutput);
    ++records;
    scalarMeasurements += metadata.scalarMeasurements;
    events.push_back({{"sequence", metadata.sequence},
                      {"run_number", metadata.runNumber},
                      {"event_number", metadata.eventNumber},
                      {"scalar_measurements", metadata.scalarMeasurements}});
    receiver.acknowledgeLast();
  }
}

} // namespace eic::telescope::streaming::mille
