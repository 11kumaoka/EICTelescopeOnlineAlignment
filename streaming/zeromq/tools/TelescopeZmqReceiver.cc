#include "TelescopeTimeframeSerialization.h"
#include "TelescopeZmqTransport.h"

#include <podio/Frame.h>
#include <podio/ROOTReader.h>
#include <podio/ROOTWriter.h>

#include <array>
#include <charconv>
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
#include <string_view>
#include <system_error>
#include <utility>

namespace {

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

void requireNew(const std::filesystem::path& path) {
  if (std::filesystem::exists(path)) {
    throw std::runtime_error("出力先は新規パスである必要があります: " +
                             path.string());
  }
  const auto parent = path.has_parent_path() ? path.parent_path()
                                              : std::filesystem::path{"."};
  if (!std::filesystem::exists(parent) ||
      !std::filesystem::is_directory(parent)) {
    throw std::runtime_error("出力先の親ディレクトリが存在しません: " +
                             parent.string());
  }
}

std::filesystem::path partialPath(const std::filesystem::path& path,
                                  std::uint64_t streamId) {
  const auto filename = path.stem().string() + ".partial-" +
                        std::to_string(streamId) + path.extension().string();
  return path.parent_path() / filename;
}

template <std::size_t Size>
void requireDistinctPaths(
    const std::array<std::filesystem::path, Size>& paths) {
  for (std::size_t i = 0; i < paths.size(); ++i) {
    const auto left = std::filesystem::absolute(paths[i]).lexically_normal();
    for (std::size_t j = i + 1; j < paths.size(); ++j) {
      const auto right =
          std::filesystem::absolute(paths[j]).lexically_normal();
      if (left == right) {
        throw std::runtime_error(
            "最終出力とpartial出力には異なるパスが必要です");
      }
    }
  }
}

void publishWithoutOverwrite(const std::filesystem::path& partial,
                             const std::filesystem::path& final) {
  requireNew(final);
  std::error_code error;
  std::filesystem::create_hard_link(partial, final, error);
  if (error) {
    throw std::runtime_error("既存出力を置換せず公開できませんでした: " +
                             final.string() + ": " + error.message());
  }
  if (!std::filesystem::remove(partial, error) || error) {
    throw std::runtime_error("公開後のpartial fileを除去できませんでした: " +
                             partial.string() + ": " + error.message());
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
                       const std::string& category) {
  podio::ROOTReader reader;
  reader.openFile(path.string());
  if (reader.getEntries(category) != 1) {
    throw std::runtime_error("復元ROOTには1 eventだけが必要です");
  }
  auto data = reader.readEntry(category, 0);
  if (!data) {
    throw std::runtime_error("復元ROOTイベントを再読込みできませんでした");
  }
  return podio::Frame(std::move(data));
}

} // namespace

int main(int argc, char** argv) {
  if (argc != 4 && argc != 5) {
    std::cerr << "使用法: TelescopeZmqReceiver BIND_ENDPOINT "
                 "NEW_RECEIVED_FB NEW_OUTPUT_ROOT [TIMEOUT_MS]\n";
    return 64;
  }

  try {
    const std::filesystem::path messagePath = argv[2];
    const std::filesystem::path outputRootPath = argv[3];
    requireDistinctPaths(std::array{messagePath, outputRootPath});
    requireNew(messagePath);
    requireNew(outputRootPath);
    eic::telescope::streaming::transport::TransportOptions options;
    if (argc == 5) {
      options.timeout = parseTimeout(argv[4]);
    }
    const auto begin = std::chrono::steady_clock::now();
    eic::telescope::streaming::transport::ReceivedTimeframe received;
    {
      eic::telescope::streaming::transport::TimeframeReceiver receiver(
          argv[1], options);
      std::cout << "ZeroMQ receiverを開始しました: "
                << receiver.boundEndpoint() << std::endl;
      received = receiver.receiveSingleTimeframe();
    }
    auto decoded =
        eic::telescope::streaming::deserializeTimeframe(received.payload);

    const auto partialMessagePath =
        partialPath(messagePath, received.streamId);
    const auto partialRootPath =
        partialPath(outputRootPath, received.streamId);
    requireDistinctPaths(std::array{messagePath, outputRootPath,
                                    partialMessagePath, partialRootPath});
    requireNew(partialMessagePath);
    requireNew(partialRootPath);
    writeBytes(partialMessagePath, received.payload);
    {
      podio::ROOTWriter writer(partialRootPath.string());
      writer.writeFrame(decoded.frame, decoded.metadata.category);
      writer.finish();
    }
    auto rootReadback =
        readFrame(partialRootPath, decoded.metadata.category);
    eic::telescope::streaming::requireEquivalentFrames(decoded.frame,
                                                        rootReadback);
    publishWithoutOverwrite(partialMessagePath, messagePath);
    publishWithoutOverwrite(partialRootPath, outputRootPath);
    const auto elapsed = std::chrono::duration_cast<std::chrono::microseconds>(
        std::chrono::steady_clock::now() - begin);

    std::cout << "ZeroMQ受信・復元検証に成功しました: "
              << received.payload.size() << " bytes, stream_id="
              << received.streamId
              << ", RawTrackerHits="
              << decoded.metadata.rawTrackerHitObjects
              << ", total_time_us=" << elapsed.count() << '\n';
    return 0;
  } catch (const std::exception& error) {
    std::cerr << "ZeroMQ受信・復元検証に失敗しました: " << error.what()
              << '\n';
    return 1;
  }
}
