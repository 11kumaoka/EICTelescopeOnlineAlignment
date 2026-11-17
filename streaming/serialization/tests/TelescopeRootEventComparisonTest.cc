#include "TelescopeRootEventComparison.h"

#include <edm4eic/RawTrackerHitCollection.h>
#include <edm4hep/EventHeaderCollection.h>
#include <podio/Frame.h>
#include <podio/ROOTWriter.h>

#include <bit>
#include <chrono>
#include <cstdint>
#include <exception>
#include <filesystem>
#include <iostream>
#include <stdexcept>
#include <string>
#include <utility>

namespace {

podio::Frame fixture(std::uint64_t entry, bool changeHit) {
  podio::Frame frame;
  edm4hep::EventHeaderCollection headers;
  auto header = headers.create();
  header.setEventNumber(1000 + entry);
  header.setRunNumber(17);
  header.setTimeStamp(0xfedcba9876543200ULL + entry);
  header.setWeight(std::bit_cast<double>(0x8000000000000000ULL));
  header.addToWeights(0.25 + static_cast<double>(entry));
  frame.put(std::move(headers), "EventHeader");

  edm4eic::RawTrackerHitCollection hits;
  auto first = hits.create();
  first.setCellID(0x123456789abcdef0ULL + entry);
  first.setCharge(changeHit ? -38 : -37);
  first.setTimeStamp(81);
  auto second = hits.create();
  second.setCellID(42 + entry);
  second.setCharge(105);
  second.setTimeStamp(-901);
  frame.put(std::move(hits), "TelescopeChannels");
  return frame;
}

void writeFixture(const std::filesystem::path &path, bool changeLastHit,
                  unsigned entries = 3) {
  podio::ROOTWriter writer(path.string());
  for (unsigned entry = 0; entry < entries; ++entry) {
    auto frame = fixture(entry, changeLastHit && entry + 1 == entries);
    writer.writeFrame(frame, "events");
  }
  writer.finish();
}

class TemporaryFiles {
public:
  explicit TemporaryFiles(std::filesystem::path directory)
      : expected(directory / "expected.root"),
        equivalent(directory / "equivalent.root"),
        changed(directory / "changed.root"),
        shorter(directory / "shorter.root") {
    if (std::filesystem::exists(directory)) {
      throw std::runtime_error("一時テストdirectoryが既に存在します");
    }
    std::filesystem::create_directory(directory);
    m_directory = std::move(directory);
  }

  ~TemporaryFiles() {
    std::error_code error;
    std::filesystem::remove(expected, error);
    std::filesystem::remove(equivalent, error);
    std::filesystem::remove(changed, error);
    std::filesystem::remove(shorter, error);
    std::filesystem::remove(m_directory, error);
  }

  std::filesystem::path expected;
  std::filesystem::path equivalent;
  std::filesystem::path changed;
  std::filesystem::path shorter;

private:
  std::filesystem::path m_directory;
};

std::filesystem::path uniqueTemporaryDirectory() {
  const auto ticks =
      std::chrono::steady_clock::now().time_since_epoch().count();
  return std::filesystem::temp_directory_path() /
         ("telescope-root-compare-" + std::to_string(ticks));
}

} // namespace

int main() {
  try {
    TemporaryFiles files(uniqueTemporaryDirectory());
    writeFixture(files.expected, false);
    writeFixture(files.equivalent, false);
    writeFixture(files.changed, true);
    writeFixture(files.shorter, false, 2);

    const auto summary =
        eic::telescope::streaming::requireEquivalentRootEventFiles(
            files.expected.string(), files.equivalent.string());
    if (summary.entries != 3) {
      throw std::runtime_error("比較したevent数が一致しません");
    }

    bool refusedChangedHit = false;
    try {
      (void)eic::telescope::streaming::requireEquivalentRootEventFiles(
          files.expected.string(), files.changed.string());
    } catch (const std::runtime_error &) {
      refusedChangedHit = true;
    }
    if (!refusedChangedHit) {
      throw std::runtime_error("変更されたRawTrackerHitを見逃しました");
    }

    bool refusedShorterFile = false;
    try {
      (void)eic::telescope::streaming::requireEquivalentRootEventFiles(
          files.expected.string(), files.shorter.string());
    } catch (const std::runtime_error &) {
      refusedShorterFile = true;
    }
    if (!refusedShorterFile) {
      throw std::runtime_error("不足したeventを見逃しました");
    }

    std::cout << "PODIO ROOT全event比較テストに成功しました\n";
    return 0;
  } catch (const std::exception &error) {
    std::cerr << "PODIO ROOT全event比較テストに失敗しました: " << error.what()
              << '\n';
    return 1;
  }
}
