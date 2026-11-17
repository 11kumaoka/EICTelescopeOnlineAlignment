#pragma once

#include <JANA/JEventSource.h>
#include <JANA/JEventSourceGeneratorT.h>

#include <cstdint>
#include <memory>
#include <string>
#include <unordered_map>

class JApplication;

class TelescopeZmqEventSource final : public JEventSource {
public:
  TelescopeZmqEventSource(std::string resourceName, JApplication *application);
  ~TelescopeZmqEventSource() override;

  void Open() override;
  void Close() override;
  Result Emit(JEvent &event) override;

  static std::string GetDescription();

private:
  class ReceiverWorker;

  Parameter<int> m_timeoutMs{
      this, "timeout_ms", 250,
      "ZeroMQ receive-poll and empty-FIFO retry interval in milliseconds"};
  Parameter<std::uint64_t> m_maxPayloadBytes{
      this, "max_payload_bytes", 64ULL * 1024ULL * 1024ULL,
      "Maximum accepted FlatBuffers payload size"};
  Parameter<int> m_highWaterMark{this, "high_water_mark", 10,
                                 "ZeroMQ receive high-water mark"};

  std::unique_ptr<ReceiverWorker> m_receiver;
  std::string m_endpoint;
  std::unordered_map<std::string, std::string> m_collectionTypes;
  bool m_finished{};
};

template <>
double JEventSourceGeneratorT<TelescopeZmqEventSource>::CheckOpenable(
    std::string resourceName);
