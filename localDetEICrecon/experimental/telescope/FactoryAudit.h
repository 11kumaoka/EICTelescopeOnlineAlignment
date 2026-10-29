// Validation observer only; no fitting or event-data mutation.
#pragma once
#include <JANA/JEventProcessor.h>
#include <services/geometry/acts/ACTSGeo_service.h>
#include <Acts/EventData/TrackContainer.hpp>
#include <ActsExamples/EventData/Track.hpp>
#include <edm4eic/TrackCollection.h>
#include <edm4eic/TrajectoryCollection.h>
#include <nlohmann/json.hpp>
#include <fstream>
#include <filesystem>
#include <fcntl.h>
#include <unistd.h>
#include <mutex>
#include <string>
#include "NavigationAudit.h"
namespace eicrecon::telescope {
class FactoryAudit : public JEventProcessor {
  std::string output;
  std::string inputOrigin="synthetic";
  bool multipleEvents=false;
  bool geometryAwareHoles=false;
  nlohmann::json events=nlohmann::json::array();
  std::mutex mutex;
public:
  void Init() override {
    GetApplication()->SetDefaultParameter("TelescopeAudit:output", output, "New JSON audit output (required)");
    GetApplication()->SetDefaultParameter("TelescopeAudit:multipleEvents",multipleEvents,"Aggregate per-event native track diagnostics; preserve single-event default");
    GetApplication()->SetDefaultParameter("TelescopeAudit:inputOrigin",inputOrigin,"Input provenance: synthetic or geant4; reconstruction remains material-free");
    GetApplication()->SetDefaultParameter("TelescopeAudit:geometryAwareHoles",geometryAwareHoles,"Validate native holes against actual sensitive bounds and official ACTS navigation");
    if(output.empty() || std::filesystem::exists(output)) throw std::runtime_error("Audit output must be a new path");
    if(inputOrigin!="synthetic" && inputOrigin!="geant4") throw std::runtime_error("Unsupported TelescopeAudit input origin");
  }
  void Process(const std::shared_ptr<const JEvent>& event) override {
    std::lock_guard<std::mutex> guard(mutex);
    const auto* hits=event->GetCollection<edm4eic::TrackerHit>("TelescopeHits");
    const auto* measurements=event->GetCollection<edm4eic::Measurement2D>("TelescopeMeasurements");
    const auto* seeds=event->GetCollection<edm4eic::TrackSeed>("TelescopeSeeds");
    const auto* tracks=event->GetCollection<edm4eic::Track>("TelescopeTracks");
    const auto* trajectories=event->GetCollection<edm4eic::Trajectory>("TelescopeTrajectories");
    auto states=event->Get<Acts::ConstVectorMultiTrajectory>("TelescopeActsStates");
    auto containers=event->Get<Acts::ConstVectorTrackContainer>("TelescopeActsTracks");
    if(states.size()!=1 || containers.size()!=1) throw std::runtime_error("Expected one ACTS container pair");
    ActsExamples::ConstTrackContainer native(
        std::make_shared<Acts::ConstVectorTrackContainer>(*containers.front()),
        std::make_shared<Acts::ConstVectorMultiTrajectory>(*states.front()));
    auto provider=GetApplication()->GetService<ACTSGeo_service>()->actsGeoProvider();
    const auto& context=provider->getActsGeometryContext();
    nlohmann::json report={{"synthetic_input",inputOrigin=="synthetic"},{"material_free",true},
      {"hits",hits->size()},{"measurements",measurements->size()},{"seeds",seeds->size()},
      {"native_tracks",native.size()},{"edm_tracks",tracks->size()},{"edm_trajectories",trajectories->size()}};
    if(inputOrigin=="geant4") {
      report["input_origin"]="geant4_digitized";
      report["material_free_scope"]="ACTS reconstruction model";
    }
    report["event_number"]=event->GetEventNumber();
    report["run_number"]=event->GetRunNumber();
    if(multipleEvents && native.size()==0 && tracks->empty()) {
      report["accepted"]=false;events.push_back(report);return;
    }
    report["accepted"]=true;
    if(hits->size()!=5 || measurements->size()!=5 || seeds->size()!=1 || native.size()!=1 || tracks->size()!=1)
      throw std::runtime_error("Unexpected factory collection counts");
    const auto seedPosition=(*seeds)[0].getPerigee();
    const Acts::Vector3 seedPoint(seedPosition.x,seedPosition.y,seedPosition.z);
    if(!provider->trackingGeometry()->lowestTrackingVolume(context,seedPoint))
      throw std::runtime_error("Seed reference outside the real TrackingGeometry");
    auto cache=provider->getFieldProvider()->makeCache(provider->getActsMagneticFieldContext());
    const auto field=provider->getFieldProvider()->getField(seedPoint,cache);
    if(!field.ok() || field.value().norm()>1e-15 || provider->surfaceMap().size()!=7)
      throw std::runtime_error("Unexpected field or SurfaceMap");
    for(const auto& [id,surface]:provider->surfaceMap())
      if(surface->surfaceMaterial()) throw std::runtime_error("Unexpected ACTS material");
    report["seed_reference_mm"]={seedPoint.x(),seedPoint.y(),seedPoint.z()};
    report["seed_inside_tracking_geometry"]=true;
    report["field_T"]={field.value().x(),field.value().y(),field.value().z()};
    const auto track=native.getTrack(0);
    report["chi2"]=track.chi2(); report["stored_ndf"]=track.nDoF();
    report["outliers"]=track.nOutliers(); report["holes"]=track.nHoles();
    const bool invalidStates=track.nMeasurements()!=5 || track.nOutliers()!=0 || (!geometryAwareHoles && track.nHoles()!=2);
    if(multipleEvents && invalidStates) {
      report["accepted"]=false;report["reason"]="Incomplete SiMAPS track or unexpected holes/outliers";
      events.push_back(report);return;
    }
    if(invalidStates) throw std::runtime_error("Unexpected CKF states");
    if(geometryAwareHoles) report["navigation_audit"]=auditSensitiveNavigation(track,*provider,*measurements);
    for(const auto& measurement:*measurements) {
      const auto hit=measurement.getHits()[0];
      const auto converter=algorithms::GeoSvc::instance().cellIDPositionConverter();
      const auto* volume=converter->findContext(hit.getCellID());
      const auto* surface=provider->surfaceMap().at(volume->identifier);
      const auto point=hit.getPosition();
      const auto local=surface->globalToLocal(context,{point.x,point.y,point.z},Acts::Vector3::UnitX(),1e-4).value();
      // Measurement2D stores binary32 coordinates. Check the official converter's
      // exact serialization contract, rather than bounding float rounding in mm.
      if(surface->geometryId().value()!=measurement.getSurface() || !local.allFinite() ||
          static_cast<float>(local[0])!=measurement.getLoc().a ||
          static_cast<float>(local[1])!=measurement.getLoc().b)
        throw std::runtime_error("Measurement surface/local mismatch");
      // Independently retain a strict geometric check before EDM rounding. The
      // DD4hep channel center must lie on its real ACTS surface within 1e-6 mm.
      const auto ddPoint=converter->position(hit.getCellID());
      const Acts::Vector3 exactPoint(ddPoint.x()/dd4hep::mm,ddPoint.y()/dd4hep::mm,ddPoint.z()/dd4hep::mm);
      const auto exactLocal=surface->globalToLocal(context,exactPoint,Acts::Vector3::UnitX(),1e-6).value();
      if((surface->localToGlobal(context,exactLocal,Acts::Vector3::UnitX())-exactPoint).norm()>1e-6)
        throw std::runtime_error("DD4hep channel center/ACTS surface mismatch before EDM rounding");
      const auto cov=measurement.getCovariance();
      if(std::abs(cov.xx-hit.getPositionError().yy)>1e-10 || std::abs(cov.yy-hit.getPositionError().xx)>1e-10 || std::abs(cov.xy)>1e-10)
        throw std::runtime_error("Measurement covariance mismatch");
      report["measurement_details"].push_back({{"cell_id",hit.getCellID()},{"volume_id",volume->identifier},
        {"geometry_id",measurement.getSurface()},{"position_mm",{point.x,point.y,point.z}},{"loc_mm",{measurement.getLoc().a,measurement.getLoc().b}},
        {"covariance_mm2",{cov.xx,cov.yy,cov.xy}}});
      if(geometryAwareHoles)
        report["measurement_details"].back()["serialization_audit"]=auditMeasurementSerialization(hit,measurement,*provider);
    }
    for(const auto state:track.trackStatesReversed()) {
      const auto flags=state.typeFlags();
      if(flags.isHole()) report["hole_geometry_ids"].push_back(state.referenceSurface().geometryId().value());
      if(!flags.isMeasurement()) continue;
      if(!state.hasSmoothed() || !state.smoothed().allFinite() || !state.smoothedCovariance().allFinite())
        throw std::runtime_error("Invalid smoothed state");
      const auto parameters=state.smoothed();
      const Acts::Vector2 local=parameters.template head<2>();
      const auto direction=Acts::makeDirectionFromPhiTheta(parameters[Acts::eBoundPhi],parameters[Acts::eBoundTheta]);
      const auto position=state.referenceSurface().localToGlobal(context,local,direction);
      const auto residual=state.template calibrated<2>()-local;
      report["smoothed_states"].push_back({{"geometry_id",state.referenceSurface().geometryId().value()},
        {"position_mm",{position.x(),position.y(),position.z()}},{"loc_mm",{local[0],local[1]}},
        {"residual_mm",{residual[0],residual[1]}},
        {"local_covariance_diagonal_mm2",{state.smoothedCovariance()(0,0),state.smoothedCovariance()(1,1)}}});
    }
    const auto edm=(*tracks)[0];
    const auto position=edm.getPosition();const auto momentum=edm.getMomentum();
    report["edm"]={{"position_mm",{position.x,position.y,position.z}},
       {"momentum_GeV",{momentum.x,momentum.y,momentum.z}}, {"pdg",edm.getPdg()},
       {"charge",edm.getCharge()},{"chi2",edm.getChi2()},{"ndf",edm.getNdf()},
       {"measurement_links",edm.getMeasurements().size()}};
    if(edm.getPdg()!=11 || edm.getCharge()!=-1 || edm.getMeasurements().size()!=5 ||
       std::abs(edm.getChi2()-track.chi2())>std::max(1e-4,2e-6*std::abs(track.chi2())) || edm.getNdf()!=track.nDoF())
      throw std::runtime_error("ACTS-to-EDM conversion mismatch");
    // Exercise the real converter with unequal variances: the benchmark's
    // equal pixel widths alone cannot detect an axis-exchange defect.
    edm4eic::TrackerHitCollection controlHits;
    edm4eic::Measurement2DCollection controlMeasurements;
    const uint64_t controlVolumes[] = {31ULL|(1ULL<<22)|(1ULL<<28),
        100ULL|(5ULL<<11)|(1ULL<<22)|(1ULL<<28)};
    for(const auto id:controlVolumes) {
      auto hit=controlHits.create(); hit.setCellID(id);
      const auto p=algorithms::GeoSvc::instance().cellIDPositionConverter()->position(id);
      hit.setPosition({float(p.x()/dd4hep::mm),float(p.y()/dd4hep::mm),float(p.z()/dd4hep::mm)});
      hit.setPositionError({2e-4f,8e-4f,0.f});
    }
    TrackerMeasurementFromHits control("TelescopeCovarianceControl"); control.init();
    control.process({&controlHits},{&controlMeasurements});
    if(controlMeasurements.size()!=2) throw std::runtime_error("Covariance control conversion failed");
    const auto xz=controlMeasurements[0].getCovariance(), xy=controlMeasurements[1].getCovariance();
    if(std::abs(xz.xx-8e-4)>1e-10 || std::abs(xz.yy-2e-4)>1e-10 ||
       std::abs(xy.xx-2e-4)>1e-10 || std::abs(xy.yy-8e-4)>1e-10)
      throw std::runtime_error("Anisotropic XZ/unchanged normal XY covariance control failed");
    report["anisotropic_xz_covariance_control_passed"]=true;
    report["unchanged_xy_covariance_control_passed"]=true;
    report["passed"]=true;
    if(multipleEvents) {events.push_back(report);return;}
    writeReport(report);
  }
  void Finish() override {
    if(!multipleEvents)return;
    std::size_t accepted=0;for(const auto& e:events)if(e.at("accepted").get<bool>())++accepted;
    writeReport({{"passed",accepted==events.size()},{"processed",events.size()},
      {"accepted",accepted},{"rejected",events.size()-accepted},{"events",events}});
  }
private:
  void writeReport(const nlohmann::json& report) {
    // Always preserve an existing audit output.
    const int fd=::open(output.c_str(),O_WRONLY|O_CREAT|O_EXCL,0644);
    if(fd<0) throw std::runtime_error("Audit output already exists or cannot be created");
    const std::string data=report.dump(2)+"\n";
    std::size_t offset=0;
    while(offset<data.size()) {
      const auto count=::write(fd,data.data()+offset,data.size()-offset);
      if(count<=0) { ::close(fd); throw std::runtime_error("Audit write failed"); }
      offset+=count;
    }
    ::close(fd);
  }
};
}
