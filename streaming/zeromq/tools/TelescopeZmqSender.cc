#include "TelescopeTimeframeSerialization.h"
#include "TelescopeZmqTransport.h"

#include <podio/Frame.h>
#include <podio/ROOTReader.h>

#include <charconv>
#include <chrono>
#include <cstddef>
#include <cstdint>
#include <filesystem>
#include <fstream>
#include <iostream>
#include <limits>
#include <random>
#include <stdexcept>
#include <string>
#include <string_view>
#include <utility>
#include <vector>

namespace {

struct CommandLine {
  enum class Mode { legacyFlatBuffer, flatBufferStream, rootStream };

  eic::telescope::streaming::transport::TransportOptions options;
  std::vector<std::filesystem::path> inputPaths;
  Mode mode{Mode::legacyFlatBuffer};
};

struct LoadedMessage {
  std::vector<std::uint8_t> bytes;
};

struct RootPassSummary {
  std::uint64_t events{};
  std::uint64_t totalPayloadBytes{};
};

struct FileIdentity {
  std::uintmax_t bytes{};
  std::filesystem::file_time_type modified{};

  bool operator==(const FileIdentity &) const = default;
};

std::chrono::milliseconds parseTimeout(std::string_view text) {
  int value{};
  const auto [end, error] =
      std::from_chars(text.data(), text.data() + text.size(), value);
  if (text.empty() || error != std::errc{} ||
      end != text.data() + text.size() || value <= 0) {
    throw std::runtime_error("TIMEOUT_MSは1以上かつint範囲内が必要です");
  }
  return std::chrono::milliseconds(value);
}

std::vector<std::uint8_t> readMessage(const std::filesystem::path &path,
                                      std::uint64_t maximumBytes) {
  const auto size = std::filesystem::file_size(path);
  if (size == 0 || size > maximumBytes) {
    throw std::runtime_error("入力FlatBuffersのサイズが無効です");
  }
  std::vector<std::uint8_t> bytes(static_cast<std::size_t>(size));
  std::ifstream input(path, std::ios::binary);
  input.exceptions(std::ios::failbit | std::ios::badbit);
  input.read(reinterpret_cast<char *>(bytes.data()),
             static_cast<std::streamsize>(bytes.size()));
  return bytes;
}

CommandLine parseCommandLine(int argc, char **argv) {
  CommandLine command;
  if (argc >= 3 && std::string_view(argv[2]) == "--root") {
    if (argc != 4 && argc != 5) {
      throw std::runtime_error("--rootの引数の数が正しくありません");
    }
    command.mode = CommandLine::Mode::rootStream;
    command.options.deliveryMode = eic::telescope::streaming::transport::
        DeliveryMode::acknowledgedRequestReply;
    command.inputPaths.emplace_back(argv[3]);
    if (argc == 5) {
      command.options.timeout = parseTimeout(argv[4]);
    }
    return command;
  }

  if (argc >= 3 && std::string_view(argv[2]) == "--stream") {
    command.mode = CommandLine::Mode::flatBufferStream;
    int inputBegin = 3;
    if (inputBegin < argc &&
        std::string_view(argv[inputBegin]) == "--timeout-ms") {
      if (inputBegin + 1 >= argc) {
        throw std::runtime_error("--timeout-msには値が必要です");
      }
      command.options.timeout = parseTimeout(argv[inputBegin + 1]);
      inputBegin += 2;
    }
    if (inputBegin >= argc) {
      throw std::runtime_error("streamには1件以上のINPUT_FBが必要です");
    }
    for (int i = inputBegin; i < argc; ++i) {
      command.inputPaths.emplace_back(argv[i]);
    }
    return command;
  }

  if (argc != 3 && argc != 4) {
    throw std::runtime_error("引数の数が正しくありません");
  }
  command.inputPaths.emplace_back(argv[2]);
  if (argc == 4) {
    command.options.timeout = parseTimeout(argv[3]);
  }
  return command;
}

FileIdentity identifyFile(const std::filesystem::path &path) {
  if (!std::filesystem::is_regular_file(path)) {
    throw std::runtime_error("入力ROOTが通常ファイルではありません: " +
                             path.string());
  }
  return {std::filesystem::file_size(path),
          std::filesystem::last_write_time(path)};
}

template <typename Consumer>
RootPassSummary readRootEvents(const std::filesystem::path &path,
                               std::uint64_t maximumPayloadBytes,
                               Consumer consume) {
  podio::ROOTReader reader;
  reader.openFile(path.string());
  const auto entries = reader.getEntries("events");
  if (entries == 0) {
    throw std::runtime_error("入力ROOTのevents categoryが空です");
  }

  RootPassSummary summary;
  for (unsigned entry = 0; entry < entries; ++entry) {
    auto data = reader.readNextEntry("events");
    if (data == nullptr) {
      throw std::runtime_error("入力ROOT eventを読み込めませんでした: entry=" +
                               std::to_string(entry));
    }
    podio::Frame frame(std::move(data));
    auto serialized = eic::telescope::streaming::serializeTimeframe(
        frame, "events", static_cast<std::uint64_t>(entry));
    if (serialized.bytes.empty() ||
        serialized.bytes.size() > maximumPayloadBytes) {
      throw std::runtime_error(
          "serialized eventのpayload sizeが送信上限外です: entry=" +
          std::to_string(entry));
    }
    if (serialized.bytes.size() >
        std::numeric_limits<std::uint64_t>::max() - summary.totalPayloadBytes) {
      throw std::runtime_error(
          "ROOT全eventのpayload合計がuint64範囲を超えます");
    }
    summary.totalPayloadBytes += serialized.bytes.size();
    ++summary.events;
    consume(std::move(serialized));
  }
  if (reader.readNextEntry("events") != nullptr) {
    throw std::runtime_error(
        "ROOT metadataのevent数より多いentryが読み込まれました");
  }
  return summary;
}

std::uint64_t makeStreamId() {
  const auto now = std::chrono::system_clock::now().time_since_epoch();
  const auto ticks =
      std::chrono::duration_cast<std::chrono::nanoseconds>(now).count();
  std::random_device entropy;
  const auto randomBits =
      ((static_cast<std::uint64_t>(entropy()) & 0xffffffffULL) << 32U) |
      (static_cast<std::uint64_t>(entropy()) & 0xffffffffULL);
  const auto streamId = randomBits ^ static_cast<std::uint64_t>(ticks);
  return streamId == 0 ? 1 : streamId;
}

} // namespace

