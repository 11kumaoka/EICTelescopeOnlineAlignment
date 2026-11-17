#include "MilleRecordOutput.h"

#include <Mille/MilleFactory.h>
#include <Mille/MilleRecord.h>

#include <stdexcept>

namespace eicrecon::telescope {
namespace {

class FileMilleRecordOutput final : public MilleRecordOutput {
public:
  explicit FileMilleRecordOutput(const std::string &path)
      : m_record(Mille::spawnMilleRecord(path, true, false)) {
    if (!m_record) {
      throw std::runtime_error("Cannot create official Mille output: " + path);
    }
  }

  Mille::MilleRecord &beginRecord(MilleRecordMetadata) override {
    if (m_finished || m_active) {
      throw std::runtime_error("Invalid file Mille record lifecycle");
    }
    m_active = true;
    return *m_record;
  }

  void commitRecord(std::uint32_t) override {
    if (!m_active || m_finished) {
      throw std::runtime_error("No active file Mille record to commit");
    }
    m_record->writeRecord();
    m_record->flushOutputFile();
    m_active = false;
  }

  void finish() override {
    if (m_finished) {
      return;
    }
    if (m_active) {
      throw std::runtime_error("Cannot finish with an uncommitted Mille record");
    }
    m_record->flushOutputFile();
    m_record.reset();
    m_finished = true;
  }

private:
  std::unique_ptr<Mille::MilleRecord> m_record;
  bool m_active{};
  bool m_finished{};
};

} // namespace

std::unique_ptr<MilleRecordOutput>
makeFileMilleRecordOutput(const std::string &path) {
  return std::make_unique<FileMilleRecordOutput>(path);
}

} // namespace eicrecon::telescope
