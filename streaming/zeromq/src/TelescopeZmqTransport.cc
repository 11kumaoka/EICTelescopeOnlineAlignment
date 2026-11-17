#include "TelescopeZmqTransport.h"

#include <zmq.hpp>

#include <algorithm>
#include <array>
#include <cerrno>
#include <cstring>
#include <limits>
#include <stdexcept>
#include <string>
#include <utility>

namespace eic::telescope::streaming::transport {

TransportTimeout::TransportTimeout(const std::string &message)
    : std::runtime_error("Telescope ZeroMQ transport: " + message) {}

namespace {

constexpr std::array<std::uint8_t, 4> kMagic{'E', 'T', 'Z', '1'};

enum class MessageKind : std::uint8_t {
  data = 1,
  endOfStream = 2,
  acknowledgement = 3,
};

enum class PayloadEncoding : std::uint8_t {
  none = 0,
  telescopeFlatBuffer = 1,
};

struct Header {
  std::uint16_t version{};
  MessageKind kind{};
  PayloadEncoding encoding{};
  std::uint64_t streamId{};
  std::uint64_t sequence{};
  std::uint64_t payloadBytes{};
};

struct MultipartMessage {
  Header header;
  std::vector<std::uint8_t> payload;
};

[[noreturn]] void fail(const std::string &message) {
  throw std::runtime_error("Telescope ZeroMQ transport: " + message);
}

void validateOptions(const TransportOptions &options) {
  if (options.timeout.count() <= 0 ||
      options.timeout.count() > std::numeric_limits<int>::max()) {
    fail("timeoutは1ms以上かつint範囲内である必要があります");
  }
  if (options.maxPayloadBytes == 0 ||
      options.maxPayloadBytes > static_cast<std::uint64_t>(
                                    std::numeric_limits<std::int64_t>::max())) {
    fail("maxPayloadBytesが無効です");
  }
  if (options.highWaterMark <= 0) {
    fail("highWaterMarkは1以上である必要があります");
  }
  if (options.acknowledgementRetries < 0 ||
      options.acknowledgementRetries > 1000) {
    fail("acknowledgementRetriesは0以上1000以下である必要があります");
  }
  if (options.deliveryMode != DeliveryMode::pushPull &&
      options.deliveryMode != DeliveryMode::acknowledgedRequestReply) {
    fail("deliveryModeが無効です");
  }
}

void writeU16(std::array<std::uint8_t, kTransportHeaderBytes> &bytes,
              std::size_t offset, std::uint16_t value) {
  bytes[offset] = static_cast<std::uint8_t>((value >> 8U) & 0xffU);
  bytes[offset + 1] = static_cast<std::uint8_t>(value & 0xffU);
}

void writeU64(std::array<std::uint8_t, kTransportHeaderBytes> &bytes,
              std::size_t offset, std::uint64_t value) {
  for (std::size_t i = 0; i < 8; ++i) {
    const auto shift = static_cast<unsigned>((7 - i) * 8);
    bytes[offset + i] = static_cast<std::uint8_t>((value >> shift) & 0xffULL);
  }
}

std::uint16_t readU16(const std::uint8_t *bytes, std::size_t offset) {
  return static_cast<std::uint16_t>(
      (static_cast<std::uint16_t>(bytes[offset]) << 8U) |
      static_cast<std::uint16_t>(bytes[offset + 1]));
}

std::uint64_t readU64(const std::uint8_t *bytes, std::size_t offset) {
  std::uint64_t value = 0;
  for (std::size_t i = 0; i < 8; ++i) {
    value = (value << 8U) | static_cast<std::uint64_t>(bytes[offset + i]);
  }
  return value;
}

std::array<std::uint8_t, kTransportHeaderBytes>
encodeHeader(const Header &header) {
  std::array<std::uint8_t, kTransportHeaderBytes> bytes{};
  std::copy(kMagic.begin(), kMagic.end(), bytes.begin());
  writeU16(bytes, 4, header.version);
  bytes[6] = static_cast<std::uint8_t>(header.kind);
  bytes[7] = static_cast<std::uint8_t>(header.encoding);
  writeU64(bytes, 8, header.streamId);
  writeU64(bytes, 16, header.sequence);
  writeU64(bytes, 24, header.payloadBytes);
  return bytes;
}

Header decodeHeader(const zmq::message_t &message) {
  if (message.size() != kTransportHeaderBytes) {
    fail("transport headerサイズが32 bytesではありません");
  }
  const auto *bytes = static_cast<const std::uint8_t *>(message.data());
  if (!std::equal(kMagic.begin(), kMagic.end(), bytes)) {
    fail("transport magicが一致しません");
  }
  const auto version = readU16(bytes, 4);
  if (version != kLegacyProtocolVersion && version != kProtocolVersion &&
      version != kAcknowledgedProtocolVersion) {
    fail("未対応のtransport protocol versionです");
  }
  const auto kind = static_cast<MessageKind>(bytes[6]);
  const auto encoding = static_cast<PayloadEncoding>(bytes[7]);
  if (kind != MessageKind::data && kind != MessageKind::endOfStream &&
      kind != MessageKind::acknowledgement) {
    fail("未対応のmessage kindです");
  }
  if (encoding != PayloadEncoding::none &&
      encoding != PayloadEncoding::telescopeFlatBuffer) {
    fail("未対応のpayload encodingです");
  }
  return {
      version,           kind, encoding, readU64(bytes, 8), readU64(bytes, 16),
      readU64(bytes, 24)};
}

void configureCommon(zmq::socket_t &socket, const TransportOptions &options) {
  const auto timeout = static_cast<int>(options.timeout.count());
  socket.set(zmq::sockopt::linger, timeout);
  socket.set(zmq::sockopt::sndtimeo, timeout);
  socket.set(zmq::sockopt::rcvtimeo, timeout);
}

void sendPart(zmq::socket_t &socket, zmq::const_buffer buffer,
              zmq::send_flags flags) {
  for (;;) {
    try {
      if (!socket.send(buffer, flags)) {
        throw TransportTimeout("送信がtimeoutしました");
      }
      return;
    } catch (const zmq::error_t &error) {
      if (error.num() != EINTR) {
        throw;
      }
    }
  }
}

void sendMultipart(zmq::socket_t &socket, const Header &header,
                   std::span<const std::uint8_t> payload) {
  const auto headerBytes = encodeHeader(header);
  sendPart(socket, zmq::buffer(headerBytes), zmq::send_flags::sndmore);
  if (payload.empty()) {
    const std::array<std::uint8_t, 0> empty{};
    sendPart(socket, zmq::buffer(empty), zmq::send_flags::none);
  } else {
    sendPart(socket, zmq::buffer(payload.data(), payload.size()),
             zmq::send_flags::none);
  }
}

zmq::message_t receivePart(zmq::socket_t &socket) {
  for (;;) {
    try {
      zmq::message_t message;
      if (!socket.recv(message, zmq::recv_flags::none)) {
        throw TransportTimeout("受信がtimeoutしました");
      }
      return message;
    } catch (const zmq::error_t &error) {
      if (error.num() != EINTR) {
        throw;
      }
    }
  }
}

MultipartMessage receiveMultipart(zmq::socket_t &socket,
                                  const TransportOptions &options) {
  auto headerFrame = receivePart(socket);
  if (!socket.get(zmq::sockopt::rcvmore)) {
    fail("payload frameがありません");
  }
  zmq::message_t payloadFrame;
  try {
    payloadFrame = receivePart(socket);
  } catch (const TransportTimeout &) {
    fail("multipart payloadの受信がtimeoutしました");
  }
  if (socket.get(zmq::sockopt::rcvmore)) {
    fail("余分なmultipart frameを受信しました");
  }

  const auto header = decodeHeader(headerFrame);
  if (header.payloadBytes != payloadFrame.size()) {
    fail("headerのpayload sizeと受信サイズが一致しません");
  }
  if (header.payloadBytes > options.maxPayloadBytes) {
    fail("payloadが設定上限を超えています");
  }
  std::vector<std::uint8_t> payload(payloadFrame.size());
  if (!payload.empty()) {
    std::memcpy(payload.data(), payloadFrame.data(), payload.size());
  }
  return {header, std::move(payload)};
}

void requireAcknowledgement(zmq::socket_t &socket,
                            const TransportOptions &options,
                            std::uint64_t streamId, std::uint64_t sequence) {
  const auto acknowledgement = receiveMultipart(socket, options);
  if (acknowledgement.header.version != kAcknowledgedProtocolVersion ||
      acknowledgement.header.kind != MessageKind::acknowledgement ||
      acknowledgement.header.encoding != PayloadEncoding::none ||
      acknowledgement.header.streamId != streamId ||
      acknowledgement.header.sequence != sequence ||
      !acknowledgement.payload.empty()) {
    fail("receiverから正しいprotocol v3 ACKを受信できませんでした");
  }
}

void sendAcknowledgement(zmq::socket_t &socket, std::uint64_t streamId,
                         std::uint64_t sequence) {
  try {
    sendMultipart(socket,
                  {kAcknowledgedProtocolVersion, MessageKind::acknowledgement,
                   PayloadEncoding::none, streamId, sequence, 0},
                  {});
  } catch (const TransportTimeout &) {
    fail("protocol v3 ACK送信がtimeoutしました");
  }
}

} // namespace

class TimeframeSender::Impl {
public:
  enum class State {
    idle,
    streaming,
    finished,
    failed,
  };

