#pragma once

#include <chrono>
#include <cstddef>
#include <cstdint>
#include <memory>
#include <span>
#include <stdexcept>
#include <string>
#include <string_view>
#include <vector>

namespace eic::telescope::streaming::transport {

inline constexpr std::uint16_t kLegacyProtocolVersion = 1;
inline constexpr std::uint16_t kProtocolVersion = 2;
inline constexpr std::uint16_t kAcknowledgedProtocolVersion = 3;
inline constexpr std::size_t kTransportHeaderBytes = 32;

// A timeout before the next complete message starts is recoverable. Timeouts
// after a multipart message has started are converted to protocol errors. For
// legacy v1, DATA and EOS form one committed unit, so missing EOS is fatal.
class TransportTimeout final : public std::runtime_error {
public:
  explicit TransportTimeout(const std::string &message);
};

enum class DeliveryMode {
  pushPull,
  acknowledgedRequestReply,
};

struct TransportOptions {
  std::chrono::milliseconds timeout{5000};
  std::uint64_t maxPayloadBytes{64ULL * 1024ULL * 1024ULL};
  int highWaterMark{10};
  // Number of times protocol v3 may resend the same request after an ACK
  // timeout. Retries retain the stream id, sequence, kind, and payload and are
  // safe because the receiver validates and suppresses exact duplicates.
  int acknowledgementRetries{3};
  DeliveryMode deliveryMode{DeliveryMode::pushPull};
};

struct ReceivedTimeframe {
  std::uint64_t streamId{};
  std::vector<std::uint8_t> payload;
};

enum class StreamMessageKind {
  data,
  endOfStream,
};

struct ReceivedStreamMessage {
  StreamMessageKind kind{};
  std::uint64_t streamId{};
  std::uint64_t sequence{};
  std::vector<std::uint8_t> payload;
};

// Protocol v2 uses PUSH and sends DATA(0..N-1) followed by EOS(N). A successful
// return only means ZeroMQ accepted the message. Protocol v3 uses REQ and waits
// for a validated receiver ACK after every DATA and EOS. A missing ACK causes
// a bounded resend of the exact same request; the receiver re-ACKs and drops an
// exact duplicate so DATA is returned only once. The transport treats payload
// bytes as opaque; callers that label them as ETFB must validate them.
class TimeframeSender {
public:
  TimeframeSender(std::string_view connectEndpoint,
                  TransportOptions options = {});
  ~TimeframeSender();

  TimeframeSender(const TimeframeSender &) = delete;
  TimeframeSender &operator=(const TimeframeSender &) = delete;

  // Protocol v2/v3 streaming API. A stream contains zero or more DATA messages
  // followed by exactly one EOS message. TransportOptions::deliveryMode
  // selects unacknowledged v2 or receiver-acknowledged v3.
  void beginStream(std::uint64_t streamId);
  void sendTimeframe(std::span<const std::uint8_t> payload);
  void endStream();

  // Number of protocol-v3 requests resent after an ACK timeout. This remains
  // zero for legacy v1 and unacknowledged v2.
  std::uint64_t acknowledgementRetryCount() const;

  // Compatibility API: emits the original protocol v1 DATA(0) + EOS(1)
  // sequence so an existing single-timeframe receiver can still consume it.
  void sendSingleTimeframe(std::span<const std::uint8_t> payload,
                           std::uint64_t streamId);

private:
  class Impl;
  std::unique_ptr<Impl> m_impl;
};

// PULL-side endpoint for protocol v1/v2, or REP-side endpoint for acknowledged
// protocol v3. Each stream is deliberately single-producer and single-consumer.
// The returned payload remains opaque until the serialization layer verifies
// and deserializes it.
class TimeframeReceiver {
public:
  TimeframeReceiver(std::string_view bindEndpoint,
                    TransportOptions options = {});
  ~TimeframeReceiver();

  TimeframeReceiver(const TimeframeReceiver &) = delete;
  TimeframeReceiver &operator=(const TimeframeReceiver &) = delete;

  const std::string &boundEndpoint() const;
  ReceivedStreamMessage receiveNext();
  ReceivedTimeframe receiveSingleTimeframe();

private:
  class Impl;
  std::unique_ptr<Impl> m_impl;
};

} // namespace eic::telescope::streaming::transport