int main(int argc, char **argv) {
  try {
    const auto command = parseCommandLine(argc, argv);
    std::vector<LoadedMessage> messages;
    RootPassSummary rootPreflight;
    FileIdentity rootIdentity;
    std::chrono::microseconds preflightElapsed{};
    std::uint64_t totalBytes = 0;
    if (command.mode == CommandLine::Mode::rootStream) {
      const auto preflightBegin = std::chrono::steady_clock::now();
      rootIdentity = identifyFile(command.inputPaths.front());
      rootPreflight = readRootEvents(
          command.inputPaths.front(), command.options.maxPayloadBytes,
          [](eic::telescope::streaming::SerializedTimeframe) {});
      if (identifyFile(command.inputPaths.front()) != rootIdentity) {
        throw std::runtime_error("preflight中に入力ROOTが変更されました");
      }
      preflightElapsed = std::chrono::duration_cast<std::chrono::microseconds>(
          std::chrono::steady_clock::now() - preflightBegin);
    } else {
      messages.reserve(command.inputPaths.size());
      for (const auto &path : command.inputPaths) {
        auto bytes = readMessage(path, command.options.maxPayloadBytes);
        (void)eic::telescope::streaming::inspectTimeframe(bytes);
        if (bytes.size() >
            std::numeric_limits<std::uint64_t>::max() - totalBytes) {
          throw std::runtime_error(
              "入力FlatBuffers合計サイズがuint64範囲を超えます");
        }
        totalBytes += bytes.size();
        messages.push_back({std::move(bytes)});
      }
    }
    const auto streamId = makeStreamId();

    eic::telescope::streaming::transport::TimeframeSender sender(
        argv[1], command.options);
    const auto begin = std::chrono::steady_clock::now();
    std::uint64_t eventCount = messages.size();
    if (command.mode == CommandLine::Mode::rootStream) {
      if (identifyFile(command.inputPaths.front()) != rootIdentity) {
        throw std::runtime_error("送信開始前に入力ROOTが変更されました");
      }
      sender.beginStream(streamId);
      const auto streamed = readRootEvents(
          command.inputPaths.front(), command.options.maxPayloadBytes,
          [&sender](eic::telescope::streaming::SerializedTimeframe message) {
            sender.sendTimeframe(message.bytes);
          });
      if (streamed.events != rootPreflight.events ||
          streamed.totalPayloadBytes != rootPreflight.totalPayloadBytes ||
          identifyFile(command.inputPaths.front()) != rootIdentity) {
        throw std::runtime_error(
            "preflight後に入力ROOTのevent数またはpayloadが変化しました");
      }
      sender.endStream();
      eventCount = streamed.events;
      totalBytes = streamed.totalPayloadBytes;
    } else if (command.mode == CommandLine::Mode::flatBufferStream) {
      sender.beginStream(streamId);
      for (const auto &message : messages) {
        sender.sendTimeframe(message.bytes);
      }
      sender.endStream();
    } else {
      sender.sendSingleTimeframe(messages.front().bytes, streamId);
    }
    const auto elapsed = std::chrono::duration_cast<std::chrono::microseconds>(
        std::chrono::steady_clock::now() - begin);

    const auto protocol =
        command.mode == CommandLine::Mode::legacyFlatBuffer
            ? "legacy-v1"
            : (command.mode == CommandLine::Mode::rootStream ? "ack-v3" : "v2");
    std::cout << "ZeroMQ送信に成功しました: events=" << eventCount
              << ", total_bytes=" << totalBytes << ", stream_id=" << streamId
              << ", protocol=" << protocol
              << ", queue_time_us=" << elapsed.count();
    if (command.mode == CommandLine::Mode::rootStream) {
      std::cout << ", preflight_time_us=" << preflightElapsed.count()
                << ", ack_retries=" << sender.acknowledgementRetryCount();
    }
    std::cout << '\n';
    if (command.mode == CommandLine::Mode::rootStream) {
      std::cout << "確認: 全DATAとEOSのtransport受領ACKを検証しました; "
                   "下流JANA処理完了のACKではありません\n";
    } else {
      std::cout << "注意: PUSH送信成功はreceiver処理完了ACKではありません\n";
    }
    return 0;
  } catch (const std::exception &error) {
    std::cerr << "ZeroMQ送信に失敗しました: " << error.what() << '\n'
              << "使用法:\n"
              << "  TelescopeZmqSender CONNECT_ENDPOINT INPUT_FB "
                 "[TIMEOUT_MS]\n"
              << "  TelescopeZmqSender CONNECT_ENDPOINT --stream "
                 "[--timeout-ms TIMEOUT_MS] INPUT_FB [INPUT_FB ...]\n"
              << "  TelescopeZmqSender CONNECT_ENDPOINT --root INPUT_ROOT "
                 "[TIMEOUT_MS]\n";
    return (argc < 3) ? 64 : 1;
  }
}
