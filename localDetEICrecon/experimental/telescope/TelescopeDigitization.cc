// Register installed EICrecon digitizers without running reconstruction.
// Detector response settings retain the historical seven-layer configuration.
#include <Evaluator/DD4hepUnits.h>
#include <JANA/JApplication.h>
#include <edm4eic/EDM4eicVersion.h>
#include <extensions/jana/JOmniFactoryGeneratorT.h>
#include <factories/digi/SiliconTrackerDigi_factory.h>

extern "C" void InitPlugin(JApplication* app) {
  InitJANAPlugin(app);
  using namespace eicrecon;
  app->Add(new JOmniFactoryGeneratorT<SiliconTrackerDigi_factory>(
      "TelescopeSiMAPSRawHits", {"EventHeader", "SiMAPSHits"},
      {"TelescopeSiMAPSRawHits",
#if EDM4EIC_BUILD_VERSION >= EDM4EIC_VERSION(8, 7, 0)
       "TelescopeSiMAPSRawHitLinks",
#endif
       "TelescopeSiMAPSRawHitAssociations"},
      {.threshold = 0.54 * dd4hep::keV, .timeResolution = 8.}, app));
  // Preserve the legacy pixel-like MPGD and AC-LGAD approximations for diagnostics.
  app->Add(new JOmniFactoryGeneratorT<SiliconTrackerDigi_factory>(
      "TelescopeMPGDRawHits", {"EventHeader", "MPGDHits"},
      {"TelescopeMPGDRawHits",
#if EDM4EIC_BUILD_VERSION >= EDM4EIC_VERSION(8, 7, 0)
       "TelescopeMPGDRawHitLinks",
#endif
       "TelescopeMPGDRawHitAssociations"},
      {.threshold = 100. * dd4hep::eV, .timeResolution = 10.}, app));
  app->Add(new JOmniFactoryGeneratorT<SiliconTrackerDigi_factory>(
      "TelescopeACLGADRawHits", {"EventHeader", "ACLGADHits"},
      {"TelescopeACLGADRawHits",
#if EDM4EIC_BUILD_VERSION >= EDM4EIC_VERSION(8, 7, 0)
       "TelescopeACLGADRawHitLinks",
#endif
       "TelescopeACLGADRawHitAssociations"},
      {.threshold = 0., .timeResolution = 0.025}, app));
}
