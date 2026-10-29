// Real-Geometry positive and tampered-observation tests for the audit only.
#include "NavigationAudit.h"
#include <DD4hep/Detector.h>
#include <algorithms/interfaces/ActsSvc.h>
#include <spdlog/sinks/stdout_color_sinks.h>
#include <filesystem>
#include <fstream>
#include <iostream>

int main(int argc,char** argv) {
  if(argc!=3 || std::filesystem::exists(argv[2])) {
    std::cerr<<"Usage: TelescopeNavigationAudit XML NEW_JSON\n";return 64;
  }
  try {
    auto& detector=dd4hep::Detector::getInstance();detector.fromXML(argv[1]);
    detector.apply("DD4hepVolumeManager",0,nullptr);algorithms::GeoSvc::instance().init(&detector);
    auto provider=std::make_shared<ActsGeometryProvider>();
    auto logger=spdlog::stdout_color_mt("navigation_audit_test");
    provider->initialize(&detector,"",logger,logger);algorithms::ActsSvc::instance().init(provider);
    const auto converter=algorithms::GeoSvc::instance().cellIDPositionConverter();
    std::vector<std::uint64_t> ids;
    std::set<std::uint64_t> measured;
    for(int layer=0;layer<7;++layer) {
      const std::uint64_t system=layer<5?31:(layer==5?100:92);
      const auto* volume=converter->findContext(system|(std::uint64_t(layer)<<11)|(1ULL<<22)|(1ULL<<28));
      if(!volume) throw std::runtime_error("Missing actual sensor context in navigation test");
      const auto id=provider->surfaceMap().at(volume->identifier)->geometryId().value();
      ids.push_back(id);if(layer<5) measured.insert(id);
    }
    const auto* last=provider->surfaceMap().at(converter->findContext(31ULL|(4ULL<<11)|(1ULL<<22)|(1ULL<<28))->identifier);
    const auto center=last->center(provider->getActsGeometryContext());
    nlohmann::json report={{"passed",false},{"positive",nlohmann::json::object()},{"refusals",nlohmann::json::object()}};
    auto test=[&](const char* name,const Acts::Vector3& point,const Acts::Vector3& direction,
                  const std::vector<std::uint64_t>& holes) {
      report["positive"][name]=eicrecon::telescope::auditNavigationRay(*provider,point,direction,ids[4],measured,holes);
    };
    const Acts::Vector3 edge=center+Acts::Vector3(0.,45.,0.);
    const auto tilted=Acts::Vector3(1.,.08,0.).normalized().eval();
    test("central_two_holes",center,Acts::Vector3::UnitX(),{ids[5],ids[6]});
    test("finite_ACLGAD_miss_one_hole",edge,tilted,{ids[5]});
    test("both_unused_surfaces_missed_zero_holes",center+Acts::Vector3(0.,49.,0.),tilted,{});
    auto refuse=[&](const char* name,const std::set<std::uint64_t>& chosen,
                    const std::vector<std::uint64_t>& holes,const char* reason) {
      try {
        eicrecon::telescope::auditNavigationRay(*provider,edge,tilted,ids[4],chosen,holes);
      } catch(const std::runtime_error& error) {
        if(std::string(error.what()).find(reason)==std::string::npos) throw;
        report["refusals"][name]=error.what();return;
      }
      throw std::runtime_error(std::string("Tampered observation was accepted: ")+name);
    };
    refuse("missing_real_hole",measured,{},"Actual CKF holes differ");
    refuse("extra_outside_bounds_hole",measured,{ids[5],ids[6]},"Actual CKF holes differ");
    refuse("duplicate_hole",measured,{ids[5],ids[5]},"duplicate hole");
    refuse("measured_surface_marked_hole",measured,{ids[4]},"measured or duplicate");
    refuse("unknown_hole_identifier",measured,{1},"Unknown, measured");
    auto missing=measured;missing.erase(ids[0]);
    refuse("missing_measured_sensor",missing,{ids[5]},"Invalid last-measurement anchor or measured surface set");
    report["passed"]=true;report["fit_executed"]=false;report["truth_used"]=false;
    std::ofstream output(argv[2]);output<<report.dump(2)<<'\n';return 0;
  } catch(const std::exception& error) {std::cerr<<error.what()<<'\n';return 1;}
}
