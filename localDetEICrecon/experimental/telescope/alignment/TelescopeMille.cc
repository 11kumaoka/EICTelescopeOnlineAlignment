// Opt-in native ACTS-to-Mille validation processor. No Geometry correction/activation.
#include "NativeMilleModel.h"
#include <JANA/JApplication.h>
#include <JANA/JEventProcessor.h>
#include <services/geometry/acts/ACTSGeo_service.h>
#include <algorithms/geo.h>
#include <ActsExamples/EventData/IndexSourceLink.hpp>
#include <ActsExamples/EventData/Track.hpp>
#include <edm4eic/Measurement2DCollection.h>
#include <Mille/MilleFactory.h>
#include <Mille/MilleRecord.h>
#include <nlohmann/json.hpp>
#include <filesystem>
#include <fstream>
#include <fcntl.h>
#include <unistd.h>
#include <set>
#include <map>
#include <mutex>
namespace eicrecon::telescope {
namespace {
template<class Derived> nlohmann::json matrix(const Eigen::MatrixBase<Derived>& m) {
  auto result=nlohmann::json::array();
  for(int i=0;i<m.rows();++i) {auto row=nlohmann::json::array();for(int j=0;j<m.cols();++j)row.push_back(m(i,j));result.push_back(row);}
  return result;
}
void reserve(const std::string& name) {
  const int fd=::open(name.c_str(),O_WRONLY|O_CREAT|O_EXCL,0644);
  if(fd<0)throw std::runtime_error("Mille prototype output must be new: "+name);::close(fd);
}
void save(const std::string& name,const nlohmann::json& report) {
  const int fd=::open(name.c_str(),O_WRONLY|O_CREAT|O_EXCL,0644);
  if(fd<0)throw std::runtime_error("Mille report output must be new");
  const auto data=report.dump(2)+"\n";std::size_t offset=0;
  while(offset<data.size()) {const auto n=::write(fd,data.data()+offset,data.size()-offset);if(n<=0){::close(fd);throw std::runtime_error("Mille report write failed");}offset+=n;}
  ::close(fd);
}
}
class TelescopeMilleProcessor : public JEventProcessor {
  std::string output,reportPath,configPath,gatePath;
  nlohmann::json config,gate;
  bool multipleEvents=false;
  std::unique_ptr<Mille::MilleRecord> record;
  nlohmann::json events=nlohmann::json::array();
  std::size_t processed=0,rejected=0;
  std::mutex mutex;
public:
  void Init() override {
    auto* app=GetApplication();
    app->SetDefaultParameter("TelescopeMille:multipleEvents",multipleEvents,"Write one native Mille record per accepted event and aggregate diagnostics");
    app->SetDefaultParameter("TelescopeMille:output",output,"New official Mille Binary; experimental single-track probe");
    app->SetDefaultParameter("TelescopeMille:report",reportPath,"New native equation JSON audit");
    app->SetDefaultParameter("TelescopeMille:config",configPath,"Explicit SiMAPS sensor identities and selected physical Global Y/rotation label map JSON");
    app->SetDefaultParameter("TelescopeMille:derivativeGate",gatePath,"Successful derivative gate JSON from this build/fixture");
    for(const auto& path:{output,reportPath}) if(path.empty() || std::filesystem::exists(path))
      throw std::runtime_error("Mille probe requires new explicit output paths");
    config=nlohmann::json::parse(std::ifstream(configPath));gate=nlohmann::json::parse(std::ifstream(gatePath));
    if(!gate.at("passed").get<bool>() || gate.at("model_sha256")!=TELESCOPE_MILLE_MODEL_SHA)throw std::runtime_error("Derivative gate failed");
    if(config.at("scope")!="experimental_zero_field_material_free")
      throw std::runtime_error("Unsupported experimental alignment scope");
    const bool explicitSensors=config.contains("aligned_sensors");
    std::map<int,nlohmann::json> selectedSensors;
    std::map<int,std::set<std::string>> selectedComponents;
    if(explicitSensors) {
      if(!config.at("aligned_sensors").is_array() || config.at("aligned_sensors").empty() ||
         config.at("correction_pivot")!="sensitive_surface_center" ||
         config.at("rotation_composition")!="left_global_exp" ||
         config.at("translation_convention")!="world_translation" ||
         config.at("fixed_components")!=nlohmann::json::array({"global_x","global_z"}) ||
         config.at("containment").at("module_envelope")!="preserve_clearance_air_growth" ||
         config.at("containment").at("layer_envelope")!="preserve_clearance_air_growth" ||
         config.at("containment").at("sensitive_corners")!=8)
        throw std::runtime_error("Unsupported explicit sensor correction or containment convention");
      std::set<uint64_t> volumes,geometries;
      std::set<int> partition;
      for(const auto& sensor:config.at("aligned_sensors")) {
        if(!sensor.at("layer").is_number_integer() || !sensor.at("volume_id").is_number_unsigned() ||
           !sensor.at("geometry_id").is_number_unsigned())
          throw std::runtime_error("Explicit sensor identities must use integer JSON values");
        const int layer=sensor.at("layer");
        const auto volume=sensor.at("volume_id").get<uint64_t>();
        const auto geometry=sensor.at("geometry_id").get<uint64_t>();
        if(sensor.at("system")!=31 || layer<1 || layer>3 || !volume || !geometry ||
           !selectedSensors.emplace(layer,sensor).second || !volumes.insert(volume).second ||
           !geometries.insert(geometry).second || !partition.insert(layer).second)
          throw std::runtime_error("Invalid or duplicate explicit SiMAPS sensor identity");
      }
      if(!config.at("fixed_reference_sensors").is_array())
        throw std::runtime_error("Expected explicit fixed reference sensors");
      for(const auto& reference:config.at("fixed_reference_sensors")) {
        if(!reference.is_number_integer())
          throw std::runtime_error("Fixed reference sensor identifiers must be integers");
        const int layer=reference;
        if(layer<0 || layer>4 || !partition.insert(layer).second)
          throw std::runtime_error("Fixed references overlap selected sensors or each other");
      }
      if(partition!=std::set<int>{0,1,2,3,4})
        throw std::runtime_error("Selected and fixed reference sensors must partition SiMAPS L0-L4");
    }
    std::set<int> labels;
    if(!config.at("parameters").is_array() || config.at("parameters").empty())
      throw std::runtime_error("Expected selected physical alignment parameters");
    for(const auto& p:config.at("parameters")) {
      if(!p.at("layer").is_number_integer() || !p.at("label").is_number_integer())
        throw std::runtime_error("Alignment layers and labels must be integers");
      const int layer=p.at("layer"),label=p.at("label");
      const bool allowedLayer=layer>=1 && layer<=(explicitSensors?3:2);
      const bool translation=p.at("axis")=="global_y" && allowedLayer && label==700002+100*layer &&
          p.value("unit",std::string("mm"))=="mm" &&
          (!explicitSensors || (p.at("unit")=="mm" && p.at("pivot")=="sensitive_surface_center" && p.at("composition")=="world_translation"));
      const bool rotation=((p.at("axis")=="global_rx" && label==700004+100*layer) ||
          (p.at("axis")=="global_ry" && label==700005+100*layer) ||
          (p.at("axis")=="global_rz" && label==700006+100*layer)) && allowedLayer &&
          p.at("unit")=="rad" && p.at("pivot")=="sensitive_surface_center" && p.at("composition")=="left_global_exp";
      if(p.at("system")!=31 || (!translation && !rotation) || !labels.insert(label).second)
        throw std::runtime_error("Unsupported or duplicate selected physical alignment parameter");
      selectedComponents[layer].insert(p.at("axis").get<std::string>());
      if(explicitSensors) {
        const auto sensor=selectedSensors.find(layer);
        if(sensor==selectedSensors.end() || !p.at("volume_id").is_number_unsigned() ||
           !p.at("geometry_id").is_number_unsigned() || p.at("volume_id")!=sensor->second.at("volume_id") ||
           p.at("geometry_id")!=sensor->second.at("geometry_id"))
          throw std::runtime_error("Parameter identity differs from its explicit selected sensor");
      }
    }
    for(const auto& [layer,sensor]:selectedSensors) {
      if(!sensor.at("components").is_array())
        throw std::runtime_error("Expected explicit selected sensor components");
      const auto components=sensor.at("components").get<std::vector<std::string>>();
      const std::set<std::string> declared(components.begin(),components.end());
      if(declared.empty() || declared.size()!=components.size() || declared!=selectedComponents[layer])
        throw std::runtime_error("Selected sensor components differ from parameter declarations");
    }
  }
  void Process(const std::shared_ptr<const JEvent>& event) override {
    std::lock_guard<std::mutex> guard(mutex);++processed;
    const auto states=event->Get<Acts::ConstVectorMultiTrajectory>("TelescopeActsStates");
    const auto tracks=event->Get<Acts::ConstVectorTrackContainer>("TelescopeActsTracks");
    const auto* measurements=event->GetCollection<edm4eic::Measurement2D>("TelescopeMeasurements");
    if(states.size()!=1 || tracks.size()!=1)throw std::runtime_error("Expected one ACTS container pair");
    ActsExamples::ConstTrackContainer container(std::make_shared<Acts::ConstVectorTrackContainer>(*tracks[0]),
        std::make_shared<Acts::ConstVectorMultiTrajectory>(*states[0]));
    if(multipleEvents && container.size()==0) {
      ++rejected;events.push_back({{"event_number",event->GetEventNumber()},{"accepted",false}});return;
    }
    if(container.size()!=1)throw std::runtime_error("This probe requires exactly one accepted track");
    const auto track=container.getTrack(0);
    if(multipleEvents && (track.nMeasurements()!=5 || track.nOutliers()!=0)) {
      ++rejected;events.push_back({{"event_number",event->GetEventNumber()},{"accepted",false},
        {"reason","Incomplete SiMAPS track or outliers"},{"measurements",track.nMeasurements()},{"outliers",track.nOutliers()}});return;
    }
    if(track.nMeasurements()!=5 || track.nOutliers()!=0)throw std::runtime_error("Expected five accepted SiMAPS measurements");
    auto geometry=GetApplication()->GetService<ACTSGeo_service>()->actsGeoProvider();
    requireDeterministicGeometry(*geometry);
    const auto& context=geometry->getActsGeometryContext();
    std::vector<std::pair<double,Acts::TrackIndexType>> indices;
    for(const auto state:track.trackStatesReversed()) if(state.typeFlags().isMeasurement())
      indices.emplace_back(state.pathLength(),state.index());
    std::sort(indices.begin(),indices.end());
    const auto first=container.trackStateContainer().getTrackState(indices.front().second);
    if(!first.hasSmoothed())throw std::runtime_error("No smoothed reference state");
    Acts::BoundTrackParameters reference(first.referenceSurface().getSharedPtr(),first.smoothed(),
        first.smoothedCovariance(),track.particleHypothesis());
    nlohmann::json report={{"passed",false},{"scope","single native track; zero field; no material; not closure"},
      {"config",config},{"derivative_gate",gatePath},{"model_sha256",TELESCOPE_MILLE_MODEL_SHA},{"acts_chi2",track.chi2()},{"acts_stored_ndf",track.nDoF()},
      {"native_holes",track.nHoles()},{"local_parameters",{"reference_loc0","reference_loc1","phi","theta"}},
      {"reference_geometry_id",first.referenceSurface().geometryId().value()},
      {"raw_measurement_covariance_used",true},{"smoothed_covariance_used_as_measurement_error",false},
      {"seed_prior_written",false},{"geometry_activated",false}};
    const auto refPosition=reference.position(context);const auto refDirection=reference.direction();
    report["reference_position_mm"]={refPosition.x(),refPosition.y(),refPosition.z()};
    report["reference_direction"]={refDirection.x(),refDirection.y(),refDirection.z()};
    report["event_number"]=event->GetEventNumber();report["run_number"]=event->GetRunNumber();report["accepted"]=true;
    struct Row {double value;std::vector<double> local,global;std::vector<int> labels;};
    std::vector<Row> rows;std::set<int> layers;
    for(const auto& [path,index]:indices) {
      const auto state=container.trackStateContainer().getTrackState(index);
      if(!state.hasSmoothed() || !state.hasUncalibratedSourceLink() || state.calibratedSize()!=2)
        throw std::runtime_error("Missing native measurement/smoothed/source-link fields");
      const auto source=state.getUncalibratedSourceLink().get<ActsExamples::IndexSourceLink>();
      if(source.index()>=measurements->size())throw std::runtime_error("Invalid measurement source link");
      const auto measurement=(*measurements)[source.index()];
      if(measurement.getHits().size()!=1)throw std::runtime_error("Ambiguous measurement-to-hit association");
      const auto hit=measurement.getHits()[0];const auto cell=hit.getCellID();
      const int layer=(cell>>11)&63;
      if((cell&255)!=31 || layer<0 || layer>4 || !layers.insert(layer).second)
        throw std::runtime_error("Only unique SiMAPS L0-L4 measurements are validated");
      const auto converter=algorithms::GeoSvc::instance().cellIDPositionConverter();
      const auto* volume=converter->findContext(cell);
      if(!volume)throw std::runtime_error("Missing DD4hep Volume context");
      const auto* surface=geometry->surfaceMap().at(volume->identifier);
      if(surface!=&state.referenceSurface() || measurement.getSurface()!=surface->geometryId().value() || source.geometryId()!=surface->geometryId())
        throw std::runtime_error("Native/EDM/official SurfaceMap association mismatch");
      for(const auto& parameter:config.at("parameters")) if(parameter.at("layer")==layer && parameter.contains("geometry_id")) {
        if(parameter.at("geometry_id")!=surface->geometryId().value() || parameter.at("volume_id")!=volume->identifier)
          throw std::runtime_error("Configured sensor identity differs from actual DD4hep/ACTS surface");
      }
      bool gateSurface=false;
      for(const auto& entry:gate.at("surfaces")) if(entry.at("layer")==layer && entry.at("geometry_id")==surface->geometryId().value() && entry.at("volume_id")==volume->identifier)gateSurface=true;
      if(!gateSurface)throw std::runtime_error("Surface is not covered by the derivative gate");
      const auto projector=state.template projectorSubspaceHelper<2>().projector();
      Acts::Matrix<2,6> expected=Acts::Matrix<2,6>::Zero();expected(0,0)=expected(1,1)=1.;
      if((projector-expected).norm()>1e-14)throw std::runtime_error("Only loc0/loc1 measurements are validated");
      const Acts::Vector2 measured=state.template calibrated<2>();
      const Acts::SquareMatrix2 covariance=state.template calibratedCovariance<2>();
      if((measured-Acts::Vector2(measurement.getLoc().a,measurement.getLoc().b)).norm()>1e-8)
        throw std::runtime_error("Native/EDM calibrated measurement mismatch");
      const auto equation=nativeEquation(*geometry,reference,*surface);
      Eigen::LLT<Acts::SquareMatrix2> factor(covariance);
      if(factor.info()!=Eigen::Success)throw std::runtime_error("Non-positive measurement covariance");
      const Acts::SquareMatrix2 whitening=factor.matrixL().solve(Acts::SquareMatrix2::Identity());
      const Acts::Vector2 residual=measured-equation.prediction;
      const Acts::Vector2 whitened=whitening*residual;
      const Acts::Matrix<2,4> local=whitening*equation.local;
      const Acts::Matrix<2,6> global=whitening*equation.global;
      int label=0;
      for(const auto& parameter:config.at("parameters"))if(parameter.at("layer")==layer)label=parameter.at("label");
      for(int row=0;row<2;++row) {
        Row data;data.value=whitened[row];
        for(int j=0;j<4;++j)data.local.push_back(local(row,j));
        for(const auto& parameter:config.at("parameters")) if(parameter.at("layer")==layer) {
          const int column=parameter.at("axis")=="global_y"?1:(parameter.at("axis")=="global_rx"?3:(parameter.at("axis")=="global_ry"?4:5));
          data.labels.push_back(parameter.at("label"));data.global.push_back(global(row,column));
        }
        rows.push_back(std::move(data));
      }
      const Acts::Vector3 center=surface->center(context);
      report["equations"].push_back({{"layer",layer},{"cell_id",cell},{"volume_id",volume->identifier},
        {"geometry_id",surface->geometryId().value()},{"source_index",source.index()},{"label",label},
        {"measurement",matrix(measured)},{"covariance",matrix(covariance)},
        {"prediction",matrix(equation.prediction)},{"residual",matrix(residual)},
        {"local",matrix(equation.local)},{"global",matrix(equation.global)},{"whitening",matrix(whitening)},
        {"center_mm",{center.x(),center.y(),center.z()}},{"rotation",matrix(surface->localToGlobalTransform(context).linear())}});
    }
    if(rows.size()!=10 || layers.size()!=5)throw std::runtime_error("Incomplete native Mille equations");
    if(!record) {reserve(output);record=Mille::spawnMilleRecord(output,true,false);}
    if(!record)throw std::runtime_error("Cannot create official Mille record");
    const std::vector<unsigned int> localIndices{1,2,3,4};
    for(const auto& row:rows)record->addData(row.value,1.,localIndices,row.local,row.labels,row.global);
    record->writeRecord();record->flushOutputFile();if(!multipleEvents)record.reset();
    report["records"]=1;report["scalar_measurements"]=rows.size();report["double_precision"]=true;
    report["passed"]=true;
    if(multipleEvents)events.push_back(report);else save(reportPath,report);
  }
  void Finish() override {
    if(!multipleEvents)return;
    record.reset();
    save(reportPath,{{"passed",rejected==0},{"processed",processed},{"accepted",processed-rejected},
      {"rejected",rejected},{"records",processed-rejected},{"events",events},
      {"model_sha256",TELESCOPE_MILLE_MODEL_SHA}});
  }
};
}
extern "C" void InitPlugin(JApplication* app) {
  InitJANAPlugin(app);app->Add(new eicrecon::telescope::TelescopeMilleProcessor);
}
