// Independent physical containment/overlap audit; no tracking or alignment fit.
#include <algorithms/tracking/ActsGeometryProvider.h>
#include <Acts/Geometry/TrackingGeometry.hpp>
#include <Acts/Propagator/Navigator.hpp>
#include <DD4hep/Detector.h>
#include <DDRec/CellIDPositionConverter.h>
#include <TGeoBBox.h>
#include <TGeoManager.h>
#include <TGeoMaterial.h>
#include <TGeoMatrix.h>
#include <TGeoTube.h>
#include <TObjArray.h>
#include <nlohmann/json.hpp>
#include <spdlog/sinks/stdout_color_sinks.h>
#include <algorithm>
#include <cmath>
#include <filesystem>
#include <fstream>
#include <iostream>
#include <map>
#include <set>
#include <vector>

using Json=nlohmann::json;
using Vector=Acts::Vector3;
constexpr double tolerance=1e-9; // mm, matching the XML containment gate.

Vector world(const TGeoHMatrix& matrix,const Vector& local) {
  const double point[]={local.x()/10.,local.y()/10.,local.z()/10.};double result[3];
  matrix.LocalToMaster(point,result);return 10.*Vector(result[0],result[1],result[2]);
}
Vector local(const TGeoHMatrix& matrix,const Vector& global) {
  const double point[]={global.x()/10.,global.y()/10.,global.z()/10.};double result[3];
  matrix.MasterToLocal(point,result);return 10.*Vector(result[0],result[1],result[2]);
}
Json vector(const Vector& value) {return {value.x(),value.y(),value.z()};}
template<class Operation> void corners(const Vector& half,Operation operation) {
  for(int x:{-1,1})for(int y:{-1,1})for(int z:{-1,1})operation(Vector(x*half.x(),y*half.y(),z*half.z()));
}
void require(bool condition,const std::string& message) {if(!condition)throw std::runtime_error(message);}

