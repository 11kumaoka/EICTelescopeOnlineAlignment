#include "TelescopeZmqEventSource.h"

#include "TelescopeTimeframeSerialization.h"
#include "TelescopeZmqTransport.h"

#include <JANA/JEvent.h>
#include <JANA/JException.h>
#include <edm4eic/RawTrackerHitCollection.h>
#include <edm4hep/EventHeaderCollection.h>
#include <podio/CollectionBase.h>
#include <podio/Frame.h>

#include <chrono>
#include <condition_variable>
#include <cstddef>
#include <deque>
#include <exception>
#include <memory>
#include <mutex>
#include <optional>
#include <stdexcept>
#include <string>
#include <string_view>
#include <thread>
#include <unordered_map>
#include <utility>
#include <vector>

namespace {

constexpr std::string_view kResourcePrefix = "telescope-zmq:";
constexpr std::string_view kAcknowledgedResourcePrefix = "telescope-zmq-ack:";

struct ParsedResource {
  std::string endpoint;
  eic::telescope::streaming::transport::DeliveryMode deliveryMode;
};

ParsedResource parseResource(std::string_view resourceName) {
  std::string_view endpoint;
  auto mode = eic::telescope::streaming::transport::DeliveryMode::pushPull;
  if (resourceName.starts_with(kAcknowledgedResourcePrefix)) {
    endpoint = resourceName.substr(kAcknowledgedResourcePrefix.size());
    mode = eic::telescope::streaming::transport::DeliveryMode::
        acknowledgedRequestReply;
  } else if (resourceName.starts_with(kResourcePrefix)) {
    endpoint = resourceName.substr(kResourcePrefix.size());
  } else {
    throw JException("TelescopeZmqEventSourceのresourceはtelescope-zmq:または"
                     "telescope-zmq-ack:で始めてください");
  }
  if (endpoint.empty()) {
    throw JException("TelescopeZmqEventSourceのZeroMQ endpointが空です");
  }
  return {std::string(endpoint), mode};
}

void insertFrameIntoEvent(
    JEvent &event, std::unique_ptr<podio::Frame> frame,
    std::unordered_map<std::string, std::string> &collectionTypes) {
  const edm4hep::EventHeaderCollection *eventHeaders = nullptr;
  std::size_t rawHitCollections = 0;
  std::vector<std::pair<std::string, std::string>> newCollectionTypes;

  for (const auto &name : frame->getAvailableCollections()) {
    const auto *collection = frame->get(name);
    if (collection == nullptr) {
      throw JException("PODIO collectionが見つかりません: " + name);
    }
    const std::string typeName(collection->getTypeName());
    const auto knownType = collectionTypes.find(name);
    if (knownType != collectionTypes.end() && knownType->second != typeName) {
      throw JException(
          "stream内でPODIO collection型が変化しました: name=" + name +
          ", expected=" + knownType->second + ", actual=" + typeName);
    }
    if (knownType == collectionTypes.end()) {
      newCollectionTypes.emplace_back(name, typeName);
    }
    if (const auto *headers =
            dynamic_cast<const edm4hep::EventHeaderCollection *>(collection)) {
      if (eventHeaders != nullptr || headers->size() != 1) {
        throw JException("ZeroMQ timeframeには1件のEventHeaderが必要です");
      }
      eventHeaders = headers;
      continue;
    }
    if (dynamic_cast<const edm4eic::RawTrackerHitCollection *>(collection) !=
        nullptr) {
      ++rawHitCollections;
      continue;
    }
    throw JException("JEventSourceで未対応のPODIO collection型です: " +
                     std::string(collection->getTypeName()));
  }

  if (eventHeaders == nullptr || rawHitCollections == 0) {
    throw JException(
        "ZeroMQ timeframeにはEventHeaderとRawTrackerHit collectionが必要です");
  }
  for (auto &[name, typeName] : newCollectionTypes) {
    collectionTypes.emplace(std::move(name), std::move(typeName));
  }
  event.SetEventNumber((*eventHeaders)[0].getEventNumber());
  event.SetRunNumber((*eventHeaders)[0].getRunNumber());

  // First transfer the fully validated Frame itself. If a later collection
  // registration throws, JEvent still owns the storage behind every pointer
  // registered so far. If this Insert throws, the unique_ptr retains ownership.
  auto *ownedFrame = frame.get();
  event.Insert(ownedFrame);
  frame.release();

  for (const auto &name : ownedFrame->getAvailableCollections()) {
    const auto *collection = ownedFrame->get(name);
    if (dynamic_cast<const edm4hep::EventHeaderCollection *>(collection) !=
        nullptr) {
      event.InsertCollectionAlreadyInFrame<edm4hep::EventHeader>(collection,
                                                                 name);
    } else {
      event.InsertCollectionAlreadyInFrame<edm4eic::RawTrackerHit>(collection,
                                                                   name);
    }
  }
}

} // namespace

