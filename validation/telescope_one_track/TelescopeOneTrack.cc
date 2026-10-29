// One synthetic, material-free track. No production algorithms are modified.
#include <Acts/ActsVersion.hpp>
#include <Acts/EventData/TrackParameters.hpp>
#include <Acts/TrackFinding/TrackStateCreator.hpp>
#include <Acts/TrackFinding/MeasurementSelector.hpp>
#include <Acts/TrackFitting/GainMatrixUpdater.hpp>
#include <Acts/Utilities/TrackHelpers.hpp>
#include <ActsExamples/EventData/Measurement.hpp>
#include <ActsExamples/EventData/MeasurementCalibration.hpp>
#include <ActsExamples/EventData/IndexSourceLink.hpp>
#include <ActsExamples/EventData/Track.hpp>
#include <DD4hep/Detector.h>
#include <DD4hep/Segmentations.h>
#include <DDRec/CellIDPositionConverter.h>
#include <Evaluator/DD4hepUnits.h>
#include <algorithms/geo.h>
#include <algorithms/interfaces/ActsSvc.h>
#include <algorithms/tracking/ActsGeometryProvider.h>
#include <algorithms/tracking/CKFTracking.h>
#include <algorithms/tracking/TrackerMeasurementFromHits.h>
#include <edm4eic/TrackerHitCollection.h>
#include <spdlog/sinks/stdout_color_sinks.h>
#include <nlohmann/json.hpp>
#include <fcntl.h>
#include <unistd.h>
#include <filesystem>
#include <iostream>
#include <set>