  Impl(std::string_view endpoint, TransportOptions selectedOptions)
      : options(std::move(selectedOptions)), context(1),
        socket(context,
               options.deliveryMode == DeliveryMode::acknowledgedRequestReply
                   ? zmq::socket_type::req
                   : zmq::socket_type::push) {
    validateOptions(options);
    configureCommon(socket, options);
    socket.set(zmq::sockopt::sndhwm, options.highWaterMark);
    if (options.deliveryMode == DeliveryMode::acknowledgedRequestReply) {
      socket.set(zmq::sockopt::rcvhwm, options.highWaterMark);
      socket.set(zmq::sockopt::maxmsgsize,
                 static_cast<std::int64_t>(std::max<std::uint64_t>(
                     options.maxPayloadBytes, kTransportHeaderBytes)));
      // RELAXED permits resending after a missing reply. CORRELATE prevents a
      // delayed ACK for an abandoned request from satisfying a later retry or
      // the next sequence.
      socket.set(zmq::sockopt::req_relaxed, 1);
      socket.set(zmq::sockopt::req_correlate, 1);
    }
    socket.set(zmq::sockopt::immediate, 1);
    socket.connect(std::string(endpoint));
  }

  TransportOptions options;
  zmq::context_t context;
  zmq::socket_t socket;
  State state{State::idle};
  std::uint64_t streamId{};
  std::uint64_t nextSequence{};
  std::uint64_t acknowledgementRetryCount{};
  void sendAcknowledgedRequest(const Header &header,
                               std::span<const std::uint8_t> payload) {
    const auto maximumAttempts =
        static_cast<unsigned>(options.acknowledgementRetries) + 1U;
    for (unsigned attempt = 1; attempt <= maximumAttempts; ++attempt) {
      sendMultipart(socket, header, payload);
      try {
        requireAcknowledgement(socket, options, header.streamId,
                               header.sequence);
        return;
      } catch (const TransportTimeout &) {
        if (attempt == maximumAttempts) {
          throw TransportTimeout(
              "protocol v3 ACKがtimeoutしました: sequence=" +
              std::to_string(header.sequence) +
              ", attempts=" + std::to_string(maximumAttempts));
        }
        ++acknowledgementRetryCount;
      }
    }
  }
};

TimeframeSender::TimeframeSender(std::string_view connectEndpoint,
                                 TransportOptions options) {
  if (connectEndpoint.empty()) {
    fail("connect endpointが空です");
  }
  try {
    m_impl = std::make_unique<Impl>(connectEndpoint, std::move(options));
  } catch (const zmq::error_t &error) {
    fail("sender初期化に失敗しました: " + std::string(error.what()));
  }
}

TimeframeSender::~TimeframeSender() = default;

void TimeframeSender::beginStream(std::uint64_t streamId) {
  if (m_impl->state != Impl::State::idle) {
    fail("streamは1つのTimeframeSenderにつき1回だけ開始できます");
  }
  m_impl->streamId = streamId;
  m_impl->nextSequence = 0;
  m_impl->state = Impl::State::streaming;
}

void TimeframeSender::sendTimeframe(std::span<const std::uint8_t> payload) {
  if (m_impl->state != Impl::State::streaming) {
    fail("sendTimeframeの前にbeginStreamが必要です");
  }
  if (payload.empty()) {
    fail("空のFlatBuffers payloadは送信できません");
  }
  if (payload.size() > m_impl->options.maxPayloadBytes) {
    fail("payloadが設定上限を超えています");
  }
  if (m_impl->nextSequence == std::numeric_limits<std::uint64_t>::max()) {
    fail("DATA sequenceが上限に達したためEOSを送信できません");
  }

  try {
    const auto version =
        m_impl->options.deliveryMode == DeliveryMode::acknowledgedRequestReply
            ? kAcknowledgedProtocolVersion
            : kProtocolVersion;
    const Header header{version,
                        MessageKind::data,
                        PayloadEncoding::telescopeFlatBuffer,
                        m_impl->streamId,
                        m_impl->nextSequence,
                        payload.size()};
    if (m_impl->options.deliveryMode ==
        DeliveryMode::acknowledgedRequestReply) {
      m_impl->sendAcknowledgedRequest(header, payload);
    } else {
      sendMultipart(m_impl->socket, header, payload);
    }
    ++m_impl->nextSequence;
  } catch (const zmq::error_t &error) {
    m_impl->state = Impl::State::failed;
    fail("送信に失敗しました: " + std::string(error.what()));
  } catch (...) {
    m_impl->state = Impl::State::failed;
    throw;
  }
}

void TimeframeSender::endStream() {
  if (m_impl->state != Impl::State::streaming) {
    fail("endStreamの前にbeginStreamが必要です");
  }
  try {
    const auto version =
        m_impl->options.deliveryMode == DeliveryMode::acknowledgedRequestReply
            ? kAcknowledgedProtocolVersion
            : kProtocolVersion;
    const Header header{
        version,          MessageKind::endOfStream, PayloadEncoding::none,
        m_impl->streamId, m_impl->nextSequence,     0};
    if (m_impl->options.deliveryMode ==
        DeliveryMode::acknowledgedRequestReply) {
      m_impl->sendAcknowledgedRequest(header, {});
    } else {
      sendMultipart(m_impl->socket, header, {});
    }
    m_impl->state = Impl::State::finished;
  } catch (const zmq::error_t &error) {
    m_impl->state = Impl::State::failed;
    fail("EOS送信に失敗しました: " + std::string(error.what()));
  } catch (...) {
    m_impl->state = Impl::State::failed;
    throw;
  }
}

std::uint64_t TimeframeSender::acknowledgementRetryCount() const {
  return m_impl->acknowledgementRetryCount;
}

void TimeframeSender::sendSingleTimeframe(std::span<const std::uint8_t> payload,
                                          std::uint64_t streamId) {
  if (m_impl->options.deliveryMode != DeliveryMode::pushPull) {
    fail("legacy single timeframe送信はPUSH/PULL mode専用です");
  }
  if (m_impl->state != Impl::State::idle) {
    fail("single timeframe送信には未使用のTimeframeSenderが必要です");
  }
  if (payload.empty()) {
    fail("空のFlatBuffers payloadは送信できません");
  }
  if (payload.size() > m_impl->options.maxPayloadBytes) {
    fail("payloadが設定上限を超えています");
  }
  m_impl->state = Impl::State::failed;
  try {
    sendMultipart(m_impl->socket,
                  {kLegacyProtocolVersion, MessageKind::data,
                   PayloadEncoding::telescopeFlatBuffer, streamId, 0,
                   payload.size()},
                  payload);
    sendMultipart(m_impl->socket,
                  {kLegacyProtocolVersion, MessageKind::endOfStream,
                   PayloadEncoding::none, streamId, 1, 0},
                  {});
    m_impl->state = Impl::State::finished;
  } catch (const zmq::error_t &error) {
    fail("送信に失敗しました: " + std::string(error.what()));
  } catch (...) {
    throw;
  }
}

class TimeframeReceiver::Impl {
public:
  Impl(std::string_view endpoint, TransportOptions selectedOptions)
      : options(std::move(selectedOptions)), context(1),
        socket(context,
               options.deliveryMode == DeliveryMode::acknowledgedRequestReply
                   ? zmq::socket_type::rep
                   : zmq::socket_type::pull) {
    validateOptions(options);
    configureCommon(socket, options);
    socket.set(zmq::sockopt::rcvhwm, options.highWaterMark);
    if (options.deliveryMode == DeliveryMode::acknowledgedRequestReply) {
      socket.set(zmq::sockopt::sndhwm, options.highWaterMark);
    }
    socket.set(zmq::sockopt::maxmsgsize,
               static_cast<std::int64_t>(std::max<std::uint64_t>(
                   options.maxPayloadBytes, kTransportHeaderBytes)));
    socket.bind(std::string(endpoint));
    endpointName = socket.get(zmq::sockopt::last_endpoint);
  }

