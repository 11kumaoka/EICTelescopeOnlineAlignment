// Isolated installed-provider and Navigator audit. No fitting, alignment, or Geometry activation.
#include <Acts/ActsVersion.hpp>
#include <Acts/Propagator/Navigator.hpp>
#include <Acts/Propagator/Propagator.hpp>
#include <Acts/Propagator/EigenStepper.hpp>
#include <Acts/Propagator/SurfaceCollector.hpp>
#include <Acts/Propagator/StandardAborters.hpp>
#include <Acts/MagneticField/ConstantBField.hpp>
#include <Acts/EventData/TrackParameters.hpp>
#include <Acts/Geometry/ApproachDescriptor.hpp>
#include <Acts/Surfaces/SurfaceArray.hpp>
#include <map>
#include <Acts/Geometry/TrackingGeometry.hpp>
#include <Acts/Geometry/TrackingVolume.hpp>
#include <Acts/Geometry/VolumeBounds.hpp>
#include <Acts/Geometry/Layer.hpp>
#include <Acts/Utilities/BinnedArray.hpp>
#include <Acts/Surfaces/CylinderBounds.hpp>
#include <TGeoBBox.h>
#include <Acts/Surfaces/PlaneSurface.hpp>
#include <Acts/Surfaces/RectangleBounds.hpp>
#include <ActsPlugins/DD4hep/DD4hepConversionHelpers.hpp>
#include <ActsPlugins/DD4hep/DD4hepDetectorElement.hpp>
#include <DD4hep/Detector.h>
#include <DD4hep/DetType.h>
#include <DDRec/DetectorData.h>
#include <DDRec/CellIDPositionConverter.h>
#include <algorithms/tracking/ActsGeometryProvider.h>
#include <spdlog/sinks/stdout_color_sinks.h>
#include <nlohmann/json.hpp>
#include <fcntl.h>
#include <unistd.h>
#include <array>
#include <filesystem>
#include <fstream>
#include <iostream>
#include <set>


using Json = nlohmann::json;
namespace {
Json matrix(const Acts::Transform3& t) {
  Json r=Json::array();
  for(int i=0;i<3;++i)r.push_back({t.linear()(i,0),t.linear()(i,1),t.linear()(i,2)});
  return r;
}
Json vector(const Acts::Vector3& v) { return {v[0],v[1],v[2]}; }
Json surfaceInfo(const Acts::Surface& s,const Acts::GeometryContext& ctx) {
  const auto t=s.localToGlobalTransform(ctx);
  double error=0.;
  for(const Acts::Vector2 point : {Acts::Vector2(0.,0.),Acts::Vector2(10.,-7.)}) {
    const auto world=s.localToGlobal(ctx,point,t.linear().col(2));
    const auto local=s.globalToLocal(ctx,world,t.linear().col(2));
    if(!local.ok())throw std::runtime_error("Surface local/global round trip failed");
    error=std::max(error,(local.value()-point).norm());
  }
  Json corners=Json::array();
  for(double x : {-49.999,49.999})for(double y : {-49.999,49.999})
    corners.push_back(s.bounds().inside(Acts::Vector2(x,y)));
  return {{"center_mm",vector(t.translation())},{"rotation",matrix(t)},
    {"u",vector(t.linear().col(0))},{"v",vector(t.linear().col(1))},
    {"normal",vector(t.linear().col(2))},{"bounds_type",int(s.bounds().type())},
    {"bounds_values",s.bounds().values()},{"square_interior_corner_checks",corners},
    {"round_trip_error_mm",error},{"geometry_id",s.geometryId().value()},
    {"material_assigned",s.surfaceMaterial()!=nullptr}};
}
Json hierarchy(dd4hep::DetElement element) {
  Json children=Json::array(),params=Json::array();
  for(const auto& [_,child] : element.children())children.push_back(hierarchy(child));
  if(auto* p=element.extension<dd4hep::rec::VariantParameters>(false))
    for(const auto& [key,_] : p->variantParameters)params.push_back(key);
  return {{"name",element.name()},{"path",element.path()},{"type",element.type()},
    {"type_flags",element.typeFlag()},{"volume_id",element.volumeID()},
    {"sensitive",element.volume().isSensitive()},
    {"layer_pattern",ActsPlugins::getParamOr<std::string>("layer_pattern",element,"$^")},
    {"parameter_keys",params},{"children",children}};
}
dd4hep::rec::VariantParameters& params(dd4hep::DetElement element) {
  auto* p=element.extension<dd4hep::rec::VariantParameters>(false);
  if(!p) {p=new dd4hep::rec::VariantParameters();element.addExtension<dd4hep::rec::VariantParameters>(p);}
  return *p;
}
void experimentalMetadata(dd4hep::Detector& detector,bool flags) {
  // Process-local discovery experiment only. No placement, shape or XML edits.
  for(const char* name : {"SiMAPS","MPGDPlane","ACLGADPlane"}) {
    auto det=detector.detector(name);
    params(det).set<std::string>("layer_pattern","^layer[0-6]$");
    if(flags)det.setTypeFlag(dd4hep::DetType::TRACKER|dd4hep::DetType::BARREL);
    for(const auto& [_,module] : det.children()) {
      auto& p=params(module);
      (void)p;
      for(const auto& [__,mod] : module.children())for(const auto& [___,sensor] : mod.children())params(sensor).set<std::string>("axis_definitions","zXy");
    }
  }
}
void writeExclusive(const std::filesystem::path& path,const Json& report) {
  const int fd=::open(path.c_str(),O_WRONLY|O_CREAT|O_EXCL,0644);
  if(fd<0)throw std::runtime_error("Output must be a new writable file: "+path.string());
  const auto data=report.dump(2)+"\n";std::size_t offset=0;
  while(offset<data.size()) {
    const auto n=::write(fd,data.data()+offset,data.size()-offset);
    if(n<=0) {::close(fd);throw std::runtime_error("Cannot write Geometry report");}
    offset+=n;
  }
  ::close(fd);
}
} // namespace