int main(int argc,char** argv) {
  if(argc!=3 || std::filesystem::exists(argv[2])) {
    std::cerr<<"Usage: TelescopePhysicalGeometryAudit XML NEW_JSON\n";return 64;
  }
  Json report={{"passed",false},{"fit_executed",false},{"truth_used",false},
    {"method","actual DD4hep solids and transforms, TGeo overlaps, installed ActsGeometryProvider and Navigator"},
    {"sensors",Json::array()},{"navigation",Json::array()}};
  try {
    auto& detector=dd4hep::Detector::getInstance();detector.fromXML(argv[1]);
    detector.apply("DD4hepVolumeManager",0,nullptr);
    dd4hep::rec::CellIDPositionConverter converter(detector);
    auto provider=std::make_shared<ActsGeometryProvider>();
    auto logger=spdlog::stdout_color_mt("physical_geometry_audit");
    provider->initialize(&detector,"",logger,logger);
    require(provider->trackingGeometry()!=nullptr && provider->surfaceMap().size()==7,"Expected real seven-surface TrackingGeometry");
    const auto& context=provider->getActsGeometryContext();
    const Acts::RotationMatrix3 parent=(Acts::RotationMatrix3()<<0.,0.,1.,-1.,0.,0.,0.,-1.,0.).finished();
    const Acts::RotationMatrix3 sensorFromActs=(Acts::RotationMatrix3()<<0.,0.,-1.,1.,0.,0.,0.,-1.,0.).finished();
    std::set<std::uint64_t> volumes,identities;std::map<std::uint64_t,int> surfaceLayers;
    auto* worldBox=dynamic_cast<TGeoBBox*>(gGeoManager->GetTopVolume()->GetShape());
    require(worldBox!=nullptr,"Expected the actual Box world");
    require(std::string(gGeoManager->GetTopVolume()->GetMaterial()->GetName())=="Air",
            "Air-envelope growth requires the actual Air world");
    const Vector worldHalf(10.*worldBox->GetDX(),10.*worldBox->GetDY(),10.*worldBox->GetDZ());
    double previousOuter=0.;
    for(int layer=0;layer<7;++layer) {
      const std::uint64_t system=layer<5?31:(layer==5?100:92);
      const std::uint64_t cell=system|(std::uint64_t(layer)<<11)|(1ULL<<22)|(1ULL<<28);
      const auto* volume=converter.findContext(cell);require(volume!=nullptr,"Missing actual Volume context");
      const auto sensor=volume->element,module=sensor.parent(),envelope=module.parent();
      auto* sensitive=dynamic_cast<TGeoBBox*>(sensor.volume().solid().ptr());
      auto* box=dynamic_cast<TGeoBBox*>(module.volume().solid().ptr());
      auto* tube=dynamic_cast<TGeoTube*>(envelope.volume().solid().ptr());
      require(sensitive && box && tube,"Expected actual sensitive Box, module Box and layer Tube hierarchy");
      require(std::string(sensor.volume().material().name())=="Silicon" &&
              std::string(module.volume().material().name())=="Air" &&
              std::string(envelope.volume().material().name())=="Air","Sensitive and Air material assignments changed");
      const Vector half(10.*sensitive->GetDX(),10.*sensitive->GetDY(),10.*sensitive->GetDZ());
      const Vector expectedHalf(50.,layer<5?.025:(layer==5?1.5:.175),50.);
      require((half-expectedHalf).cwiseAbs().maxCoeff()<1e-10,"Sensitive dimensions changed");
      const Vector moduleHalf(10.*box->GetDX(),10.*box->GetDY(),10.*box->GetDZ());
      const auto& sensorWorld=sensor.nominal().worldTransformation();
      const auto& moduleWorld=module.nominal().worldTransformation();
      const auto& layerWorld=envelope.nominal().worldTransformation();
      const Vector center=world(sensorWorld,Vector::Zero()),moduleCenter=world(moduleWorld,Vector::Zero());
      Acts::RotationMatrix3 ddRotation,moduleRotation;
      for(int axis=0;axis<3;++axis) {
        ddRotation.col(axis)=world(sensorWorld,Vector::Unit(axis))-center;
        moduleRotation.col(axis)=world(moduleWorld,Vector::Unit(axis))-moduleCenter;
      }
      require((moduleRotation-parent).cwiseAbs().maxCoeff()<2e-12 && std::abs(moduleCenter.y())<tolerance &&
              std::abs(moduleCenter.z())<tolerance && moduleCenter.x()>0.,"Unsupported actual module placement");
      const auto* surface=provider->surfaceMap().at(volume->identifier);
      const auto transform=surface->localToGlobalTransform(context);
      require((transform.translation()-center).norm()<1e-9 &&
              (transform.linear()*sensorFromActs-ddRotation).cwiseAbs().maxCoeff()<2e-12,"Actual DD4hep and ACTS sensor frames differ");
      require(surface->surfaceMaterial()==nullptr,"Unexpected ACTS surface material");
      require(volumes.insert(volume->identifier).second && identities.insert(surface->geometryId().value()).second,"Duplicate actual detector identity");
      surfaceLayers.emplace(surface->geometryId().value(),layer);
      for(std::uint64_t channel:{std::uint64_t(0),std::uint64_t(1)<<32,std::uint64_t(1)<<48})
        require(converter.findContext(cell|channel)->identifier==volume->identifier,"CellID channels changed sensitive association");
      Vector maximum=Vector::Zero();Json sensitiveCorners=Json::array();
      corners(half,[&](const Vector& point) {
        const auto global=world(sensorWorld,point);const auto inModule=local(moduleWorld,global);
        maximum=maximum.cwiseMax(inModule.cwiseAbs());sensitiveCorners.push_back(vector(global));
      });
      const Vector clearance=moduleHalf-maximum;
      require(clearance.minCoeff()>=-tolerance,"Actual sensitive component protrudes from its Air module");
      const double inner=10.*tube->GetRmin(),outer=10.*tube->GetRmax(),halfZ=10.*tube->GetDz();
      require(std::isfinite(inner) && inner>0. && inner<outer && halfZ>0. && inner>=previousOuter-tolerance,"Invalid or overlapping actual layer envelopes");
      previousOuter=outer;
      const auto inLayer=local(layerWorld,moduleCenter);
      const Vector worldExtent=parent.cwiseAbs()*moduleHalf;
      const Vector layerClearance(inLayer.x()-worldExtent.x()-inner,
        outer-std::hypot(inLayer.x()+worldExtent.x(),worldExtent.y()),halfZ-worldExtent.z());
      require(layerClearance.minCoeff()>=-tolerance,"Actual module protrudes from its Air layer");
      corners(moduleHalf,[&](const Vector& point) {
        require((worldHalf-world(moduleWorld,point).cwiseAbs()).minCoeff()>=-tolerance,"Actual module protrudes from world");
      });
      report["sensors"].push_back({{"layer",layer},{"cell_id",cell},{"volume_id",volume->identifier},
        {"geometry_id",surface->geometryId().value()},{"center_mm",vector(center)},
        {"sensitive_half_mm",vector(half)},{"module_half_mm",vector(moduleHalf)},
        {"module_clearance_mm",vector(clearance)},{"layer_clearance_mm",vector(layerClearance)},
        {"layer_inner_outer_half_z_mm",{inner,outer,halfZ}},{"global_sensitive_corners_mm",sensitiveCorners}});
    }
    // Full physical hierarchy overlap check supplements the tighter analytical
    // canonical-box/cylinder checks above. ROOT takes this tolerance in cm.
    gGeoManager->CheckOverlaps(1e-7);
    const auto* overlaps=gGeoManager->GetListOfOverlaps();
    report["TGeo_overlap_tolerance_mm"]=1e-6;
    report["TGeo_overlap_count"]=overlaps?overlaps->GetEntriesFast():0;
    require(!overlaps || overlaps->GetEntriesFast()==0,"Actual TGeo hierarchy has overlaps or extrusions");
    for(const auto& ray:std::vector<std::pair<const char*,Vector>>{{"central",{249.,0.,0.}},
          {"off_axis",{249.,3.,-2.}},{"opposite_phi",{-249.,0.,0.}}}) {
      const Vector direction=ray.first==std::string("opposite_phi")?Vector(-1.,0.,0.):Vector(1.,0.,0.);
      Acts::Navigator::Config cfg;cfg.trackingGeometry=provider->trackingGeometry();
      cfg.resolveSensitive=true;cfg.resolveMaterial=false;cfg.resolvePassive=false;
      Acts::Navigator navigator(cfg);Acts::Navigator::Options options(context);auto state=navigator.makeState(options);
      Vector position=ray.second;require(navigator.initialize(state,position,direction,Acts::Direction::Forward()).ok(),"Official Navigator initialization failed");
      std::vector<int> hits;bool terminated=false;
      for(unsigned step=0;step<1000;++step) {
        const auto target=navigator.nextTarget(state,position,direction);
        if(target.isNone()){terminated=true;break;}
        position=target.intersection().position();
        const auto found=surfaceLayers.find(target.surface().geometryId().value());
        if(found!=surfaceLayers.end())hits.push_back(found->second);
        navigator.handleSurfaceReached(state,position,direction,target.surface());
      }
      const std::vector<int> expected=ray.first==std::string("opposite_phi")?std::vector<int>{}:std::vector<int>{0,1,2,3,4,5,6};
      require(terminated && navigator.endOfWorldReached(state) && hits==expected,"Official Navigator sensitive crossings differ");
      report["navigation"].push_back({{"name",ray.first},{"hits",hits},{"terminated",terminated},{"end_of_world",true}});
    }
    report["passed"]=true;
  } catch(const std::exception& error) {report["error"]=error.what();}
  std::ofstream output(argv[2]);output<<report.dump(2)<<'\n';
  return report["passed"].get<bool>()?0:1;
}
