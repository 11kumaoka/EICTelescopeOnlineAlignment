#include "MilleCollector.h"
#include "TelescopeZmqTransport.h"

#include <nlohmann/json.hpp>

#include <algorithm>
#include <cerrno>
#include <chrono>
#include <cstdlib>
#include <filesystem>
#include <fstream>
#include <iostream>
#include <limits>
#include <map>
#include <stdexcept>
#include <string>
#include <unistd.h>

namespace {

std::map<std::string, std::string> arguments(int argc, char **argv) {
  std::map<std::string, std::string> result;
  for (int index = 1; index < argc; index += 2) {
    const std::string key = argv[index];
    if (!key.starts_with("--") || index + 1 >= argc ||
        !result.emplace(key, argv[index + 1]).second) {
      throw std::invalid_argument("invalid or duplicate argument: " + key);
    }
  }
  return result;
}

const std::string &required(const std::map<std::string, std::string> &values,
                            const std::string &key) {
  const auto found = values.find(key);
  if (found == values.end() || found->second.empty()) {
    throw std::invalid_argument("missing argument: " + key);
  }
  return found->second;
}

std::uint64_t unsignedInteger(const std::string &text,
                              const std::string &name) {
  if (text.empty() ||
      !std::all_of(text.begin(), text.end(),
                   [](unsigned char value) { return value >= '0' && value <= '9'; })) {
    throw std::invalid_argument("invalid integer: " + name);
  }
  std::size_t consumed = 0;
  const auto value = std::stoull(text, &consumed, 10);
  if (consumed != text.size()) {
    throw std::invalid_argument("invalid integer: " + name);
  }
  return value;
}

bool sha256(const std::string &value) {
  return value.size() == 64 &&
         std::all_of(value.begin(), value.end(), [](unsigned char character) {
           return (character >= '0' && character <= '9') ||
                  (character >= 'a' && character <= 'f');
         });
}

void writeNewJson(const std::filesystem::path &path,
                  const nlohmann::json &value) {
  if (std::filesystem::exists(path)) {
    throw std::runtime_error("ready path must be new: " + path.string());
  }
  const auto temporary =
      path.string() + ".partial-" + std::to_string(::getpid());
  {
    std::ofstream output(temporary, std::ios::out | std::ios::binary);
    output << value.dump(2) << '\n';
    output.flush();
    if (!output) {
      throw std::runtime_error("ready receipt write failed");
    }
  }
  std::error_code error;
  std::filesystem::create_hard_link(temporary, path, error);
  if (error) {
    throw std::runtime_error("ready receipt publication failed: " +
                             error.message());
  }
  std::filesystem::remove(temporary, error);
  if (error) {
    throw std::runtime_error("ready temporary cleanup failed: " +
                             error.message());
  }
}

} // namespace

int main(int argc, char **argv) try {
  const auto values = arguments(argc, argv);
  const auto &bind = required(values, "--bind");
  if (!bind.starts_with("tcp://127.0.0.1:")) {
    throw std::invalid_argument("Collector bind endpoint must use IPv4 loopback");
  }
  // Normalize CLI paths before the Collector performs directory fsync and
  // publishes their exact identities in its receipts. This also makes the
  // standalone executable safe to use with ordinary relative paths.
  const auto ready =
      std::filesystem::absolute(required(values, "--ready"));
  const auto output =
      std::filesystem::absolute(required(values, "--output"));
  const auto report =
      std::filesystem::absolute(required(values, "--report"));
  const auto records64 = unsignedInteger(required(values, "--records"),
                                         "--records");
  const auto timeout64 = unsignedInteger(required(values, "--timeout-ms"),
                                         "--timeout-ms");
  if (records64 == 0 || records64 > std::numeric_limits<std::size_t>::max() ||
      timeout64 == 0 || timeout64 > std::numeric_limits<int>::max()) {
    throw std::invalid_argument("record count or timeout is out of range");
  }

  eic::telescope::streaming::transport::TransportOptions options;
  options.timeout = std::chrono::milliseconds(timeout64);
  options.deliveryMode = eic::telescope::streaming::transport::
      DeliveryMode::acknowledgedRequestReply;
  options.deferAcknowledgement = true;
  eic::telescope::streaming::transport::TimeframeReceiver receiver(bind,
                                                                   options);
  const auto model = required(values, "--model-id");
  const auto configuration = required(values, "--configuration-id");
  const auto geometry = required(values, "--geometry-id");
  if (!sha256(model) || !sha256(configuration) || !sha256(geometry)) {
    throw std::invalid_argument("scientific identities must be lowercase SHA-256");
  }
  writeNewJson(ready, {
                          {"schema", "telescope-mille-collector-ready-v1"},
                          {"pid", ::getpid()},
                          {"endpoint", receiver.boundEndpoint()},
                          {"expected_records", records64},
                          {"model_id", model},
                          {"configuration_id", configuration},
                          {"geometry_id", geometry},
                          {"output", output.string()},
                      });
  eic::telescope::streaming::mille::CollectorConfig config{
      output, report, static_cast<std::size_t>(records64), model,
      configuration, geometry, 10};
  (void)eic::telescope::streaming::mille::collectMilleRecords(receiver,
                                                              config);
  return 0;
} catch (const std::exception &error) {
  std::cerr << "Mille Collector error: " << error.what() << '\n';
  return 1;
}
