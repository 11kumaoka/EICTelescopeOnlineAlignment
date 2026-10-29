// Deterministic test input only: fresh radial-beam pixel centers, no simulation truth.
#pragma once
#include <algorithms/interfaces/WithPodConfig.h>
#include <extensions/jana/JOmniFactory.h>
#include <services/geometry/dd4hep/DD4hep_service.h>
#include <algorithms/geo.h>
#include <DD4hep/Segmentations.h>
#include <DDRec/CellIDPositionConverter.h>
#include <Evaluator/DD4hepUnits.h>
#include <edm4eic/TrackerHitCollection.h>
#include <edm4eic/MCRecoTrackerHitAssociationCollection.h>
#include <cmath>
namespace eicrecon::telescope {
class SyntheticTrackerHits_factory : public JOmniFactory<SyntheticTrackerHits_factory, NoConfig> {
  PodioOutput<edm4eic::TrackerHit> hits{this};
  // The synthetic event genuinely has no MC/raw-hit associations.
  PodioOutput<edm4eic::MCRecoTrackerHitAssociation> associations{this};
  Service<DD4hep_service> geometry{this};
public:
  void Configure() {}
  void Process(int32_t, uint64_t) {
    const auto& geo=algorithms::GeoSvc::instance();
    const auto converter=geo.cellIDPositionConverter();
    for(int layer=0;layer<5;++layer) {
      const uint64_t base=31ULL|(uint64_t(layer)<<11)|(1ULL<<22)|(1ULL<<28);
      const auto* context=converter->findContext(base);
      if(!context) throw std::runtime_error("Missing synthetic-hit detector context");
      const dd4hep::Position global((250.+50.*layer)*dd4hep::mm,
          (2.+.003*50.*layer)*dd4hep::mm,(-3.-.002*50.*layer)*dd4hep::mm);
      const auto cell=geo.detector()->readout("SiMAPSHits").segmentation().cellID(context->worldToLocal(global),global,base);
      const auto position=converter->position(cell);
      const auto dimensions=converter->cellDimensions(cell);
      if(dimensions.size()!=2) throw std::runtime_error("Expected two X/Z pixel dimensions");
      auto hit=hits()->create(); hit.setCellID(cell);
      hit.setPosition({float(position.x()/dd4hep::mm),float(position.y()/dd4hep::mm),float(position.z()/dd4hep::mm)});
      hit.setPositionError({float(std::pow(dimensions[0]/dd4hep::mm,2)/12.),
                           float(std::pow(dimensions[1]/dd4hep::mm,2)/12.),0.f});
    }
  }
};
}