class TelescopeZmqEventSource::ReceiverWorker {
public:
  enum class ItemKind { data, endOfStream, error };

  struct QueuedItem {
    ItemKind kind{ItemKind::data};
    eic::telescope::streaming::transport::ReceivedStreamMessage message{};
    std::exception_ptr error{};
  };

  ReceiverWorker(std::string endpoint,
                 eic::telescope::streaming::transport::TransportOptions options)
      : m_endpoint(std::move(endpoint)), m_options(std::move(options)),
        m_capacity(m_options.highWaterMark > 0
                       ? static_cast<std::size_t>(m_options.highWaterMark)
                       : 1U) {
    m_thread = std::thread(&ReceiverWorker::run, this);

    std::unique_lock lock(m_mutex);
    m_startedCondition.wait(lock, [this] { return m_started; });
    if (m_startupError != nullptr) {
      const auto error = m_startupError;
      lock.unlock();
      m_thread.join();
      std::rethrow_exception(error);
    }
  }

  ~ReceiverWorker() { stop(); }

  ReceiverWorker(const ReceiverWorker &) = delete;
  ReceiverWorker &operator=(const ReceiverWorker &) = delete;

  std::optional<QueuedItem> waitForItem(std::chrono::milliseconds timeout) {
    std::unique_lock lock(m_mutex);
    if (!m_dataCondition.wait_for(lock, timeout, [this] {
          return !m_queue.empty() || m_workerStopped;
        })) {
      return std::nullopt;
    }
    if (m_queue.empty()) {
      return std::nullopt;
    }
    auto item = std::move(m_queue.front());
    m_queue.pop_front();
    lock.unlock();
    m_spaceCondition.notify_one();
    return item;
  }

  const std::string &boundEndpoint() const { return m_boundEndpoint; }

private:
  bool stopRequested() {
    std::lock_guard lock(m_mutex);
    return m_stopRequested;
  }

  bool enqueue(QueuedItem item) {
    std::unique_lock lock(m_mutex);
    m_spaceCondition.wait(lock, [this] {
      return m_stopRequested || m_queue.size() < m_capacity;
    });
    if (m_stopRequested) {
      return false;
    }
    m_queue.push_back(std::move(item));
    lock.unlock();
    m_dataCondition.notify_one();
    return true;
  }

  void markStopped() {
    {
      std::lock_guard lock(m_mutex);
      m_workerStopped = true;
    }
    m_dataCondition.notify_all();
    m_spaceCondition.notify_all();
  }

  void run() noexcept {
    std::unique_ptr<eic::telescope::streaming::transport::TimeframeReceiver>
        receiver;
    try {
      receiver = std::make_unique<
          eic::telescope::streaming::transport::TimeframeReceiver>(m_endpoint,
                                                                   m_options);
      {
        std::lock_guard lock(m_mutex);
        m_boundEndpoint = receiver->boundEndpoint();
        m_started = true;
      }
      m_startedCondition.notify_one();
    } catch (...) {
      {
        std::lock_guard lock(m_mutex);
        m_startupError = std::current_exception();
        m_started = true;
        m_workerStopped = true;
      }
      m_startedCondition.notify_one();
      m_dataCondition.notify_all();
      return;
    }

    try {
      while (!stopRequested()) {
        eic::telescope::streaming::transport::ReceivedStreamMessage message;
        try {
          message = receiver->receiveNext();
        } catch (
            const eic::telescope::streaming::transport::TransportTimeout &) {
          continue;
        }

        const bool isEnd = message.kind ==
                           eic::telescope::streaming::transport::
                               StreamMessageKind::endOfStream;
        QueuedItem item;
        item.kind = isEnd ? ItemKind::endOfStream : ItemKind::data;
        item.message = std::move(message);
        if (!enqueue(std::move(item)) || isEnd) {
          break;
        }
      }
    } catch (...) {
      if (!stopRequested()) {
        QueuedItem item;
        item.kind = ItemKind::error;
        item.error = std::current_exception();
        (void)enqueue(std::move(item));
      }
    }

    // The receiver and its ZeroMQ socket are both destroyed in this worker
    // thread, the same thread that created and used them.
    receiver.reset();
    markStopped();
  }

  void stop() {
    {
      std::lock_guard lock(m_mutex);
      m_stopRequested = true;
    }
    m_dataCondition.notify_all();
    m_spaceCondition.notify_all();
    if (m_thread.joinable()) {
      m_thread.join();
    }
  }

  std::string m_endpoint;
  eic::telescope::streaming::transport::TransportOptions m_options;
  std::size_t m_capacity;
  std::mutex m_mutex;
  std::condition_variable m_startedCondition;
  std::condition_variable m_dataCondition;
  std::condition_variable m_spaceCondition;
  std::deque<QueuedItem> m_queue;
  std::thread m_thread;
  std::string m_boundEndpoint;
  std::exception_ptr m_startupError;
  bool m_started{};
  bool m_stopRequested{};
  bool m_workerStopped{};
};

