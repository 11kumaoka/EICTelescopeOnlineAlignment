#include "MilleZmqRecordOutput.h"

#include "MilleRecordOutput.h"
#include "MilleTrackRecordSerialization.h"
#include "TelescopeZmqTransport.h"

#include <Mille/MilleRecord.h>

#include <chrono>
#include <condition_variable>
#include <future>
#include <mutex>
#include <stdexcept>
#include <thread>
#include <utility>
#include <vector>

namespace eicrecon::telescope {
namespace {

namespace protocol = eic::telescope::streaming;

class ZmqMilleRecordOutput final : public MilleRecordOutput,
                                   private Mille::MilleRecord {
public:
  explicit ZmqMilleRecordOutput(MilleZmqOutputConfig config)
      : Mille::MilleRecord(true, false), m_config(std::move(config)) {
    if (m_config.endpoint.empty() || m_config.streamId == 0) {
      throw std::invalid_argument("Mille ZMQ endpoint/stream ID is invalid");
    }
    std::promise<void> ready;
    auto readyFuture = ready.get_future();
    m_thread = std::thread(&ZmqMilleRecordOutput::ioLoop, this,
                           std::move(ready));
    try {
      readyFuture.get();
    } catch (...) {
      if (m_thread.joinable()) {
        m_thread.join();
      }
      throw;
    }
  }

  ~ZmqMilleRecordOutput() override { stopWithoutEos(); }

  Mille::MilleRecord &beginRecord(MilleRecordMetadata metadata) override {
    if (m_finished || m_active) {
      throw std::runtime_error("Invalid ZMQ Mille record lifecycle");
    }
    m_metadata = metadata;
    m_active = true;
    return *this;
  }

  void commitRecord(std::uint32_t scalarMeasurements) override {
    if (!m_active || m_finished) {
      throw std::runtime_error("No active ZMQ Mille record to commit");
    }
    protocol::mille::TrackRecordMetadata metadata{
        m_config.streamId,
        m_nextSequence,
        m_metadata.runNumber,
        m_metadata.eventNumber,
        m_config.modelId,
        m_config.configurationId,
        m_config.geometryId,
        scalarMeasurements};
    auto serialized = protocol::mille::serializeTrackRecord(buffer_, metadata);
    submit(CommandKind::data, std::move(serialized.bytes));
    resetBuffers();
    newSetIfNeeded();
    ++m_nextSequence;
    m_active = false;
  }

  void finish() override {
    if (m_finished) {
      return;
    }
    if (m_active) {
      throw std::runtime_error("Cannot finish with an uncommitted Mille record");
    }
    try {
      submit(CommandKind::endOfStream, {});
      m_finished = true;
    } catch (...) {
      if (m_thread.joinable()) {
        m_thread.join();
      }
      throw;
    }
    if (m_thread.joinable()) {
      m_thread.join();
    }
  }

private:
  enum class CommandKind { data, endOfStream, stop };
  struct Command {
    CommandKind kind{};
    std::vector<std::uint8_t> payload;
    std::promise<void> completion;
  };

  // These virtuals only satisfy MilleRecord's output interface. Lifecycle is
  // owned by MilleRecordOutput so scalar-count metadata cannot be omitted.
  void writeRecord() override {
    throw std::logic_error("Use MilleRecordOutput::commitRecord");
  }
  void flushOutputFile() override {}
  void resetOutputFile() override {
    throw std::logic_error("A ZMQ Mille stream cannot be reset");
  }

  void submit(CommandKind kind, std::vector<std::uint8_t> payload) {
    auto command = std::make_shared<Command>();
    command->kind = kind;
    command->payload = std::move(payload);
    auto completion = command->completion.get_future();
    {
      std::lock_guard lock(m_mutex);
      if (m_stopped || m_command) {
        throw std::runtime_error("Mille ZMQ sender thread is unavailable");
      }
      m_command = command;
    }
    m_condition.notify_one();
    completion.get();
  }

  void ioLoop(std::promise<void> ready) noexcept {
    try {
      protocol::transport::TransportOptions options;
      options.timeout = std::chrono::milliseconds(m_config.timeoutMilliseconds);
      options.acknowledgementRetries = m_config.acknowledgementRetries;
      options.deliveryMode =
          protocol::transport::DeliveryMode::acknowledgedRequestReply;
      protocol::transport::TimeframeSender sender(m_config.endpoint, options);
      sender.beginStream(m_config.streamId);
      ready.set_value();
      for (;;) {
        std::shared_ptr<Command> command;
        {
          std::unique_lock lock(m_mutex);
          m_condition.wait(lock, [&] { return static_cast<bool>(m_command); });
          command = std::move(m_command);
          m_command.reset();
        }
        try {
          if (command->kind == CommandKind::data) {
            sender.sendPayload(
                command->payload,
                protocol::transport::PayloadEncoding::milleRecordFlatBuffer);
            command->completion.set_value();
          } else if (command->kind == CommandKind::endOfStream) {
            sender.endStream();
            command->completion.set_value();
            break;
          } else {
            command->completion.set_value();
            break;
          }
        } catch (...) {
          {
            std::lock_guard lock(m_mutex);
            m_stopped = true;
          }
          command->completion.set_exception(std::current_exception());
          return;
        }
      }
    } catch (...) {
      {
        std::lock_guard lock(m_mutex);
        m_stopped = true;
      }
      try {
        ready.set_exception(std::current_exception());
      } catch (...) {
      }
      return;
    }
    {
      std::lock_guard lock(m_mutex);
      m_stopped = true;
    }
  }

  void stopWithoutEos() noexcept {
    if (!m_thread.joinable()) {
      return;
    }
    try {
      bool submitStop = false;
      {
        std::lock_guard lock(m_mutex);
        submitStop = !m_stopped && !m_command;
      }
      if (submitStop) {
        try {
          submit(CommandKind::stop, {});
        } catch (...) {
        }
      }
      m_condition.notify_all();
      m_thread.join();
    } catch (...) {
    }
  }

  MilleZmqOutputConfig m_config;
  MilleRecordMetadata m_metadata;
  std::uint64_t m_nextSequence{};
  bool m_active{};
  bool m_finished{};
  std::mutex m_mutex;
  std::condition_variable m_condition;
  std::shared_ptr<Command> m_command;
  bool m_stopped{};
  std::thread m_thread;
};

} // namespace

std::unique_ptr<MilleRecordOutput>
makeZmqMilleRecordOutput(MilleZmqOutputConfig config) {
  return std::make_unique<ZmqMilleRecordOutput>(std::move(config));
}

} // namespace eicrecon::telescope