using Json=nlohmann::json;
Json vec(const Acts::Vector3& p) {return {p.x(),p.y(),p.z()};}
void save(const char* name,const Json& report) {
  const int fd=::open(name,O_WRONLY|O_CREAT|O_EXCL,0644);
  if(fd<0)throw std::runtime_error("Output must be a new file");
  const std::string data=report.dump(2)+"\n";std::size_t offset=0;
  while(offset<data.size()) {const auto n=::write(fd,data.data()+offset,data.size()-offset);if(n<=0){::close(fd);throw std::runtime_error("Write failed");}offset+=n;}
  ::close(fd);
}
int main(int argc,char** argv) {
  if(argc!=3) {std::cerr<<"Usage: TelescopeOneTrack BARREL_XML NEW_OUTPUT_JSON\n";return 64;}
  if(std::filesystem::exists(argv[2])) {std::cerr<<"Refusing existing output\n";return 64;}
  Json report={{"passed",false},{"material_free",true},{"synthetic_input",true},
    {"simulation_or_old_rawhits_used",false},{"alignment_performed",false},
    {"hit_error_convention","xx/yy store sensor-X/Z readout variances"},
    {"fit_measurement_layers",{0,1,2,3,4}},{"excluded_measurement_layers",{5,6}}};
  try {
    auto& detector=dd4hep::Detector::getInstance();detector.fromXML(argv[1]);
    detector.apply("DD4hepVolumeManager",0,nullptr);
    algorithms::GeoSvc::instance().init(&detector);
    const auto converter=algorithms::GeoSvc::instance().cellIDPositionConverter();
    auto provider=std::make_shared<ActsGeometryProvider>();
    auto log=spdlog::stdout_color_mt("one_track_geometry");log->set_level(spdlog::level::info);
    provider->initialize(&detector,"",log,log);
    algorithms::ActsSvc::instance().init(provider);
    // Verify the installed upper-level converter's documented ePIC-only init
    // constraint; never bypass init or add a fictitious B0 detector/constant.
    try {
      eicrecon::TrackerMeasurementFromHits officialConversion("converter_reuse_check");
      officialConversion.init();
      throw std::runtime_error("Unexpected converter init success; reassess the proposed adaptation");
    } catch(const std::runtime_error& error) {
      const std::string message=error.what();
      if(message.find("B0Tracker_Station_1_ID")==std::string::npos)throw;
      report["official_measurement_init_failure"]=message;
    }
    const auto& gctx=provider->getActsGeometryContext();
    const auto& mctx=provider->getActsMagneticFieldContext();
    const auto& cctx=provider->getActsCalibrationContext();
    if(provider->surfaceMap().size()!=7)throw std::runtime_error("Expected seven official sensitive surfaces");
    for(const auto& [id,surface]:provider->surfaceMap())if(surface->surfaceMaterial())throw std::runtime_error("Unexpected surface material");

    edm4eic::TrackerHitCollection hits;
    ActsExamples::MeasurementContainer measurements;
    ActsExamples::GeometryIdMultiset<ActsExamples::IndexSourceLink> links;
    std::vector<Acts::Vector3> points;
    std::set<uint64_t> volumeIds,geometryIds;
    // Generate fresh telescope pixel hits by quantizing a fixed straight ray.
    // Only SiMAPS X/Z is a validated two-coordinate readout in this fixture.
    for(int layer=0;layer<5;++layer) {
      const uint64_t base=31ULL|(uint64_t(layer)<<11)|(1ULL<<22)|(1ULL<<28);
      const auto* context=converter->findContext(base);
      if(!context)throw std::runtime_error("Missing barrel Volume ID");
      const Acts::Vector3 ray(250.+50.*layer,2.+.003*50.*layer,-3.-.002*50.*layer);
      const dd4hep::Position global(ray.x()*dd4hep::mm,ray.y()*dd4hep::mm,ray.z()*dd4hep::mm);
      const auto local=context->worldToLocal(global);
      const auto cell=detector.readout("SiMAPSHits").segmentation().cellID(local,global,base);
      const auto* associated=converter->findContext(cell);
      if(!associated || associated->identifier!=context->identifier)throw std::runtime_error("Pixel CellID/Volume mismatch");
      const auto* surface=provider->surfaceMap().at(associated->identifier);
      if(!volumeIds.insert(associated->identifier).second || !geometryIds.insert(surface->geometryId().value()).second)
        throw std::runtime_error("Nonunique measurement surface");
      const auto center=converter->position(cell);const auto dimensions=converter->cellDimensions(cell);
      Acts::Vector3 position(center.x()/dd4hep::mm,center.y()/dd4hep::mm,center.z()/dd4hep::mm);
      const double varianceX=std::pow(dimensions.at(0)/dd4hep::mm,2)/12.;
      if(dimensions.size()!=2)throw std::runtime_error("Expected X/Z readout dimensions");
      const double varianceZ=std::pow(dimensions.at(1)/dd4hep::mm,2)/12.;
      if(!(varianceX>0. && varianceZ>0.))throw std::runtime_error("Invalid SiMAPS pixel variances");
      auto hit=hits.create();hit.setCellID(cell);hit.setPosition({float(position.x()),float(position.y()),float(position.z())});
      // EICrecon packs X/Z readout variances as xx/yy, not native XYZ.
      hit.setPositionError({float(varianceX),float(varianceZ),0.f});
      const auto hp=hit.getPosition();position={hp.x,hp.y,hp.z};points.push_back(position);
      const auto measured=surface->globalToLocal(gctx,position,Acts::Vector3::UnitX(),1e-4);
      if(!measured.ok() || !surface->bounds().inside(measured.value()))throw std::runtime_error("Hit is not on bounded sensitive surface");
      const auto transform=surface->localToGlobalTransform(gctx);
      auto sensorAxis=[&](int axis) {
        dd4hep::Position offset(0,0,0);if(axis==0)offset.SetX(dd4hep::mm);else offset.SetZ(dd4hep::mm);
        const auto p=associated->localToWorld(offset),o=associated->localToWorld(dd4hep::Position(0,0,0));
        return Acts::Vector3((p.x()-o.x())/dd4hep::mm,(p.y()-o.y())/dd4hep::mm,(p.z()-o.z())/dd4hep::mm);
      };
      Acts::SquareMatrix2 projection;
      projection<<transform.linear().col(0).dot(sensorAxis(0)),transform.linear().col(0).dot(sensorAxis(2)),
                  transform.linear().col(1).dot(sensorAxis(0)),transform.linear().col(1).dot(sensorAxis(2));
      Acts::SquareMatrix2 sensorCov=Acts::SquareMatrix2::Zero();sensorCov.diagonal()<<hit.getPositionError().xx,hit.getPositionError().yy;
      const Acts::SquareMatrix2 covariance=projection*sensorCov*projection.transpose();
      const auto measurement=measurements.emplaceMeasurement<2>(surface->geometryId(),
        std::array<std::uint8_t,2>{Acts::eBoundLoc0,Acts::eBoundLoc1},measured.value(),covariance);
      links.emplace(surface->geometryId(),measurement.index());
      // Independent native-axis check; anisotropic correlated covariance is a
      // transform diagnostic only, never an uncertainty rescaling for this fit.
      const Acts::Vector2 expected(-local.z()/dd4hep::mm,local.x()/dd4hep::mm);
      const auto native=associated->worldToLocal(dd4hep::Position(position.x()*dd4hep::mm,position.y()*dd4hep::mm,position.z()*dd4hep::mm));
      const Acts::Vector2 expectedMeasured(-native.z()/dd4hep::mm,native.x()/dd4hep::mm);
      if((measured.value()-expectedMeasured).norm()>1e-10)throw std::runtime_error("Local coordinate convention mismatch");
      Acts::SquareMatrix2 testCov;testCov<<1e-4,3e-5,3e-5,4e-4;
      Acts::SquareMatrix2 expectedTest;expectedTest<<4e-4,-3e-5,-3e-5,1e-4;
      if((projection*testCov*projection.transpose()-expectedTest).norm()>1e-12)throw std::runtime_error("Covariance axis/sign mismatch");
      report["measurements"].push_back({{"layer",layer},{"cell_id",cell},{"volume_id",associated->identifier},
        {"geometry_id",surface->geometryId().value()},{"position_mm",vec(position)},
        {"loc_mm",{measured.value()[0],measured.value()[1]}},
        {"covariance_mm2",{{covariance(0,0),covariance(0,1)},{covariance(1,0),covariance(1,1)}}},
        {"readout_pitch_X_Z_mm",{dimensions[0]/dd4hep::mm,dimensions[1]/dd4hep::mm}},
        {"packed_input_variances_X_Z_mm2",{hit.getPositionError().xx,hit.getPositionError().yy}},
        {"sensor_X_Z_to_acts_u_v",{{projection(0,0),projection(0,1)},{projection(1,0),projection(1,1)}}},
        {"ray_local_mm",{expected[0],expected[1]}},{"covariance_probe_passed",true}});
    }
    const Acts::Vector3 direction=(points.back()-points.front()).normalized();
    const Acts::Vector3 seedPosition=points.front()+((249.-points.front().x())/direction.x())*direction;
    if(!provider->trackingGeometry()->lowestTrackingVolume(gctx,seedPosition))throw std::runtime_error("Seed outside TrackingGeometry");
    auto fieldCache=provider->getFieldProvider()->makeCache(mctx);
    const auto field=provider->getFieldProvider()->getField(seedPosition,fieldCache);
    if(!field.ok() || field.value().norm()>1e-15)throw std::runtime_error("Expected zero magnetic field");
    report["field_T"]=vec(field.value());
    Acts::BoundMatrix seedCov=Acts::BoundMatrix::Zero();seedCov.diagonal()<<1.,1.,1e-3,1e-3,1e-4,1.;
    auto seed=Acts::BoundTrackParameters::createCurvilinear(
      Acts::Vector4(seedPosition.x(),seedPosition.y(),seedPosition.z(),0.),direction,-1.,seedCov,Acts::ParticleHypothesis::electron());
    report["seed"]={{"position_mm",vec(seedPosition)},{"direction",vec(direction)},
      {"derived_from_pixel_hits",true},{"q_over_p_per_GeV",-1.},{"covariance_diagonal",{1.,1.,1e-3,1e-3,1e-4,1.}}};
    ActsExamples::PassThroughCalibrator calibrator;
    ActsExamples::MeasurementCalibratorAdapter calibration(calibrator,measurements);
    ActsExamples::IndexSourceLinkAccessor accessor;accessor.container=&links;
    Acts::MeasurementSelector::Config selection{{Acts::GeometryIdentifier(),{.etaBins={},.chi2CutOff={15.},.numMeasurementsCutOff={10}}}};
    Acts::MeasurementSelector selector(selection);
    Acts::GainMatrixUpdater updater;
    using Creator=Acts::TrackStateCreator<ActsExamples::IndexSourceLinkAccessor::Iterator,ActsExamples::TrackContainer>;
    Creator creator;creator.sourceLinkAccessor.connect<&ActsExamples::IndexSourceLinkAccessor::range>(&accessor);
    creator.calibrator.connect<&ActsExamples::MeasurementCalibratorAdapter::calibrate>(&calibration);
    creator.measurementSelector.connect<&Acts::MeasurementSelector::select<Acts::VectorMultiTrajectory>>(&selector);
    Acts::CombinatorialKalmanFilterExtensions<ActsExamples::TrackContainer> extensions;
    extensions.updater.connect<&Acts::GainMatrixUpdater::operator()<ActsExamples::TrackContainer::TrackStateContainerBackend>>(&updater);
    extensions.createTrackStates.connect<&Creator::createTrackStates>(&creator);
    auto logger=Acts::getDefaultLogger("OneTrack",Acts::Logging::INFO);
    auto finder=eicrecon::CKFTracking::makeCKFTrackingFunction(provider->trackingGeometry(),provider->getFieldProvider(),*logger);
    Acts::PropagatorPlainOptions propagation(gctx,mctx);propagation.maxSteps=10000;
    eicrecon::CKFTracking::TrackFinderOptions options(gctx,mctx,cctx,extensions,propagation);
    auto backend=std::make_shared<Acts::VectorTrackContainer>();auto trajectory=std::make_shared<Acts::VectorMultiTrajectory>();
    ActsExamples::TrackContainer tracks(backend,trajectory);
    const auto result=(*finder)(seed,options,tracks);
    if(!result.ok())throw std::runtime_error("Official CKF failed: "+result.error().message());
    report["candidate_count"]=result.value().size();
    if(result.value().size()!=1)throw std::runtime_error("Expected exactly one candidate");
    auto track=result.value().front();
    const auto smoothed=Acts::smoothTrack(gctx,track,*logger);
    if(!smoothed.ok())throw std::runtime_error("ACTS smoothing failed: "+smoothed.error().message());
    report["track"]={{"n_measurements",track.nMeasurements()},{"n_outliers",track.nOutliers()},
      {"n_holes",track.nHoles()},{"chi2",track.chi2()},{"stored_ndf",track.nDoF()}};
    int states=0;double maxResidual=0.;
    for(const auto& state:track.trackStatesReversed()) {
      if(state.typeFlags().test(Acts::TrackStateFlag::IsHole)) {
        const auto id=state.referenceSurface().geometryId();
        if(id.extra()!=100 && id.extra()!=92)throw std::runtime_error("Unexpected hole on fitted SiMAPS layer");
        report["excluded_readout_holes"].push_back(id.value());
      }
      if(!state.typeFlags().test(Acts::TrackStateFlag::HasMeasurement))continue;
      if(!state.hasSmoothed() || !state.smoothed().allFinite() || !state.smoothedCovariance().allFinite())throw std::runtime_error("Invalid smoothed state");
      if(state.smoothedCovariance()(0,0)<=0. || state.smoothedCovariance()(1,1)<=0. || state.smoothedCovariance().topLeftCorner<2,2>().determinant()<=0.)throw std::runtime_error("Nonpositive local state covariance");
      const auto index=state.getUncalibratedSourceLink().get<ActsExamples::IndexSourceLink>().index();
      const Acts::Vector2 residual=measurements.getMeasurement<2>(index).parameters()-state.smoothed().head<2>();
      const Acts::Vector3 dir=Acts::makeDirectionFromPhiTheta(state.smoothed()[Acts::eBoundPhi],state.smoothed()[Acts::eBoundTheta]);
      const auto position=state.referenceSurface().localToGlobal(gctx,state.smoothed().head<2>(),dir);
      maxResidual=std::max(maxResidual,residual.cwiseAbs().maxCoeff());++states;
      report["states"].push_back({{"measurement_index",index},{"geometry_id",state.referenceSurface().geometryId().value()},
        {"position_mm",vec(position)},{"direction",vec(dir)},{"residual_mm",{residual[0],residual[1]}},
        {"local_covariance_diagonal_mm2",{state.smoothedCovariance()(0,0),state.smoothedCovariance()(1,1)}}});
    }
    if(track.nMeasurements()!=5 || states!=5 || track.nOutliers()!=0 || track.nHoles()!=2 || report["excluded_readout_holes"].size()!=2 || maxResidual>.034641)
      throw std::runtime_error("One-track acceptance failed");
    report["maximum_abs_residual_mm"]=maxResidual;report["passed"]=true;
    save(argv[2],report);return 0;
  } catch(const std::exception& e) {report["error"]=e.what();save(argv[2],report);std::cerr<<e.what()<<'\n';return 1;}
}
