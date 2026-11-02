// Real official RawHit reconstruction and binary32 serialization checks; no fit.
#include "NavigationAudit.h"
#include "algorithms/tracking/TrackerMeasurementFromHits.h"
#include <algorithms/tracking/TrackerHitReconstruction.h>
#include <algorithms/interfaces/ActsSvc.h>
#include <DD4hep/Detector.h>
#include <spdlog/sinks/stdout_color_sinks.h>
#include <filesystem>
#include <fstream>
#include <iostream>
#include <limits>

int main(int argc,char** argv) {
  if(argc!=3 || std::filesystem::exists(argv[2])) {
    std::cerr<<"Usage: TelescopeSerializationAudit XML NEW_JSON\n";return 64;
  }
  try {
    auto& detector=dd4hep::Detector::getInstance();detector.fromXML(argv[1]);
    detector.apply("DD4hepVolumeManager",0,nullptr);algorithms::GeoSvc::instance().init(&detector);
    auto provider=std::make_shared<ActsGeometryProvider>();
    auto logger=spdlog::stdout_color_mt("serialization_audit_test");
    provider->initialize(&detector,"",logger,logger);algorithms::ActsSvc::instance().init(provider);
    const auto converter=algorithms::GeoSvc::instance().cellIDPositionConverter();
    const auto segmentation=detector.readout("SiMAPSHits").segmentation();
    edm4eic::RawTrackerHitCollection raw;
    // Large opposite-sign coordinates exercise binary32 spacing near 40 mm.
    for(int layer=0;layer<5;++layer) for(int sign:{-1,1}) {
      const std::uint64_t base=31ULL|(std::uint64_t(layer)<<11)|(1ULL<<22)|(1ULL<<28);
      const auto* volume=converter->findContext(base);
      if(!volume)throw std::runtime_error("Missing physical sensor in serialization test");
      const dd4hep::Position local(sign*40.*dd4hep::mm,0.,-sign*39.5*dd4hep::mm);
      const auto world=volume->localToWorld(local);
      auto hit=raw.create();hit.setCellID(segmentation.cellID(local,world,base));
      hit.setCharge(700001);hit.setTimeStamp(1234);
    }
    edm4eic::TrackerHitCollection hits;
    eicrecon::TrackerHitReconstruction reconstruction("ActualOfficialSerializationFixture");reconstruction.init();
    reconstruction.process({&raw},{&hits});
    edm4eic::Measurement2DCollection measurements;
    eicrecon::telescope::TrackerMeasurementFromHits conversion("ActualMeasurementSerializationFixture");conversion.init();
    conversion.process({&hits},{&measurements});
    if(hits.size()!=10 || measurements.size()!=10)throw std::runtime_error("Official fixture conversion lost channels");
    nlohmann::json report={{"passed",false},{"positive",nlohmann::json::array()},
      {"refusals",nlohmann::json::object()},{"fit_executed",false},{"truth_used",false},
      {"input","actual installed TrackerHitReconstruction and maintained measurement conversion; 10 large-coordinate channels"}};
    for(std::size_t i=0;i<hits.size();++i) {
      auto proof=eicrecon::telescope::auditMeasurementSerialization(hits[i],measurements[i],*provider);
      proof["cell_id"]=hits[i].getCellID();proof["layer"]=(hits[i].getCellID()>>11)&63;
      report["positive"].push_back(proof);
    }
    // The third hit belongs to L1, including its actual candidate rotation.
    auto hit=hits[2];auto measurement=measurements[2];
    const auto* volume=converter->findContext(hit.getCellID());
    const auto* surface=provider->surfaceMap().at(volume->identifier);
    const auto rotation=surface->localToGlobalTransform(provider->getActsGeometryContext()).linear().eval();
    for(int i=0;i<3;++i)report["actual_L1_surface_rotation"].push_back({rotation(i,0),rotation(i,1),rotation(i,2)});
    auto refuse=[&](const char* name,const char* expected) {
      try {eicrecon::telescope::auditMeasurementSerialization(hit,measurement,*provider);}
      catch(const std::runtime_error& error) {
        if(std::string(error.what()).find(expected)==std::string::npos)throw;
        report["refusals"][name]=error.what();return;
      }
      throw std::runtime_error(std::string("Tampered serialization was accepted: ")+name);
    };
    const auto position=hit.getPosition();
    hit.setPosition({std::nextafter(position.x,std::numeric_limits<float>::infinity()),position.y,position.z});
    refuse("global_one_binary32_ULP","Global binary32 position");hit.setPosition(position);
    const auto local=measurement.getLoc();
    measurement.setLoc({std::nextafter(local.a,std::numeric_limits<float>::infinity()),local.b});
    refuse("local_one_binary32_ULP","Local binary32 coordinates");measurement.setLoc(local);
    auto covariance=measurement.getCovariance();auto wrongCovariance=covariance;
    wrongCovariance.xx=std::nextafter(covariance.xx,std::numeric_limits<float>::infinity());
    measurement.setCovariance(wrongCovariance);refuse("covariance_one_binary32_ULP","Measurement binary32 covariance");measurement.setCovariance(covariance);
    const auto cell=hit.getCellID();hit.setCellID(hits[3].getCellID());
    refuse("wrong_channel","RawHit/CellID/CartesianGridXZ association");hit.setCellID(cell);
    const auto errors=hit.getPositionError();auto wrongErrors=errors;
    wrongErrors.xx=std::nextafter(errors.xx,std::numeric_limits<float>::infinity());hit.setPositionError(wrongErrors);
    refuse("pixel_variance_one_binary32_ULP","TrackerHit binary32 variance");hit.setPositionError(errors);
    eicrecon::telescope::auditMeasurementSerialization(hit,measurement,*provider);
    report["passed"]=true;std::ofstream output(argv[2]);output<<report.dump(2)<<'\n';return 0;
  } catch(const std::exception& error) {std::cerr<<error.what()<<'\n';return 1;}
}
