// Offline Truth-channel generator and independent DD4hep placement audit.
// Reconstruction consumes only packed channel data, never this Truth report.
#include <DD4hep/Detector.h>
#include <DD4hep/Segmentations.h>
#include <DDRec/CellIDPositionConverter.h>
#include <Evaluator/DD4hepUnits.h>
#include <algorithms/geo.h>
#include <algorithms/interfaces/ActsSvc.h>
#include <algorithms/tracking/ActsGeometryProvider.h>
#include <nlohmann/json.hpp>
#include <spdlog/sinks/stdout_color_sinks.h>
#include <filesystem>
#include <fstream>
#include <iostream>
#include <cmath>
#include <random>
int main(int argc,char** argv) {
  if((argc!=4 && argc!=5) || std::filesystem::exists(argv[3])) {std::cerr<<"Usage: TelescopeChannels XML EVENT_COUNT_OR_ZERO_AUDIT NEW_JSON [BOUNDED_BEAM_JSON]\n";return 64;}
  try {
    nlohmann::json beam;
    if(argc==5) {std::ifstream input(argv[4]);input>>beam;
      if(beam.at("recipe")!="bounded_uniform_tilt_v1" || beam.at("origin_half_y_mm")!=12. ||
         beam.at("origin_half_z_mm")!=10. || beam.at("slope_half_range")!=.08)
        throw std::runtime_error("Only the declared bounded tilt-study beam is supported");
    }
    const int count=std::stoi(argv[2]);if(count<0 || count>(beam.is_null()?64:4096))throw std::runtime_error("Expected 0..64 baseline or 0..4096 study events");
    std::mt19937_64 random(beam.is_null()?0:beam.at("seed").get<uint64_t>());
    std::uniform_real_distribution<double> uniform(-1.,1.);
    auto& detector=dd4hep::Detector::getInstance();detector.fromXML(argv[1]);detector.apply("DD4hepVolumeManager",0,nullptr);
    algorithms::GeoSvc::instance().init(&detector);
    auto provider=std::make_shared<ActsGeometryProvider>();auto logger=spdlog::stdout_color_mt("channel_geometry");
    provider->initialize(&detector,"",logger,logger);algorithms::ActsSvc::instance().init(provider);
    const auto converter=algorithms::GeoSvc::instance().cellIDPositionConverter();
    const auto& gctx=provider->getActsGeometryContext();
    nlohmann::json report={{"events",nlohmann::json::array()},{"geometry",argv[1]},
      {"generation","actual DD4hep sensitive-plane intersection and CartesianGridXZ quantization"}};
    auto world=[](const auto* context,const dd4hep::Position& local) {
      const auto p=context->localToWorld(local);return Acts::Vector3(p.x()/dd4hep::mm,p.y()/dd4hep::mm,p.z()/dd4hep::mm);
    };
    for(int layer=0;layer<7;++layer) {
      const uint64_t system=layer<5?31:(layer==5?100:92);
      const uint64_t base=system|(uint64_t(layer)<<11)|(1ULL<<22)|(1ULL<<28);
      const auto* context=converter->findContext(base);if(!context)throw std::runtime_error("Missing real detector Volume context");
      const auto center=world(context,{0,0,0});const auto* surface=provider->surfaceMap().at(context->identifier);
      nlohmann::json rotation=nlohmann::json::array();
      for(int i=0;i<3;++i) {nlohmann::json row=nlohmann::json::array();for(int j=0;j<3;++j)row.push_back(surface->localToGlobalTransform(gctx).linear()(i,j));rotation.push_back(row);}
      nlohmann::json ddRotation=nlohmann::json::array();
      Acts::RotationMatrix3 native;
      for(int j=0;j<3;++j) {
        dd4hep::Position axis(j==0?dd4hep::mm:0,j==1?dd4hep::mm:0,j==2?dd4hep::mm:0);
        native.col(j)=(world(context,axis)-center).normalized();
      }
      for(int i=0;i<3;++i) {auto row=nlohmann::json::array();for(int j=0;j<3;++j)row.push_back(native(i,j));ddRotation.push_back(row);}
      // ACTS centers and native DD4hep centers must describe the same sensor.
      if((center-surface->center(gctx)).norm()>1e-8)throw std::runtime_error("DD4hep/ACTS center mismatch");
      report["sensors"].push_back({{"layer",layer},{"volume_id",context->identifier},{"geometry_id",surface->geometryId().value()},
        {"center_mm",{center.x(),center.y(),center.z()}},{"acts_rotation",rotation},{"dd4hep_rotation",ddRotation}});
    }
    if(!beam.is_null())report["beam_recipe"]=beam;
    auto fraction=[](double x){return x-std::floor(x);};
    for(int event=0;event<count;++event) {
      Acts::Vector3 origin(250.,-12.+24.*fraction(.137+.61803398875*event),-10.+20.*fraction(.231+.41421356237*event));
      Acts::Vector3 direction=Acts::Vector3(1.,-.008+.016*fraction(.31+.7548776662*event),-.006+.012*fraction(.27+.569840291*event)).normalized();
      if(!beam.is_null()) {
        // Draw independent intercepts and slopes; preserve the baseline recipe.
        const double y=12.*uniform(random),z=10.*uniform(random);
        const double sy=.08*uniform(random),sz=.08*uniform(random);
        origin=Acts::Vector3(250.,y,z);direction=Acts::Vector3(1.,sy,sz).normalized();
      }
      nlohmann::json data={{"event",event},{"origin_mm",{origin.x(),origin.y(),origin.z()}},
        {"direction",{direction.x(),direction.y(),direction.z()}},{"channels",nlohmann::json::array()}};
      for(int layer=0;layer<5;++layer) {
        const uint64_t base=31ULL|(uint64_t(layer)<<11)|(1ULL<<22)|(1ULL<<28);
        const auto* context=converter->findContext(base);
        const Acts::Vector3 center=world(context,{0,0,0});
        const Acts::Vector3 normal=(world(context,{0,dd4hep::mm,0})-center).normalized();
        const double path=normal.dot(center-origin)/normal.dot(direction);
        const Acts::Vector3 point=origin+path*direction;
        const dd4hep::Position global(point.x()*dd4hep::mm,point.y()*dd4hep::mm,point.z()*dd4hep::mm);
        const auto local=context->worldToLocal(global);
        if(std::abs(local.y()/dd4hep::mm)>1e-8 || std::abs(local.x()/dd4hep::mm)>49. || std::abs(local.z()/dd4hep::mm)>49.)
          throw std::runtime_error("Generated trajectory misses bounded physical sensor");
        const uint64_t cell=detector.readout("SiMAPSHits").segmentation().cellID(local,global,base);
        if(converter->findContext(cell)->identifier!=context->identifier)throw std::runtime_error("Quantization changed sensor identity");
        const auto quantized=context->worldToLocal(converter->position(cell));
        const auto dimensions=converter->cellDimensions(cell);
        if(dimensions.size()!=2 || std::abs(local.x()-quantized.x())>dimensions[0]/2.+1e-10 || std::abs(local.z()-quantized.z())>dimensions[1]/2.+1e-10)
          throw std::runtime_error("Pixel quantization bound violated");
        data["channels"].push_back({{"layer",layer},{"cell_id",cell},{"charge",1},{"timestamp",0},
          {"physical_intersection_mm",{point.x(),point.y(),point.z()}},
          {"sensor_x_z_mm",{local.x()/dd4hep::mm,local.z()/dd4hep::mm}},
          {"pitch_x_z_mm",{dimensions[0]/dd4hep::mm,dimensions[1]/dd4hep::mm}}});
      }
      report["events"].push_back(data);
    }
    report["passed"]=true;std::ofstream output(argv[3]);output<<report.dump(2)<<'\n';return 0;
  }catch(const std::exception& error){std::cerr<<error.what()<<'\n';return 2;}
}