TelescopeZmqEventSource::TelescopeZmqEventSource(std::string resourceName,
                                                 JApplication *application)
    : JEventSource(std::move(resourceName), application) {
  SetTypeName("TelescopeZmqEventSource");
  SetPrefix("telescope_zmq_source");
  SetCallbackStyle(CallbackStyle::ExpertMode);
}

TelescopeZmqEventSource::~TelescopeZmqEventSource() = default;

void TelescopeZmqEventSource::Open() {
  const auto resource = parseResource(GetResourceName());
  m_endpoint = resource.endpoint;
  eic::telescope::streaming::transport::TransportOptions options;
  options.timeout = std::chrono::milliseconds(*m_timeoutMs);
  options.maxPayloadBytes = *m_maxPayloadBytes;
  options.highWaterMark = *m_highWaterMark;
  options.deliveryMode = resource.deliveryMode;
  try {
    m_receiver = std::make_unique<ReceiverWorker>(m_endpoint, options);
  } catch (const std::exception &error) {
    throw JException("ZeroMQ受信threadの開始に失敗しました: " +
                     std::string(error.what()));
  }
  m_collectionTypes.clear();
  m_finished = false;
  LOG_INFO(GetLogger()) << "ZeroMQ JEventSourceを開始しました: "
                        << m_receiver->boundEndpoint() << ", protocol="
                        << (resource.deliveryMode ==
                                    eic::telescope::streaming::transport::
                                        DeliveryMode::acknowledgedRequestReply
                                ? "ack-v3"
                                : "legacy-v1/v2")
                        << LOG_END;
}

void TelescopeZmqEventSource::Close() {
  m_receiver.reset();
  LOG_INFO(GetLogger()) << "ZeroMQ JEventSourceを終了しました" << LOG_END;
}

JEventSource::Result TelescopeZmqEventSource::Emit(JEvent &event) {
  if (m_finished) {
    return Result::FailureFinished;
  }
  if (m_receiver == nullptr) {
    throw JException("ZeroMQ JEventSourceがOpenされていません");
  }

  auto item = m_receiver->waitForItem(std::chrono::milliseconds(*m_timeoutMs));
  if (!item.has_value()) {
    return Result::FailureTryAgain;
  }

  if (item->kind == ReceiverWorker::ItemKind::error) {
    try {
      std::rethrow_exception(item->error);
    } catch (const std::exception &error) {
      throw JException("ZeroMQ streamの受信に失敗しました: " +
                       std::string(error.what()));
    } catch (...) {
      throw JException("ZeroMQ streamの受信に失敗しました: 不明な例外");
    }
  }
  if (item->kind == ReceiverWorker::ItemKind::endOfStream) {
    m_finished = true;
    LOG_INFO(GetLogger()) << "ZeroMQ streamのEOSを受信しました: stream_id="
                          << item->message.streamId
                          << ", sequence=" << item->message.sequence << LOG_END;
    return Result::FailureFinished;
  }

  auto received = std::move(item->message);

  try {
    if (received.encoding != eic::telescope::streaming::transport::
                                 PayloadEncoding::telescopeFlatBuffer) {
      throw std::runtime_error("DATA payload encodingがETFBではありません");
    }
    auto decoded =
        eic::telescope::streaming::deserializeTimeframe(received.payload);
    if (decoded.metadata.category != "events") {
      throw std::runtime_error("PODIO categoryがeventsではありません");
    }
    const auto rawHitObjects = decoded.metadata.rawTrackerHitObjects;
    auto frame = std::make_unique<podio::Frame>(std::move(decoded.frame));
    insertFrameIntoEvent(event, std::move(frame), m_collectionTypes);
    LOG_DEBUG(GetLogger())
        << "ZeroMQ timeframeをJEventへ変換しました: stream_id="
        << received.streamId << ", sequence=" << received.sequence
        << ", event=" << event.GetEventNumber()
        << ", run=" << event.GetRunNumber()
        << ", RawTrackerHits=" << rawHitObjects << LOG_END;
    return Result::Success;
  } catch (const std::exception &error) {
    throw JException("ZeroMQ timeframeのJEvent変換に失敗しました: " +
                     std::string(error.what()));
  }
}

std::string TelescopeZmqEventSource::GetDescription() {
  return "Telescope FlatBuffers over ZeroMQ v1/v2 or acknowledged v3";
}

template <>
double JEventSourceGeneratorT<TelescopeZmqEventSource>::CheckOpenable(
    std::string resourceName) {
  const std::string_view resource(resourceName);
  return resource.starts_with(kResourcePrefix) ||
                 resource.starts_with(kAcknowledgedResourcePrefix)
             ? 1.0
             : 0.0;
}
