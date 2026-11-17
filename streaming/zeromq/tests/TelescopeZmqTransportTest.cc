#include "TelescopeZmqTransport.h"
#include "TelescopeTimeframeSerialization.h"

#include <edm4eic/RawTrackerHitCollection.h>
#include <edm4hep/EventHeaderCollection.h>
#include <podio/Frame.h>
#include <zmq.hpp>

#include <array>
#include <chrono>
#include <cstdint>
#include <cstring>
#include <exception>
#include <iostream>
#include <span>
#include <stdexcept>
#include <string>
#include <thread>
#include <vector>

namespace {

podio::Frame fixture() {
  podio::Frame frame;
  edm4hep::EventHeaderCollection headers;
  auto header = headers.create();
  header.setEventNumber(37);
  header.setRunNumber(4);
  header.setTimeStamp(123456789);
  header.setWeight(-0.0);
  header.addToWeights(0.25);
  frame.put(std::move(headers), "EventHeader");

  edm4eic::RawTrackerHitCollection hits;
  auto first = hits.create();
  first.setCellID(0x123456789abcdef0ULL);
  first.setCharge(-12);
  first.setTimeStamp(88);
  auto second = hits.create();
  second.setCellID(42);
  second.setCharge(91);
  second.setTimeStamp(-73);
  frame.put(std::move(hits), "TelescopeChannels");
  return frame;
}

void writeU16(std::array<std::uint8_t, 32> &bytes, std::size_t offset,
              std::uint16_t value) {
  bytes[offset] = static_cast<std::uint8_t>((value >> 8U) & 0xffU);
  bytes[offset + 1] = static_cast<std::uint8_t>(value & 0xffU);
}

void writeU64(std::array<std::uint8_t, 32> &bytes, std::size_t offset,
              std::uint64_t value) {
  for (std::size_t i = 0; i < 8; ++i) {
    bytes[offset + i] = static_cast<std::uint8_t>(
        (value >> static_cast<unsigned>((7 - i) * 8)) & 0xffULL);
  }
}

std::uint64_t readU64(const std::uint8_t *bytes, std::size_t offset) {
  std::uint64_t value = 0;
  for (std::size_t i = 0; i < 8; ++i) {
    value = (value << 8U) | static_cast<std::uint64_t>(bytes[offset + i]);
  }
  return value;
}

std::array<std::uint8_t, 32>
makeRawHeader(std::uint8_t kind, std::uint8_t encoding, std::uint64_t streamId,
              std::uint64_t sequence, std::uint64_t payloadBytes) {
  std::array<std::uint8_t, 32> header{};
  header[0] = 'E';
  header[1] = 'T';
  header[2] = 'Z';
  header[3] = '1';
  writeU16(header, 4,
           eic::telescope::streaming::transport::kAcknowledgedProtocolVersion);
  header[6] = kind;
  header[7] = encoding;
  writeU64(header, 8, streamId);
  writeU64(header, 16, sequence);
  writeU64(header, 24, payloadBytes);
  return header;
}

struct RawRequest {
  std::uint8_t kind{};
  std::uint64_t streamId{};
  std::uint64_t sequence{};
  std::vector<std::uint8_t> payload;
};

RawRequest receiveRawRequest(zmq::socket_t &socket) {
  zmq::message_t headerFrame;
  if (!socket.recv(headerFrame) || headerFrame.size() != 32 ||
      !socket.get(zmq::sockopt::rcvmore)) {
    throw std::runtime_error("raw protocol v3 request headerが不正です");
  }
  zmq::message_t payloadFrame;
  if (!socket.recv(payloadFrame) || socket.get(zmq::sockopt::rcvmore)) {
    throw std::runtime_error("raw protocol v3 request payloadが不正です");
  }
  const auto *header = static_cast<const std::uint8_t *>(headerFrame.data());
  if (header[0] != 'E' || header[1] != 'T' || header[2] != 'Z' ||
      header[3] != '1' || header[6] == 3 ||
      readU64(header, 24) != payloadFrame.size()) {
    throw std::runtime_error("raw protocol v3 request envelopeが不正です");
  }
  std::vector<std::uint8_t> payload(payloadFrame.size());
  if (!payload.empty()) {
    std::memcpy(payload.data(), payloadFrame.data(), payload.size());
  }
  return {header[6], readU64(header, 8), readU64(header, 16),
          std::move(payload)};
}

void sendRawAcknowledgement(zmq::socket_t &socket, const RawRequest &request) {
  const auto header =
      makeRawHeader(3, 0, request.streamId, request.sequence, 0);
  const std::array<std::uint8_t, 0> empty{};
  if (!socket.send(zmq::buffer(header), zmq::send_flags::sndmore) ||
      !socket.send(zmq::buffer(empty), zmq::send_flags::none)) {
    throw std::runtime_error("raw protocol v3 ACK送信がtimeoutしました");
  }
}

void sendRawRequestAndRequireAck(zmq::socket_t &socket, std::uint8_t kind,
                                 std::uint64_t streamId, std::uint64_t sequence,
                                 std::span<const std::uint8_t> payload) {
  const auto encoding = kind == 1 ? 1U : 0U;
  const auto header =
      makeRawHeader(kind, encoding, streamId, sequence, payload.size());
  if (!socket.send(zmq::buffer(header), zmq::send_flags::sndmore) ||
      !socket.send(zmq::buffer(payload.data(), payload.size()),
                   zmq::send_flags::none)) {
    throw std::runtime_error("raw protocol v3 request送信がtimeoutしました");
  }
  zmq::message_t ackHeader;
  zmq::message_t ackPayload;
  if (!socket.recv(ackHeader) || ackHeader.size() != 32 ||
      !socket.get(zmq::sockopt::rcvmore) || !socket.recv(ackPayload) ||
      socket.get(zmq::sockopt::rcvmore) || !ackPayload.empty()) {
    throw std::runtime_error("raw protocol v3 ACK受信が不正です");
  }
  const auto *ack = static_cast<const std::uint8_t *>(ackHeader.data());
  if (ack[6] != 3 || readU64(ack, 8) != streamId ||
      readU64(ack, 16) != sequence || readU64(ack, 24) != 0) {
    throw std::runtime_error("raw protocol v3 ACK内容が一致しません");
  }
}

void sendRawData(std::string endpoint, std::span<const std::uint8_t> payload,
                 std::uint64_t sequence, std::uint64_t declaredPayloadBytes,
                 std::uint16_t protocolVersion = 1) {
  std::array<std::uint8_t, 32> header{};
  header[0] = 'E';
  header[1] = 'T';
  header[2] = 'Z';
  header[3] = '1';
  writeU16(header, 4, protocolVersion);
  header[6] = 1; // DATA
  header[7] = 1; // Telescope FlatBuffer
  writeU64(header, 8, 7);
  writeU64(header, 16, sequence);
  writeU64(header, 24, declaredPayloadBytes);

  zmq::context_t context(1);
  zmq::socket_t socket(context, zmq::socket_type::push);
  socket.set(zmq::sockopt::linger, 1000);
  socket.set(zmq::sockopt::sndtimeo, 1000);
  socket.set(zmq::sockopt::immediate, 1);
  socket.connect(endpoint);
  if (!socket.send(zmq::buffer(header), zmq::send_flags::sndmore) ||
      !socket.send(zmq::buffer(payload.data(), payload.size()),
                   zmq::send_flags::none)) {
    throw std::runtime_error("不完全streamのテスト送信がtimeoutしました");
  }
}

} // namespace