  TransportOptions options;
  zmq::context_t context;
  zmq::socket_t socket;
  std::string endpointName;
  std::uint16_t version{};
  std::uint64_t streamId{};
  std::uint64_t expectedSequence{};
  Header lastAcknowledgedHeader{};
  std::vector<std::uint8_t> lastAcknowledgedPayload;
  bool hasLastAcknowledgedMessage{};
  bool pendingLegacyEos{};
  bool finished{};
  bool failed{};
};

TimeframeReceiver::TimeframeReceiver(std::string_view bindEndpoint,
                                     TransportOptions options) {
  if (bindEndpoint.empty()) {
    fail("bind endpointが空です");
  }
  try {
    m_impl = std::make_unique<Impl>(bindEndpoint, std::move(options));
  } catch (const zmq::error_t &error) {
    fail("receiver初期化に失敗しました: " + std::string(error.what()));
  }
}

TimeframeReceiver::~TimeframeReceiver() = default;

const std::string &TimeframeReceiver::boundEndpoint() const {
  return m_impl->endpointName;
}

ReceivedStreamMessage TimeframeReceiver::receiveNext() {
  if (m_impl->failed) {
    fail("protocol error後のTimeframeReceiverは再利用できません");
  }
  if (m_impl->pendingLegacyEos) {
    m_impl->pendingLegacyEos = false;
    m_impl->finished = true;
    return {StreamMessageKind::endOfStream, m_impl->streamId, 1, {}};
  }
  if (m_impl->finished) {
    fail("EOS受信後にreceiveNextは呼び出せません");
  }

  try {
    for (;;) {
      auto message = receiveMultipart(m_impl->socket, m_impl->options);
      const bool acknowledged = m_impl->options.deliveryMode ==
                                DeliveryMode::acknowledgedRequestReply;
      if (acknowledged &&
          message.header.version != kAcknowledgedProtocolVersion) {
        fail("ACK mode receiverはprotocol v3だけを受信できます");
      }
      if (!acknowledged &&
          message.header.version == kAcknowledgedProtocolVersion) {
        fail("protocol v3にはACK mode receiverが必要です");
      }
      if (message.header.kind == MessageKind::acknowledgement) {
        fail("senderからACK messageを受信しました");
      }
      if (m_impl->version == 0) {
        m_impl->version = message.header.version;
        m_impl->streamId = message.header.streamId;
      } else if (message.header.version != m_impl->version) {
        fail("stream途中でtransport protocol versionが変化しました");
      } else if (message.header.streamId != m_impl->streamId) {
        fail("stream途中でstream IDが変化しました");
      }

      if (acknowledged && m_impl->hasLastAcknowledgedMessage &&
          message.header.sequence + 1U == m_impl->expectedSequence &&
          message.header.sequence == m_impl->lastAcknowledgedHeader.sequence) {
        const auto &last = m_impl->lastAcknowledgedHeader;
        if (message.header.version != last.version ||
            message.header.kind != last.kind ||
            message.header.encoding != last.encoding ||
            message.header.streamId != last.streamId ||
            message.header.payloadBytes != last.payloadBytes ||
            message.payload != m_impl->lastAcknowledgedPayload) {
          fail("同じsequenceで内容の異なる再送を受信しました");
        }
        sendAcknowledgement(m_impl->socket, message.header.streamId,
                            message.header.sequence);
        continue;
      }

      if (message.header.sequence != m_impl->expectedSequence) {
        fail("stream sequenceが期待値と一致しません");
      }

      if (message.header.kind == MessageKind::endOfStream) {
        if (message.header.encoding != PayloadEncoding::none ||
            !message.payload.empty()) {
          fail("EOS messageのencodingまたはpayloadが不正です");
        }
        if (m_impl->version == kLegacyProtocolVersion) {
          fail("legacy protocol v1はDATA(0)の前にEOSを送信できません");
        }
        if (acknowledged) {
          m_impl->lastAcknowledgedHeader = message.header;
          m_impl->lastAcknowledgedPayload = message.payload;
          m_impl->hasLastAcknowledgedMessage = true;
          sendAcknowledgement(m_impl->socket, message.header.streamId,
                              message.header.sequence);
        }
        m_impl->finished = true;
        return {StreamMessageKind::endOfStream,
                message.header.streamId,
                message.header.sequence,
                {}};
      }

      if (message.header.encoding != PayloadEncoding::telescopeFlatBuffer ||
          message.payload.empty()) {
        fail("DATA messageのencodingまたはpayloadが不正です");
      }
      if (m_impl->expectedSequence ==
          std::numeric_limits<std::uint64_t>::max()) {
        fail("DATA sequenceが上限に達したためEOSを受信できません");
      }

      const auto sequence = m_impl->expectedSequence;
      ++m_impl->expectedSequence;

      if (m_impl->version == kLegacyProtocolVersion) {
        if (sequence != 0) {
          fail("legacy protocol v1は1件のDATAだけを受信できます");
        }
        MultipartMessage eos;
        try {
          eos = receiveMultipart(m_impl->socket, m_impl->options);
        } catch (const TransportTimeout &) {
          fail("legacy DATA受信後にEOSがtimeoutしました");
        }
        if (eos.header.version != kLegacyProtocolVersion ||
            eos.header.kind != MessageKind::endOfStream ||
            eos.header.encoding != PayloadEncoding::none ||
            eos.header.streamId != message.header.streamId ||
            eos.header.sequence != 1 || !eos.payload.empty()) {
          fail("legacy protocol v1の正しいEOS(1)を受信できませんでした");
        }
        m_impl->pendingLegacyEos = true;
      }

      if (acknowledged) {
        m_impl->lastAcknowledgedHeader = message.header;
        m_impl->lastAcknowledgedPayload = message.payload;
        m_impl->hasLastAcknowledgedMessage = true;
        sendAcknowledgement(m_impl->socket, message.header.streamId, sequence);
      }

      return {StreamMessageKind::data, message.header.streamId, sequence,
              std::move(message.payload)};
    }
  } catch (const TransportTimeout &) {
    throw;
  } catch (const zmq::error_t &error) {
    m_impl->failed = true;
    fail("受信に失敗しました: " + std::string(error.what()));
  } catch (...) {
    m_impl->failed = true;
    throw;
  }
}

ReceivedTimeframe TimeframeReceiver::receiveSingleTimeframe() {
  auto data = receiveNext();
  if (data.kind != StreamMessageKind::data || data.sequence != 0) {
    m_impl->failed = true;
    fail("先頭messageがsequence 0のFlatBuffers DATAではありません");
  }

  ReceivedStreamMessage eos;
  try {
    eos = receiveNext();
  } catch (const TransportTimeout &) {
    m_impl->failed = true;
    fail("DATA受信後にEOSがtimeoutしました");
  }
  if (eos.kind != StreamMessageKind::endOfStream ||
      eos.streamId != data.streamId || eos.sequence != 1 ||
      !eos.payload.empty()) {
    m_impl->failed = true;
    fail("single timeframeに正しいsequence 1のEOSがありません");
  }
  return {data.streamId, std::move(data.payload)};
}

} // namespace eic::telescope::streaming::transport
