// Experimental factory chain following EICrecon src/global/tracking/tracking.cc.
#include "factories/SyntheticTrackerHits_factory.h"
#include "factories/TelescopeSeed_factory.h"
#include "factories/tracking/TrackerMeasurementFromHits_factory.h"
#include "factories/tracking/CKFTracking_factory.h"
#include <factories/tracking/ActsToTracks_factory.h>
#include <factories/tracking/TrackerHitReconstruction_factory.h>
#include <factories/meta/CollectionCollector_factory.h>
#include <extensions/jana/JOmniFactoryGeneratorT.h>
#include <JANA/JApplication.h>
#include "FactoryAudit.h"
extern "C" void InitPlugin(JApplication* app) {
  InitJANAPlugin(app);
  using namespace eicrecon;
  app->Add(new telescope::FactoryAudit);
  bool channelInput=false;
  app->SetDefaultParameter("TelescopeTracking:channelInput",channelInput,
      "Decode immutable TelescopeChannels with the installed official TrackerHitReconstruction");
  if(channelInput) {
    app->Add(new JOmniFactoryGeneratorT<TrackerHitReconstruction_factory>(
        "TelescopeHitReconstruction", {"TelescopeChannels"}, {"TelescopeHits"}, app));
    app->Add(new JOmniFactoryGeneratorT<CollectionCollector_factory<edm4eic::MCRecoTrackerHitAssociation,true>>(
        "TelescopeHitAssociations", {}, {"TelescopeHitAssociations"}, app));
  } else {
    app->Add(new JOmniFactoryGeneratorT<telescope::SyntheticTrackerHits_factory>(
        "TelescopeSyntheticHits", {}, {"TelescopeHits", "TelescopeHitAssociations"}, app));
  }
  app->Add(new JOmniFactoryGeneratorT<telescope::TrackerMeasurementFromHits_factory>(
      "TelescopeMeasurements", {"TelescopeHits"}, {"TelescopeMeasurements"}, app));
  app->Add(new JOmniFactoryGeneratorT<telescope::TelescopeSeed_factory>(
      "TelescopeSeeds", {"TelescopeHits"}, {"TelescopeSeeds", "TelescopeSeedParameters"}, app));
  app->Add(new JOmniFactoryGeneratorT<telescope::CKFTracking_factory>(
      "TelescopeCKF", {"TelescopeSeeds", "TelescopeMeasurements"},
      {"TelescopeActsStates", "TelescopeActsTracks"}, {.electronHypothesis=true}, app));
  app->Add(new JOmniFactoryGeneratorT<ActsToTracks_factory>(
      "TelescopeTracks", {"TelescopeMeasurements", "TelescopeSeeds", "TelescopeActsStates",
       "TelescopeActsTracks", "TelescopeHitAssociations"},
      {"TelescopeTrajectories", "TelescopeTrackParameters", "TelescopeTracks",
#if EDM4EIC_BUILD_VERSION >= EDM4EIC_VERSION(8, 7, 0)
       "TelescopeTrackLinks",
#endif
       "TelescopeTrackAssociations"}, app));
}
