// Factory boundary follows pinned EICrecon TrackSeeding_factory.h.
#pragma once
#include "../algorithms/TelescopeSeed.h"
#include <extensions/jana/JOmniFactory.h>
namespace eicrecon::telescope {
class TelescopeSeed_factory : public JOmniFactory<TelescopeSeed_factory, NoConfig> {
  PodioInput<edm4eic::TrackerHit> hits{this};
  PodioOutput<edm4eic::TrackSeed> seeds{this};
  PodioOutput<edm4eic::TrackParameters> parameters{this};
  std::unique_ptr<TelescopeSeed> algorithm;
public:
  void Configure() { algorithm=std::make_unique<TelescopeSeed>(GetPrefix()); algorithm->init(); }
  void Process(int32_t, uint64_t) { algorithm->process({hits()}, {seeds().get(), parameters().get()}); }
};
}
