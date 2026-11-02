// DD4hep XML initialization hook for the temporary barrel-compatible telescope representation.
// This supplies metadata only; it constructs no ACTS objects or detector volumes.
#include <DD4hep/Detector.h>
#include <DD4hep/DetType.h>
#include <DD4hep/Factories.h>
#include <DDRec/DetectorData.h>
#include <TGeoTube.h>
#include <functional>
#include <stdexcept>

static long initializeBarrelMetadata(dd4hep::Detector& detector, int argc, char** argv) {
  if(argc!=2)throw std::runtime_error("TestbenchBarrelMetadata expects detector name and sensor axes");
  auto element=detector.detector(argv[0]);
  if(element.children().empty())throw std::runtime_error("Barrel detector has no physical layers");
  for(const auto& [name,layer]:element.children()) {
    if(!dynamic_cast<TGeoTubeSeg*>(layer.volume().solid().ptr()))
      throw std::runtime_error("Barrel metadata requires actual Tube layer envelopes");
    if(!layer.extension<dd4hep::rec::VariantParameters>(false))
      layer.addExtension<dd4hep::rec::VariantParameters>(new dd4hep::rec::VariantParameters());
  }
  unsigned sensors=0;
  std::function<void(dd4hep::DetElement)> visit=[&](dd4hep::DetElement node) {
    if(node.volume().isSensitive()) {
      auto* params=node.extension<dd4hep::rec::VariantParameters>(false);
      if(!params) {params=new dd4hep::rec::VariantParameters();node.addExtension<dd4hep::rec::VariantParameters>(params);}
      params->set<std::string>("axis_definitions",argv[1]);++sensors;
    }
    for(const auto& [name,child]:node.children())visit(child);
  };
  visit(element);
  if(!sensors)throw std::runtime_error("Barrel detector has no real sensitive descendants");
  element.setTypeFlag(element.typeFlag()|dd4hep::DetType::TRACKER|dd4hep::DetType::BARREL);
  return 1;
}
DECLARE_APPLY(TestbenchBarrelMetadata,initializeBarrelMetadata)
