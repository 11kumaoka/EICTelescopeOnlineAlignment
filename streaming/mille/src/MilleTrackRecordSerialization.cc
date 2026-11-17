#include "MilleTrackRecordSerialization.h"

#include "MilleTrackRecord_generated.h"

#include <flatbuffers/flatbuffers.h>

#include <algorithm>
#include <cmath>
#include <limits>
#include <stdexcept>

namespace eic::telescope::streaming::mille {
namespace {

namespace wire = eic::telescope::streaming::mille::fb;

[[noreturn]] void fail(const std::string &message) {
  throw std::runtime_error("Mille FlatBuffers: " + message);
}

bool validIdentity(const std::string &value) {
  return value.size() == 64 &&
         std::all_of(value.begin(), value.end(), [](unsigned char c) {
           return (c >= '0' && c <= '9') || (c >= 'a' && c <= 'f');
         });
}

void validateMetadata(const TrackRecordMetadata &metadata) {
  if (!validIdentity(metadata.modelId) ||
      !validIdentity(metadata.configurationId) ||
      !validIdentity(metadata.geometryId)) {
    fail("model/configuration/geometry identity must be lowercase SHA-256");
  }
  if (metadata.scalarMeasurements == 0) {
    fail("scalar measurement count must be positive");
  }
}

std::vector<double> values(const Mille::MilleBuffer &buffer) {
  if (buffer.doublePrecision) {
    return buffer.doubleBuffer;
  }
  std::vector<double> result;
  result.reserve(buffer.floatBuffer.size());
  for (const float value : buffer.floatBuffer) {
    result.push_back(static_cast<double>(value));
  }
  return result;
}

void validateBuffer(const Mille::MilleBuffer &buffer,
                    std::uint32_t scalarMeasurements) {
  const auto valueCount = buffer.doublePrecision ? buffer.doubleBuffer.size()
                                                 : buffer.floatBuffer.size();
  if (buffer.intBuffer.empty() || buffer.intBuffer.size() != valueCount) {
    fail("integer/value buffer sizes differ or are empty");
  }
  const double firstValue = buffer.doublePrecision
                                ? buffer.doubleBuffer.front()
                                : buffer.floatBuffer.front();
  if (buffer.intBuffer.front() != 0 || firstValue != 0.) {
    fail("record does not start with the official (0,0) marker");
  }
  const auto zeroLabels = static_cast<std::size_t>(std::count(
      buffer.intBuffer.begin(), buffer.intBuffer.end(), 0));
  if (zeroLabels != 1U + 2U * scalarMeasurements) {
    fail("record delimiter count differs from scalar measurement metadata");
  }
  if (buffer.doublePrecision) {
    if (!std::all_of(buffer.doubleBuffer.begin(), buffer.doubleBuffer.end(),
                     [](double value) { return std::isfinite(value); })) {
      fail("record contains a non-finite double value");
    }
  } else if (!std::all_of(buffer.floatBuffer.begin(), buffer.floatBuffer.end(),
                          [](float value) { return std::isfinite(value); })) {
    fail("record contains a non-finite float value");
  }
}

} // namespace

SerializedTrackRecord serializeTrackRecord(const Mille::MilleBuffer &buffer,
                                           const TrackRecordMetadata &metadata) {
  validateMetadata(metadata);
  validateBuffer(buffer, metadata.scalarMeasurements);
  const auto realValues = values(buffer);
  flatbuffers::FlatBufferBuilder builder;
  const auto model = builder.CreateString(metadata.modelId);
  const auto configuration = builder.CreateString(metadata.configurationId);
  const auto geometry = builder.CreateString(metadata.geometryId);
  const auto integers = builder.CreateVector(buffer.intBuffer);
  const auto reals = builder.CreateVector(realValues);
  const auto record = wire::CreateTrackRecord(
      builder, kWireSchemaVersion, metadata.streamId, metadata.sequence,
      metadata.runNumber, metadata.eventNumber, model, configuration, geometry,
      buffer.doublePrecision ? wire::StoragePrecision::Float64
                             : wire::StoragePrecision::Float32,
      metadata.scalarMeasurements, integers, reals);
  builder.Finish(record, wire::TrackRecordIdentifier());
  return {{builder.GetBufferPointer(),
           builder.GetBufferPointer() + builder.GetSize()}};
}

DecodedTrackRecord deserializeTrackRecord(
    std::span<const std::uint8_t> bytes) {
  if (bytes.empty() || bytes.size() > std::numeric_limits<std::uint32_t>::max()) {
    fail("payload size is invalid");
  }
  flatbuffers::Verifier verifier(bytes.data(), bytes.size());
  if (!wire::VerifyTrackRecordBuffer(verifier)) {
    fail("FlatBuffers verification failed");
  }
  const auto *record = wire::GetTrackRecord(bytes.data());
  if (record->wire_schema_version() != kWireSchemaVersion) {
    fail("unsupported wire schema version");
  }
  DecodedTrackRecord decoded;
  decoded.metadata = {
      record->stream_id(),          record->sequence(),
      record->run_number(),         record->event_number(),
      record->model_id()->str(),    record->configuration_id()->str(),
      record->geometry_id()->str(), record->scalar_measurements()};
  validateMetadata(decoded.metadata);
  if (record->precision() != wire::StoragePrecision::Float32 &&
      record->precision() != wire::StoragePrecision::Float64) {
    fail("unsupported storage precision");
  }
  decoded.buffer.doublePrecision =
      record->precision() == wire::StoragePrecision::Float64;
  decoded.buffer.intBuffer.assign(record->integer_buffer()->begin(),
                                  record->integer_buffer()->end());
  if (decoded.buffer.doublePrecision) {
    decoded.buffer.doubleBuffer.assign(record->value_buffer()->begin(),
                                       record->value_buffer()->end());
  } else {
    decoded.buffer.floatBuffer.reserve(record->value_buffer()->size());
    for (const double value : *record->value_buffer()) {
      const float narrowed = static_cast<float>(value);
      if (!std::isfinite(value) || static_cast<double>(narrowed) != value) {
        fail("Float32 record value is not exactly representable");
      }
      decoded.buffer.floatBuffer.push_back(narrowed);
    }
  }
  validateBuffer(decoded.buffer, decoded.metadata.scalarMeasurements);
  return decoded;
}

} // namespace eic::telescope::streaming::mille