int main(int argc,char** argv) {
  if(argc!=6) {
    std::cerr<<"Usage: TelescopeGeometryProbe XML OUTPUT_JSON local|official|official-elements none|discovery|discovery-and-flags nominal|translated|tilted\n";
    return 64;
  }
  const std::string provider=argv[3],metadata=argv[4],scenario=argv[5];
  if(scenario!="nominal" && scenario!="translated" && scenario!="tilted")return 64;
  if((provider!="local" && provider!="official" && provider!="official-elements") ||
     (metadata!="none" && metadata!="discovery" && metadata!="discovery-and-flags"))return 64;
  if(std::filesystem::exists(argv[2])) {std::cerr<<"Refusing existing output\n";return 64;}
  Json report={{"provider",provider},{"scenario",scenario},{"experimental_metadata",metadata},
    {"acts_version",{Acts::VersionMajor,Acts::VersionMinor,Acts::VersionPatch}},
    {"tracking_geometry_available",false},{"inference_enabled",false},{"component_axis_definitions","zXy"},
    {"material_map","disabled for zero-material/process-noise comparison"}};
  try {
    auto& detector=dd4hep::Detector::getInstance();
    detector.fromXML(argv[1]);detector.apply("DD4hepVolumeManager",0,nullptr);
    dd4hep::rec::CellIDPositionConverter converter(detector);
    std::array<Acts::Transform3,7> local;
    for(int i=0;i<7;++i) { local[i]=Acts::Transform3::Identity(); local[i].translation()=Acts::Vector3(250.+50.*i,0.,0.); local[i].linear()<<0,0,1,-1,0,0,0,-1,0;
      if(scenario=="translated")local[i].translation()+=Acts::Vector3(0.,-.2,-.1);
      if(scenario=="tilted")local[i].linear()=local[i].linear()*Eigen::AngleAxisd(.005,Acts::Vector3::UnitX()).toRotationMatrix();
    }
    report["original_hierarchy"]=hierarchy(detector.world());
    if(metadata!="none")experimentalMetadata(detector,metadata=="discovery-and-flags");
    report["selected_hierarchy"]=hierarchy(detector.world());
    Acts::GeometryContext context=Acts::GeometryContext::dangerouslyDefaultConstruct();
    std::shared_ptr<ActsGeometryProvider> upstream;
    std::vector<std::shared_ptr<ActsPlugins::DD4hepDetectorElement>> elements;
    std::array<const Acts::Surface*,7> surfaces{};
    std::vector<std::shared_ptr<Acts::PlaneSurface>> localSurfaces;
    std::string conversionError;
    if(provider=="official") {
      upstream=std::make_shared<ActsGeometryProvider>();
      auto log=spdlog::stdout_color_mt("official_geometry");log->set_level(spdlog::level::trace);
      try {
        upstream->initialize(&detector,"",log,log);
        if(!upstream->trackingGeometry())throw std::runtime_error("Official provider returned no TrackingGeometry");
        context=upstream->getActsGeometryContext();report["tracking_geometry_available"]=true;
        report["official_surface_map_size"]=upstream->surfaceMap().size();
      } catch(const std::exception& e) {conversionError=e.what();report["conversion_error"]=conversionError;}
    }
    std::set<uint64_t> volumeIds,geometryIds;
    for(int layer=0;layer<7;++layer) {
      const uint64_t system=layer<5?31:(layer==5?100:92);
      const uint64_t cell=system|(uint64_t(layer)<<11)|(1ULL<<22)|(1ULL<<28);
      const auto* volume=converter.findContext(cell);
      if(!volume || !volumeIds.insert(volume->identifier).second)throw std::runtime_error("Missing/duplicate sensitive Volume ID association");
      auto reference=Acts::Surface::makeShared<Acts::PlaneSurface>(local[layer],std::make_shared<Acts::RectangleBounds>(50.,50.));
      reference->assignGeometryId(Acts::GeometryIdentifier().withVolume(1).withLayer(layer+1).withSensitive(1));
      Json row={{"layer",layer},{"cell_id",cell},{"volume_id",volume->identifier},
        {"context_element",volume->element.path()},{"local",surfaceInfo(*reference,context)}};
      auto* box=dynamic_cast<TGeoBBox*>(volume->element.volume().solid().ptr());
      if(!box)throw std::runtime_error("Sensitive solid is not a Box");
      row["dd4hep_material"]=volume->element.volume().material().name();
      row["physical_half_lengths_mm"]={box->GetDX()*10.,box->GetDY()*10.,box->GetDZ()*10.};
      row["physical_component_rotation"]=Json::array();row["physical_world_rotation"]=Json::array();
      for(int j=0;j<9;++j){row["physical_component_rotation"].push_back(volume->element.placement().ptr()->GetMatrix()->GetRotationMatrix()[j]);row["physical_world_rotation"].push_back(volume->element.nominal().worldTransformation().GetRotationMatrix()[j]);}
      for(const uint64_t channel : {uint64_t(0),uint64_t(1)<<32,uint64_t(1)<<48})
        if(converter.findContext(cell|channel)->identifier!=volume->identifier)throw std::runtime_error("CellID channels map to different sensitive contexts");
      if(provider=="local") {
        surfaces[layer]=reference.get();localSurfaces.push_back(reference);
      } else if(provider=="official-elements") {
        if(!volume->element.volume().isSensitive())throw std::runtime_error("Volume context is not a sensitive DetElement");
        // Record the native missing-metadata default separately, never use it as
        // an equivalent surface for tracking or alignment.
        try {
          auto native=std::make_shared<ActsPlugins::DD4hepDetectorElement>(volume->element,ActsPlugins::TGeoAxes::parse("XYZ"),Acts::UnitConstants::cm);
          row["native_default_XYZ"]=surfaceInfo(native->surface(),context);
          row["native_default_XYZ"]["thickness_mm"]=native->thickness();
        } catch(const std::exception& e) {
          row["native_default_XYZ"]={{"conversion_error",e.what()}};
        }
        auto element=std::make_shared<ActsPlugins::DD4hepDetectorElement>(volume->element,ActsPlugins::TGeoAxes::parse("zXy"),Acts::UnitConstants::cm);
        if(element->identifier()!=volume->identifier)throw std::runtime_error("Official detector-element/Volume ID mismatch");
        surfaces[layer]=&element->surface();row["sensitive_thickness_mm"]=element->thickness();elements.push_back(element);
      } else if(conversionError.empty()) {
        const auto found=upstream->surfaceMap().find(volume->identifier);
        if(found==upstream->surfaceMap().end())throw std::runtime_error("Official TrackingGeometry has no surface for a telescope Volume ID");
        surfaces[layer]=found->second;
        if(!geometryIds.insert(surfaces[layer]->geometryId().value()).second)throw std::runtime_error("Official GeometryIdentifiers are not unique");
      }
      if(surfaces[layer]) {
        const auto& s=*surfaces[layer];row["selected"]=surfaceInfo(s,context);
        row["is_plane_surface"]=dynamic_cast<const Acts::PlaneSurface*>(&s)!=nullptr;
        if(const auto* de=dynamic_cast<const ActsPlugins::DD4hepDetectorElement*>(s.surfacePlacement())) {
          row["official_detector_identifier"]=de->identifier();row["official_thickness_mm"]=de->thickness();
        }
        const auto t=s.localToGlobalTransform(context);
        row["center_difference_mm"]=(t.translation()-local[layer].translation()).norm();
        row["max_rotation_difference"]=(t.linear()-local[layer].linear()).cwiseAbs().maxCoeff();
        const auto a=reference->bounds().values(),b=s.bounds().values();
        bool equalBounds=a.size()==b.size() && reference->bounds().type()==s.bounds().type();
        if(equalBounds)for(std::size_t j=0;j<a.size();++j)equalBounds=equalBounds && std::abs(a[j]-b[j])<1e-12;
        row["bounds_equivalent"]=equalBounds;
        row["equivalent"]=(equalBounds && double(row["center_difference_mm"])<1e-10 && double(row["max_rotation_difference"])<1e-12 && double(row["selected"]["round_trip_error_mm"])<1e-10);
      }
      report["planes"].push_back(row);
    }
    if(!conversionError.empty()) {
      report["passed"]=false;report["status"]="Official TrackingGeometry unavailable; production local path unchanged";
      writeExclusive(argv[2],report);return 2;
    }

    if(upstream && conversionError.empty()) {
      auto tg=upstream->trackingGeometry();const auto* world=tg->highestTrackingVolume();
      report["world_bounds"]=world->volumeBounds().values();
      world->visitVolumes([&](const Acts::TrackingVolume* v){
        report["volumes"].push_back({{"name",v->volumeName()},{"bounds",v->volumeBounds().values()}});
        if(const auto* layers=v->confinedLayers())for(const auto& l:layers->arrayObjects()) {
          if(l->surfaceArray())report["cylinder_layers"].push_back({{"bounds",l->surfaceRepresentation().bounds().values()},
            {"thickness_mm",l->layerThickness()},{"geometry_id",l->geometryId().value()}});
        }
      });
      std::map<const Acts::Surface*,std::size_t> materialRows;
      auto inventorySurface=[&](const Acts::Surface& surface,const std::string& role,const std::string& owner) {
        if(!materialRows.contains(&surface)) {
          const std::size_t index=materialRows.size();materialRows[&surface]=index;
          report["material_inventory"]["surfaces"].push_back({{"geometry_id",surface.geometryId().value()},
            {"material_assigned",surface.surfaceMaterial()!=nullptr},{"roles",Json::array()},
            {"bounds_type",int(surface.bounds().type())},{"bounds",surface.bounds().values()}});
        }
        report["material_inventory"]["surfaces"][materialRows.at(&surface)]["roles"].push_back({{"role",role},{"owner",owner}});
      };
      world->visitVolumes([&](const Acts::TrackingVolume* volume) {
        report["material_inventory"]["volumes"].push_back({{"name",volume->volumeName()},
          {"geometry_id",volume->geometryId().value()},{"material_assigned",volume->volumeMaterial()!=nullptr}});
        for(const auto& boundary:volume->boundarySurfaces())inventorySurface(boundary->surfaceRepresentation(),"boundary",volume->volumeName());
        if(const auto* layers=volume->confinedLayers())for(const auto& layer:layers->arrayObjects()) {
          const auto owner=volume->volumeName()+":"+std::to_string(layer->geometryId().value());
          inventorySurface(layer->surfaceRepresentation(),"layer_representation",owner);
          if(const auto* approach=layer->approachDescriptor())for(const auto* surface:approach->containedSurfaces())
            inventorySurface(*surface,"approach",owner);
          if(const auto* array=layer->surfaceArray())for(const auto* surface:array->surfaces())inventorySurface(*surface,"sensitive",owner);
        }
      });
      Acts::Navigator::Config cfg;cfg.trackingGeometry=tg;cfg.resolveSensitive=true;cfg.resolveMaterial=false;cfg.resolvePassive=false;
      Acts::Navigator nav(cfg);
      for(const auto& ray:std::vector<std::pair<std::string,Acts::Vector3>>{
          {"outside_start",{240.,0.,0.}},{"bounded_miss",{240.,0.,70.}}}) {
        Json row={{"name",ray.first},{"hits",Json::array()},{"path_lengths_mm",Json::array()}};
        for(int i=0;i<7;++i) {
          const auto intersection=surfaces[i]->intersect(context,ray.second,Acts::Vector3::UnitX(),Acts::BoundaryTolerance::None()).closestForward();
          if(intersection.isValid() && intersection.pathLength()>=0.) {
            row["hits"].push_back(i);row["path_lengths_mm"].push_back(intersection.pathLength());
          }
        }
        report["sensitive_intersections"].push_back(row);
      }
      using Collector=Acts::SurfaceCollector<>;
      using Propagator=Acts::Propagator<Acts::EigenStepper<>,Acts::Navigator>;
      auto field=std::make_shared<Acts::ConstantBField>(Acts::Vector3::Zero());
      Propagator propagator{Acts::EigenStepper<>(field),Acts::Navigator(cfg)};
      Acts::MagneticFieldContext mctx;
      const double entryRadius=world->volumeBounds().values()[0];
      auto propagateFrom=[&](const std::string& name,const Acts::Vector3& start) {
        auto seed=Acts::BoundTrackParameters::createCurvilinear(Acts::Vector4(start.x(),start.y(),start.z(),0.),
          Acts::Vector3::UnitX(),-1.,std::nullopt,Acts::ParticleHypothesis::electron());
        Propagator::Options<Acts::ActorList<Collector,Acts::EndOfWorldReached>> options(context,mctx);
        options.maxSteps=1000;options.pathLimit=1000.;
        auto result=propagator.propagate(seed,options);
        Json test={{"name",name},{"start_mm",vector(start)},{"ok",result.ok()},{"hits",Json::array()}};
        if(result.ok()) {
          test["path_length_mm"]=result.value().pathLength;
          for(const auto& hit:result.value().template get<Collector::result_type>().collected)
            for(int i=0;i<7;++i)if(hit.surface==surfaces[i])test["hits"].push_back(i);
          if(result.value().endParameters)test["end_mm"]=vector(result.value().endParameters->position(context));
        } else test["error"]=result.error().message();
        report["propagation"].push_back(test);
      };
      propagateFrom("inside_annulus",{entryRadius+.001,0.,0.});
      propagateFrom("before_first_plane",{249.,0.,0.});
      propagateFrom("outside_inner_boundary",{240.,0.,0.});
      propagateFrom("transformed_gun",{-250.,0.,0.});
      // Use the actual inner world boundary as an entry target, never a fake detector.
      const Acts::Surface* entrySurface=nullptr;
      for(const auto& boundary:world->boundarySurfaces()) {
        const auto& surface=boundary->surfaceRepresentation();
        const auto* bounds=dynamic_cast<const Acts::CylinderBounds*>(&surface.bounds());
        if(bounds && std::abs(bounds->values()[0]-entryRadius)<1e-8)entrySurface=&surface;
      }
      if(!entrySurface)throw std::runtime_error("No actual inner world Cylinder boundary");
      using EntryPropagator=Acts::Propagator<Acts::EigenStepper<>>;
      EntryPropagator entryPropagator{Acts::EigenStepper<>(field)};
      auto outsideSeed=Acts::BoundTrackParameters::createCurvilinear(Acts::Vector4(240.,0.,0.,0.),
        Acts::Vector3::UnitX(),-1.,std::nullopt,Acts::ParticleHypothesis::electron());
      EntryPropagator::Options<> entryOptions(context,mctx);entryOptions.pathLimit=100.;entryOptions.maxSteps=1000;
      auto entry=entryPropagator.propagate(outsideSeed,*entrySurface,entryOptions);
      report["entry_transport"]={{"ok",entry.ok()},{"target_geometry_id",entrySurface->geometryId().value()},
        {"material_aware",false},{"start_mm",{240.,0.,0.}}};
      if(entry.ok() && entry.value().endParameters) {
        const Acts::Vector3 position=entry.value().endParameters->position(context);
        report["entry_transport"]["end_mm"]=vector(position);
        report["entry_transport"]["path_length_mm"]=entry.value().pathLength;
        propagateFrom("on_actual_entry_boundary",position);
        // A documented 1 micron interior seed margin is a numerical convention,
        // not an added volume or altered tracking boundary.
        propagateFrom("entry_with_interior_margin",position+Acts::Vector3(.001,0.,0.));
      } else if(!entry.ok())report["entry_transport"]["error"]=entry.error().message();
      // Straight stepping uses real Navigator targets, including volume boundaries.
      // Starts just inside the annular world; outside-world entry is tested separately.
      const double rmin=world->volumeBounds().values()[0];
      for(const auto& ray : std::vector<std::pair<std::string,Acts::Vector3>>{
          {"central",{rmin+1e-3,0.,0.}}, {"off_axis",{rmin+1e-3,20.,15.}},
          {"z_miss",{rmin+1e-3,0.,51.5}}, {"opposite_phi",{-rmin-1e-3,0.,0.}},
          {"outside_world",{rmin-10.,0.,0.}}, {"transformed_gun",{-250.,0.,0.}}}) {
        Acts::Vector3 pos=ray.second,dir=ray.first=="opposite_phi"?Acts::Vector3(-1.,0.,0.):Acts::Vector3(1.,0.,0.);
        Acts::Navigator::Options opts(context);auto state=nav.makeState(opts);
        Json test={{"name",ray.first},{"start",vector(pos)},{"hits",Json::array()},{"steps",Json::array()}};
        const auto init=nav.initialize(state,pos,dir,Acts::Direction::Forward());test["initialized"]=init.ok();
        for(int step=0;init.ok() && step<200;++step) {
          const auto target=nav.nextTarget(state,pos,dir);if(target.isNone()) {test["terminated"]=true;break;}
          if(!std::isfinite(target.intersection().pathLength()) || target.intersection().pathLength()<-1e-5)throw std::runtime_error("Invalid Navigator forward target");
          pos=target.intersection().position();const auto& surface=target.surface();
          test["steps"].push_back({{"position",vector(pos)},{"geometry_id",surface.geometryId().value()}});
          for(int i=0;i<7;++i)if(&surface==surfaces[i])test["hits"].push_back(i);
          nav.handleSurfaceReached(state,pos,dir,surface);
        }
        test["end_of_world"]=nav.endOfWorldReached(state);report["navigation"].push_back(test);
      }
    }
    bool passed=true;for(const auto& row:report["planes"])passed=passed && bool(row["equivalent"]);
    report["passed"]=passed;
    report["status"]=provider=="official-elements"?"Component surface conversion only; identifiers remain unclosed; NOT a TrackingGeometry provider":"Geometry probe only; no numerical fitter or navigation replacement";
    writeExclusive(argv[2],report);return passed?0:3;
  } catch(const std::exception& e) {
    report["passed"]=false;report["error"]=e.what();
    try {writeExclusive(argv[2],report);}catch(...){}
    std::cerr<<e.what()<<'\n';return 1;
  }
}