int main() {
  try {
    auto source = fixture();
    const auto serialized =
        eic::telescope::streaming::serializeTimeframe(source, "events", 11);
    eic::telescope::streaming::transport::TransportOptions options;
    options.timeout = std::chrono::milliseconds(3000);

    eic::telescope::streaming::transport::TimeframeReceiver receiver(
        "tcp://127.0.0.1:*", options);
    eic::telescope::streaming::transport::TimeframeSender sender(
        receiver.boundEndpoint(), options);
    constexpr std::uint64_t streamId = 0x1020304050607080ULL;
    sender.sendSingleTimeframe(serialized.bytes, streamId);
    auto received = receiver.receiveSingleTimeframe();

    if (received.streamId != streamId || received.payload != serialized.bytes) {
      throw std::runtime_error("ZeroMQでpayload bytesが変化しました");
    }
    auto decoded =
        eic::telescope::streaming::deserializeTimeframe(received.payload);
    eic::telescope::streaming::requireEquivalentFrames(source, decoded.frame);
    if (decoded.metadata.sourceEntry != 11 ||
        decoded.metadata.rawTrackerHitObjects != 2) {
      throw std::runtime_error("復元metadataが一致しません");
    }

    eic::telescope::streaming::transport::TimeframeReceiver streamReceiver(
        "tcp://127.0.0.1:*", options);
    eic::telescope::streaming::transport::TimeframeSender streamSender(
        streamReceiver.boundEndpoint(), options);
    const std::vector<eic::telescope::streaming::SerializedTimeframe>
        streamPayloads{
            eic::telescope::streaming::serializeTimeframe(source, "events", 20),
            eic::telescope::streaming::serializeTimeframe(source, "events", 21),
            eic::telescope::streaming::serializeTimeframe(source, "events",
                                                          22)};
    constexpr std::uint64_t multiStreamId = 0xa0b0c0d0e0f00123ULL;
    streamSender.beginStream(multiStreamId);
    for (const auto &payload : streamPayloads) {
      streamSender.sendTimeframe(payload.bytes);
    }
    streamSender.endStream();

    for (std::size_t i = 0; i < streamPayloads.size(); ++i) {
      const auto message = streamReceiver.receiveNext();
      if (message.kind !=
              eic::telescope::streaming::transport::StreamMessageKind::data ||
          message.streamId != multiStreamId || message.sequence != i ||
          message.payload != streamPayloads[i].bytes) {
        throw std::runtime_error("複数event streamのDATA順序が一致しません");
      }
    }
    const auto streamEnd = streamReceiver.receiveNext();
    if (streamEnd.kind != eic::telescope::streaming::transport::
                              StreamMessageKind::endOfStream ||
        streamEnd.streamId != multiStreamId ||
        streamEnd.sequence != streamPayloads.size() ||
        !streamEnd.payload.empty()) {
      throw std::runtime_error("複数event streamのEOSが一致しません");
    }

    // Regression for finite bulk senders: protocol v2 can close while a
    // PUSH-local queue still contains messages. Protocol v3 must not return
    // from any send until the REP-side transport has received and validated
    // the complete request.
    auto acknowledgedOptions = options;
    acknowledgedOptions.deliveryMode = eic::telescope::streaming::transport::
        DeliveryMode::acknowledgedRequestReply;
    eic::telescope::streaming::transport::TimeframeReceiver
        acknowledgedReceiver("tcp://127.0.0.1:*", acknowledgedOptions);
    constexpr std::uint64_t acknowledgedStreamId = 0x33445566778899aaULL;
    constexpr std::size_t acknowledgedEvents = 1000;
    std::exception_ptr acknowledgedReceiverError;
    std::size_t acknowledgedReceived = 0;
    std::jthread acknowledgedThread([&] {
      try {
        for (std::size_t i = 0; i < acknowledgedEvents; ++i) {
          const auto message = acknowledgedReceiver.receiveNext();
          if (message.kind != eic::telescope::streaming::transport::
                                  StreamMessageKind::data ||
              message.streamId != acknowledgedStreamId ||
              message.sequence != i || message.payload != serialized.bytes) {
            throw std::runtime_error(
                "protocol v3 DATAまたはACK順序が一致しません");
          }
          ++acknowledgedReceived;
        }
        const auto eos = acknowledgedReceiver.receiveNext();
        if (eos.kind != eic::telescope::streaming::transport::
                            StreamMessageKind::endOfStream ||
            eos.streamId != acknowledgedStreamId ||
            eos.sequence != acknowledgedEvents || !eos.payload.empty()) {
          throw std::runtime_error("protocol v3 EOSが一致しません");
        }
      } catch (...) {
        acknowledgedReceiverError = std::current_exception();
      }
    });
    eic::telescope::streaming::transport::TimeframeSender acknowledgedSender(
        acknowledgedReceiver.boundEndpoint(), acknowledgedOptions);
    acknowledgedSender.beginStream(acknowledgedStreamId);
    for (std::size_t i = 0; i < acknowledgedEvents; ++i) {
      acknowledgedSender.sendTimeframe(serialized.bytes);
    }
    acknowledgedSender.endStream();
    acknowledgedThread.join();
    if (acknowledgedReceiverError != nullptr) {
      std::rethrow_exception(acknowledgedReceiverError);
    }
    if (acknowledgedReceived != acknowledgedEvents) {
      throw std::runtime_error("protocol v3でeventが欠落しました");
    }

    // A delayed reply must make the REQ sender retry the same sequence. The
    // correlation option must reject replies for abandoned attempts.
    zmq::context_t delayedContext(1);
    zmq::socket_t delayedServer(delayedContext, zmq::socket_type::rep);
    delayedServer.set(zmq::sockopt::linger, 0);
    delayedServer.set(zmq::sockopt::rcvtimeo, 2000);
    delayedServer.set(zmq::sockopt::sndtimeo, 2000);
    delayedServer.bind("tcp://127.0.0.1:*");
    const auto delayedEndpoint = delayedServer.get(zmq::sockopt::last_endpoint);
    std::exception_ptr delayedServerError;
    std::size_t delayedDataRequests = 0;
    std::jthread delayedThread([&] {
      try {
        for (;;) {
          const auto request = receiveRawRequest(delayedServer);
          if (request.kind == 1) {
            ++delayedDataRequests;
            if (delayedDataRequests == 1) {
              std::this_thread::sleep_for(std::chrono::milliseconds(140));
            }
          }
          sendRawAcknowledgement(delayedServer, request);
          if (request.kind == 2) {
            break;
          }
        }
      } catch (...) {
        delayedServerError = std::current_exception();
      }
    });
    auto retryOptions = acknowledgedOptions;
    retryOptions.timeout = std::chrono::milliseconds(50);
    retryOptions.acknowledgementRetries = 5;
    std::exception_ptr delayedSenderError;
    std::uint64_t delayedRetryCount = 0;
    try {
      eic::telescope::streaming::transport::TimeframeSender retryingSender(
          delayedEndpoint, retryOptions);
      retryingSender.beginStream(0x9988776655443322ULL);
      retryingSender.sendTimeframe(serialized.bytes);
      retryingSender.endStream();
      delayedRetryCount = retryingSender.acknowledgementRetryCount();
    } catch (...) {
      delayedSenderError = std::current_exception();
    }
    delayedThread.join();
    if (delayedServerError != nullptr) {
      std::rethrow_exception(delayedServerError);
    }
    if (delayedSenderError != nullptr) {
      std::rethrow_exception(delayedSenderError);
    }
    if (delayedDataRequests < 2 || delayedRetryCount == 0) {
      throw std::runtime_error("protocol v3 ACK timeout後に再送しませんでした");
    }

    // The production receiver must re-ACK an exact duplicate without
    // returning it to the caller a second time.
    eic::telescope::streaming::transport::TimeframeReceiver duplicateReceiver(
        "tcp://127.0.0.1:*", acknowledgedOptions);
    constexpr std::uint64_t duplicateStreamId = 0x1234432112344321ULL;
    std::exception_ptr duplicateReceiverError;
    std::vector<std::uint64_t> deliveredSequences;
    std::jthread duplicateThread([&] {
      try {
        for (std::size_t i = 0; i < 2; ++i) {
          const auto message = duplicateReceiver.receiveNext();
          if (message.kind !=
              eic::telescope::streaming::transport::StreamMessageKind::data) {
            throw std::runtime_error("duplicate試験でDATA以外を受信しました");
          }
          deliveredSequences.push_back(message.sequence);
        }
        const auto eos = duplicateReceiver.receiveNext();
        if (eos.kind != eic::telescope::streaming::transport::
                            StreamMessageKind::endOfStream ||
            eos.sequence != 2) {
          throw std::runtime_error("duplicate試験のEOSが一致しません");
        }
      } catch (...) {
        duplicateReceiverError = std::current_exception();
      }
    });
    zmq::context_t duplicateContext(1);
    zmq::socket_t duplicateClient(duplicateContext, zmq::socket_type::req);
    duplicateClient.set(zmq::sockopt::linger, 0);
    duplicateClient.set(zmq::sockopt::sndtimeo, 2000);
    duplicateClient.set(zmq::sockopt::rcvtimeo, 2000);
    duplicateClient.connect(duplicateReceiver.boundEndpoint());
    sendRawRequestAndRequireAck(duplicateClient, 1, duplicateStreamId, 0,
                                serialized.bytes);
    sendRawRequestAndRequireAck(duplicateClient, 1, duplicateStreamId, 0,
                                serialized.bytes);
    sendRawRequestAndRequireAck(duplicateClient, 1, duplicateStreamId, 1,
                                serialized.bytes);
    sendRawRequestAndRequireAck(duplicateClient, 2, duplicateStreamId, 2, {});
    duplicateThread.join();
    if (duplicateReceiverError != nullptr) {
      std::rethrow_exception(duplicateReceiverError);
    }
    if (deliveredSequences != std::vector<std::uint64_t>{0, 1}) {
      throw std::runtime_error(
          "protocol v3 duplicate DATAを上位へ配送しました");
    }

    bool refusedLegacyOnAcknowledgedSocket = false;
    try {
      eic::telescope::streaming::transport::TimeframeSender invalidSender(
          acknowledgedReceiver.boundEndpoint(), acknowledgedOptions);
      invalidSender.sendSingleTimeframe(serialized.bytes, 1);
    } catch (const std::runtime_error &) {
      refusedLegacyOnAcknowledgedSocket = true;
    }
    if (!refusedLegacyOnAcknowledgedSocket) {
      throw std::runtime_error("ACK mode socketがlegacy v1送信を受理しました");
    }

    options.timeout = std::chrono::milliseconds(250);
    eic::telescope::streaming::transport::TimeframeReceiver incompleteReceiver(
        "tcp://127.0.0.1:*", options);
    sendRawData(incompleteReceiver.boundEndpoint(), serialized.bytes, 0,
                serialized.bytes.size());
    bool refusedIncompleteStream = false;
    try {
      (void)incompleteReceiver.receiveSingleTimeframe();
    } catch (const eic::telescope::streaming::transport::TransportTimeout &) {
      throw std::runtime_error(
          "legacy v1のEOS欠落が回復可能timeoutとして報告されました");
    } catch (const std::runtime_error &) {
      refusedIncompleteStream = true;
    }
    if (!refusedIncompleteStream) {
      throw std::runtime_error("EOSのないstreamを正常終了として受理しました");
    }

    options.timeout = std::chrono::milliseconds(50);
    eic::telescope::streaming::transport::TimeframeReceiver idleReceiver(
        "tcp://127.0.0.1:*", options);
    bool reportedRecoverableTimeout = false;
    try {
      (void)idleReceiver.receiveSingleTimeframe();
    } catch (const eic::telescope::streaming::transport::TransportTimeout &) {
      reportedRecoverableTimeout = true;
    }
    if (!reportedRecoverableTimeout) {
      throw std::runtime_error("DATA待機timeoutが型付きで報告されませんでした");
    }

    eic::telescope::streaming::transport::TimeframeReceiver pausedReceiver(
        "tcp://127.0.0.1:*", options);
    eic::telescope::streaming::transport::TimeframeSender pausedSender(
        pausedReceiver.boundEndpoint(), options);
    pausedSender.beginStream(0x55aaULL);
    pausedSender.sendTimeframe(serialized.bytes);
    const auto beforePause = pausedReceiver.receiveNext();
    if (beforePause.kind !=
            eic::telescope::streaming::transport::StreamMessageKind::data ||
        beforePause.sequence != 0) {
      throw std::runtime_error("pause前のv2 DATAが一致しません");
    }
    bool reportedInterEventTimeout = false;
    try {
      (void)pausedReceiver.receiveNext();
    } catch (const eic::telescope::streaming::transport::TransportTimeout &) {
      reportedInterEventTimeout = true;
    }
    if (!reportedInterEventTimeout) {
      throw std::runtime_error(
          "event間timeoutが正常待機として報告されませんでした");
    }
    pausedSender.sendTimeframe(serialized.bytes);
    pausedSender.endStream();
    const auto afterPause = pausedReceiver.receiveNext();
    const auto pausedEnd = pausedReceiver.receiveNext();
    if (afterPause.kind !=
            eic::telescope::streaming::transport::StreamMessageKind::data ||
        afterPause.sequence != 1 ||
        pausedEnd.kind != eic::telescope::streaming::transport::
                              StreamMessageKind::endOfStream ||
        pausedEnd.sequence != 2) {
      throw std::runtime_error(
          "event間timeout後にstreamを再開できませんでした");
    }

    options.timeout = std::chrono::milliseconds(1000);
    eic::telescope::streaming::transport::TimeframeReceiver sequenceReceiver(
        "tcp://127.0.0.1:*", options);
    sendRawData(sequenceReceiver.boundEndpoint(), serialized.bytes, 9,
                serialized.bytes.size());
    bool refusedWrongSequence = false;
    try {
      (void)sequenceReceiver.receiveSingleTimeframe();
    } catch (const std::runtime_error &) {
      refusedWrongSequence = true;
    }
    if (!refusedWrongSequence) {
      throw std::runtime_error("不正なDATA sequenceを受理しました");
    }

    eic::telescope::streaming::transport::TimeframeReceiver gapReceiver(
        "tcp://127.0.0.1:*", options);
    sendRawData(gapReceiver.boundEndpoint(), serialized.bytes, 1,
                serialized.bytes.size(), 2);
    bool refusedV2Gap = false;
    try {
      (void)gapReceiver.receiveNext();
    } catch (const std::runtime_error &) {
      refusedV2Gap = true;
    }
    if (!refusedV2Gap) {
      throw std::runtime_error("v2 streamの先頭sequence gapを受理しました");
    }

    eic::telescope::streaming::transport::TimeframeReceiver sizeReceiver(
        "tcp://127.0.0.1:*", options);
    sendRawData(sizeReceiver.boundEndpoint(), serialized.bytes, 0,
                serialized.bytes.size() + 1);
    bool refusedWrongSize = false;
    try {
      (void)sizeReceiver.receiveSingleTimeframe();
    } catch (const std::runtime_error &) {
      refusedWrongSize = true;
    }
    if (!refusedWrongSize) {
      throw std::runtime_error("不正なpayload sizeを受理しました");
    }

    options.maxPayloadBytes = 1;
    eic::telescope::streaming::transport::TimeframeReceiver tinyReceiver(
        "tcp://127.0.0.1:*", options);
    eic::telescope::streaming::transport::TimeframeSender tinySender(
        tinyReceiver.boundEndpoint(), options);
    const std::array<std::uint8_t, 1> tinyPayload{42};
    tinySender.sendSingleTimeframe(tinyPayload, 8);
    const auto tinyReceived = tinyReceiver.receiveSingleTimeframe();
    if (tinyReceived.payload.size() != 1 || tinyReceived.payload[0] != 42) {
      throw std::runtime_error("小さいpayload上限でtransportが破損しました");
    }

    std::cout << "ZeroMQ localhost往復テストに成功しました\n";
    return 0;
  } catch (const std::exception &error) {
    std::cerr << "ZeroMQ localhost往復テストに失敗しました: " << error.what()
              << '\n';
    return 1;
  }
}
