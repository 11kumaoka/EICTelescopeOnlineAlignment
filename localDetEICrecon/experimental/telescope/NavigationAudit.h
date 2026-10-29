// Observer-only checks using the installed ACTS Navigator and real sensitive bounds.
#pragma once
#include <Acts/Propagator/Navigator.hpp>
#include <Acts/Definitions/Units.hpp>
#include <Acts/EventData/TrackParameters.hpp>
#include <ActsExamples/EventData/IndexSourceLink.hpp>
#include <DDRec/CellIDPositionConverter.h>
#include <DD4hep/Segmentations.h>
#include <Evaluator/DD4hepUnits.h>
#include <algorithms/geo.h>
#include <algorithms/tracking/ActsGeometryProvider.h>
#include <edm4eic/Measurement2DCollection.h>
#include <edm4eic/RawTrackerHit.h>
#include <nlohmann/json.hpp>
#include <cmath>
#include <bit>
#include <cstdint>
#include <map>
#include <optional>
#include <set>
#include <stdexcept>
#include <string>
#include <vector>

namespace eicrecon::telescope {
// Verify the two EDM binary32 serializations against their actual geometry.
// The invariant channel coordinates are decoded directly by the segmentation;
// inverse-transforming a rounded global hit is not a channel-identity test.
inline nlohmann::json auditMeasurementSerialization(const edm4eic::TrackerHit& hit,
    const edm4eic::Measurement2D& measurement, const ActsGeometryProvider& provider) {
  auto fail=[](const char* reason) {
    throw std::runtime_error(std::string("Measurement serialization integrity failure: ")+reason);
  };
  auto same=[](float a,float b) {
    return std::bit_cast<std::uint32_t>(a)==std::bit_cast<std::uint32_t>(b);
  };
  const auto converter=algorithms::GeoSvc::instance().cellIDPositionConverter();
  const auto detector=algorithms::GeoSvc::instance().detector();
  const auto cell=hit.getCellID();
  const auto* volume=converter->findContext(cell);
  const auto segmentation=detector->readout("SiMAPSHits").segmentation();
  if(!volume || (cell&255)!=31 || segmentation.type()!="CartesianGridXZ" ||
      !hit.getRawHit().isAvailable() || hit.getRawHit().getCellID()!=cell)
    fail("RawHit/CellID/CartesianGridXZ association mismatch");
  const auto* surface=provider.surfaceMap().at(volume->identifier);
  if(measurement.getHits().size()!=1 ||
      measurement.getHits()[0].getObjectID()!=hit.getObjectID() ||
      measurement.getSurface()!=surface->geometryId().value())
    fail("TrackerHit/Measurement2D/SurfaceMap association mismatch");
  const auto intrinsic=segmentation.position(cell);
  const auto dimensions=converter->cellDimensions(cell);
  const auto world=volume->localToWorld(intrinsic);
  const auto converted=converter->position(cell);
  const Acts::Vector3 canonical(intrinsic.x()/dd4hep::mm,intrinsic.y()/dd4hep::mm,intrinsic.z()/dd4hep::mm);
  const Acts::Vector3 expectedGlobal(world.x()/dd4hep::mm,world.y()/dd4hep::mm,world.z()/dd4hep::mm);
  if(!canonical.allFinite() || !expectedGlobal.allFinite() || dimensions.size()!=2 ||
      !std::isfinite(dimensions[0]) || !std::isfinite(dimensions[1]) ||
      dimensions[0]<=0 || dimensions[1]<=0) fail("Invalid canonical channel coordinates or dimensions");
  const auto position=hit.getPosition();
  const float expectedX=static_cast<float>(expectedGlobal[0]),expectedY=static_cast<float>(expectedGlobal[1]),expectedZ=static_cast<float>(expectedGlobal[2]);
  if(!same(expectedX,static_cast<float>(converted.x()/dd4hep::mm)) ||
      !same(expectedY,static_cast<float>(converted.y()/dd4hep::mm)) ||
      !same(expectedZ,static_cast<float>(converted.z()/dd4hep::mm)) ||
      !same(position.x,expectedX) || !same(position.y,expectedY) || !same(position.z,expectedZ))
    fail("Global binary32 position differs from the canonical DD4hep channel center");
  // Preserve the installed official TrackerHitReconstruction variance formula.
  const auto variance=[](double pitch) {const double resolution=pitch/3.4641016151;return resolution*resolution;};
  const auto errors=hit.getPositionError();
  if(!same(errors.xx,static_cast<float>(variance(dimensions[0]/dd4hep::mm))) ||
      !same(errors.yy,static_cast<float>(variance(dimensions[1]/dd4hep::mm))) || !same(errors.zz,0.f))
    fail("TrackerHit binary32 variance differs from the official pixel formula");
  const auto& context=provider.getActsGeometryContext();
  const auto local=surface->globalToLocal(context,{position.x,position.y,position.z},
      {0,0,0},0.1*Acts::UnitConstants::um);
  if(!local.ok() || !local.value().allFinite()) fail("Rounded global hit cannot be converted by the official surface");
  const float expectedU=static_cast<float>(local.value()[0]),expectedV=static_cast<float>(local.value()[1]);
  if(!same(measurement.getLoc().a,expectedU) || !same(measurement.getLoc().b,expectedV))
    fail("Local binary32 coordinates differ from the official conversion");
  // Repeat the converter's sensor-X/Z to ACTS-u/v covariance projection.
  const auto origin=volume->localToWorld(dd4hep::Position(0,0,0));
  const auto x=volume->localToWorld(dd4hep::Position(dd4hep::mm,0,0));
  const auto z=volume->localToWorld(dd4hep::Position(0,0,dd4hep::mm));
  const Acts::Vector3 gx((x.x()-origin.x())/dd4hep::mm,(x.y()-origin.y())/dd4hep::mm,(x.z()-origin.z())/dd4hep::mm);
  const Acts::Vector3 gz((z.x()-origin.x())/dd4hep::mm,(z.y()-origin.y())/dd4hep::mm,(z.z()-origin.z())/dd4hep::mm);
  const auto rotation=surface->localToGlobalTransform(context).linear().eval();
  Acts::SquareMatrix2 jacobian;
  jacobian<<rotation.col(0).dot(gx),rotation.col(0).dot(gz),rotation.col(1).dot(gx),rotation.col(1).dot(gz);
  Acts::SquareMatrix2 covariance=Acts::SquareMatrix2::Zero();
  covariance(0,0)=errors.xx*Acts::UnitConstants::mm*Acts::UnitConstants::mm;
  covariance(1,1)=errors.yy*Acts::UnitConstants::mm*Acts::UnitConstants::mm;
  covariance=(jacobian*covariance*jacobian.transpose()).eval();
  const auto actual=measurement.getCovariance();
  const float expectedXX=static_cast<float>(covariance(0,0)),expectedYY=static_cast<float>(covariance(1,1)),expectedXY=static_cast<float>(covariance(0,1));
  if(!covariance.allFinite() || !same(actual.xx,expectedXX) || !same(actual.yy,expectedYY) || !same(actual.xy,expectedXY))
    fail("Measurement binary32 covariance differs from the official conversion");
  return {{"passed",true},{"canonical_sensor_local_mm",{canonical[0],canonical[1],canonical[2]}},
    {"canonical_cell_dimensions_mm",{dimensions[0]/dd4hep::mm,dimensions[1]/dd4hep::mm}},
    {"expected_global_float32_mm",{expectedX,expectedY,expectedZ}},
    {"expected_measurement_local_float32_mm",{expectedU,expectedV}},
    {"expected_measurement_covariance_float32_mm2",{expectedXX,expectedYY,expectedXY}}};
}

inline nlohmann::json auditNavigationRay(const ActsGeometryProvider& provider,
    const Acts::Vector3& anchor, const Acts::Vector3& direction, std::uint64_t anchorId,
    const std::set<std::uint64_t>& measured, const std::vector<std::uint64_t>& holes) {
  using Json=nlohmann::json;
  const auto& context=provider.getActsGeometryContext();
  Json report={{"passed",false},{"method","installed Acts::Navigator targets and independent bounded SurfaceMap intersections"},
    {"anchor_geometry_id",anchorId},{"anchor_position_mm",{anchor.x(),anchor.y(),anchor.z()}},
    {"anchor_direction",{direction.x(),direction.y(),direction.z()}},{"measured_geometry_ids",measured},
    {"actual_hole_geometry_ids",holes},{"surface_intersections",Json::array()},{"navigator_steps",Json::array()}};
  auto fail=[&](const char* reason) {
    report["failure"]=reason;
    throw std::runtime_error("Sensitive navigation integrity failure: "+report.dump());
  };
  if(!anchor.allFinite() || !direction.allFinite() || std::abs(direction.norm()-1.)>1e-12 ||
      measured.size()!=5 || !measured.contains(anchorId)) fail("Invalid last-measurement anchor or measured surface set");
  std::map<std::uint64_t,const Acts::Surface*> surfaces;
  for(const auto& [volume,surface]:provider.surfaceMap())
    if(!surfaces.emplace(surface->geometryId().value(),surface).second) fail("Duplicate official sensitive GeometryIdentifier");
  for(const auto id:measured) if(!surfaces.contains(id)) fail("Measured surface absent from official SurfaceMap");
  std::set<std::uint64_t> observed;
  for(const auto id:holes)
    if(!surfaces.contains(id) || measured.contains(id) || !observed.insert(id).second)
      fail("Unknown, measured or duplicate hole surface");
  Acts::Navigator::Config config;config.trackingGeometry=provider.trackingGeometry();
  config.resolveSensitive=true;config.resolveMaterial=false;config.resolvePassive=false;
  Acts::Navigator navigator(config);
  Acts::Navigator::Options options(context);
  auto state=navigator.makeState(options);
  const auto initialized=navigator.initialize(state,anchor,direction,Acts::Direction::Forward());
  if(!initialized.ok()) fail("Official Navigator initialization failed");
  std::set<std::uint64_t> bounded;
  for(const auto& [id,surface]:surfaces) {
    const auto plane=surface->intersect(context,anchor,direction,Acts::BoundaryTolerance::Infinite()).closestForward();
    Json row={{"geometry_id",id},{"forward_plane_intersection",false},{"inside_sensitive_bounds",false},
      {"bounds",surface->bounds().values()}};
    if(plane.isValid() && std::isfinite(plane.pathLength()) && plane.pathLength()>=options.nearLimit) {
      const auto local=surface->globalToLocal(context,plane.position(),direction,options.surfaceTolerance);
      if(!local.ok()) fail("Independent intersection is not on its official surface");
      const bool inside=surface->insideBounds(local.value(),Acts::BoundaryTolerance::None());
      const auto checked=surface->intersect(context,anchor,direction,Acts::BoundaryTolerance::None()).closestForward();
      if(inside!=checked.isValid()) fail("Bounded intersection and local sensitive bounds disagree");
      row.update({{"forward_plane_intersection",true},{"inside_sensitive_bounds",inside},
        {"path_length_mm",plane.pathLength()},{"local_mm",{local.value()[0],local.value()[1]}}});
      if(inside && !bounded.insert(id).second) fail("Duplicate bounded sensitive crossing");
    }
    report["surface_intersections"].push_back(row);
  }
  Acts::Vector3 position=anchor;
  std::set<std::uint64_t> navigated;
  bool terminated=false;
  for(unsigned step=0;step<1000;++step) {
    const auto target=navigator.nextTarget(state,position,direction);
    if(target.isNone()) {terminated=true;break;}
    const auto& intersection=target.intersection();
    if(!std::isfinite(intersection.pathLength()) || intersection.pathLength()<-options.surfaceTolerance)
      fail("Invalid official Navigator forward target");
    position=intersection.position();
    if(!position.allFinite()) fail("Nonfinite official Navigator position");
    const auto& surface=target.surface();
    report["navigator_steps"].push_back({{"geometry_id",surface.geometryId().value()},
      {"position_mm",{position.x(),position.y(),position.z()}},{"step_path_mm",intersection.pathLength()}});
    const auto found=surfaces.find(surface.geometryId().value());
    if(found!=surfaces.end()) {
      if(found->second!=&surface || !navigated.insert(found->first).second)
        fail("Wrong or duplicate official sensitive navigation target");
    }
    navigator.handleSurfaceReached(state,position,direction,surface);
  }
  report["navigator_terminated"]=terminated;
  report["navigator_end_of_world"]=navigator.endOfWorldReached(state);
  report["bounded_forward_geometry_ids"]=bounded;
  report["navigator_sensitive_geometry_ids"]=navigated;
  if(!terminated || !navigator.endOfWorldReached(state)) fail("Official Navigator did not reach end of world");
  if(navigated!=bounded) fail("Official navigation differs from independent bounded sensitive intersections");
  std::set<std::uint64_t> expected;
  for(const auto id:bounded) {
    if(measured.contains(id)) fail("Measured sensitive crossing lies beyond the last measurement anchor");
    expected.insert(id);
  }
  report["expected_hole_geometry_ids"]=expected;
  if(observed!=expected) fail("Actual CKF holes differ from crossed unmeasured sensitive surfaces");
  report["passed"]=true;
  return report;
}

template<typename Track>
nlohmann::json auditSensitiveNavigation(const Track& track, const ActsGeometryProvider& provider,
    const edm4eic::Measurement2DCollection& measurements) {
  std::set<std::uint64_t> measured;
  std::vector<std::uint64_t> holes;
  std::set<std::size_t> sources;
  std::set<int> layers;
  std::optional<Acts::BoundTrackParameters> anchor;
  std::uint64_t anchorId=0;
  const auto converter=algorithms::GeoSvc::instance().cellIDPositionConverter();
  for(const auto state:track.trackStatesReversed()) {
    const auto flags=state.typeFlags();
    if(flags.isHole()) holes.push_back(state.referenceSurface().geometryId().value());
    if(!flags.isMeasurement()) continue;
    if(flags.isHole() || !state.hasFiltered() || !state.filtered().allFinite() ||
        !state.filteredCovariance().allFinite() || !state.hasUncalibratedSourceLink())
      throw std::runtime_error("Sensitive navigation integrity failure: invalid native measurement state");
    const auto source=state.getUncalibratedSourceLink().template get<ActsExamples::IndexSourceLink>();
    if(source.index()>=measurements.size() || !sources.insert(source.index()).second)
      throw std::runtime_error("Sensitive navigation integrity failure: invalid/duplicate SourceLink index");
    const auto measurement=measurements[source.index()];
    if(measurement.getHits().size()!=1) throw std::runtime_error("Sensitive navigation integrity failure: ambiguous measurement hit");
    const auto cell=measurement.getHits()[0].getCellID();
    const int layer=(cell>>11)&63;
    const auto* volume=converter->findContext(cell);
    if(!volume || (cell&255)!=31 || layer>4 || !layers.insert(layer).second)
      throw std::runtime_error("Sensitive navigation integrity failure: expected unique real SiMAPS L0-L4 cells");
    const auto* surface=provider.surfaceMap().at(volume->identifier);
    const auto id=surface->geometryId().value();
    if(surface!=&state.referenceSurface() || source.geometryId().value()!=id || measurement.getSurface()!=id || !measured.insert(id).second)
      throw std::runtime_error("Sensitive navigation integrity failure: native/SourceLink/CellID/SurfaceMap mismatch");
    // Reverse iteration finds the last forward-filtered measurement first.
    // Its tangent is the one ACTS actually used to navigate unused detectors.
    if(!anchor) {
      anchor.emplace(surface->getSharedPtr(),state.filtered(),state.filteredCovariance(),Acts::ParticleHypothesis::electron());
      anchorId=id;
    }
  }
  if(!anchor || measured.size()!=5 || sources.size()!=measurements.size() || holes.size()!=track.nHoles())
    throw std::runtime_error("Sensitive navigation integrity failure: incomplete or inconsistent native surface states");
  auto report=auditNavigationRay(provider,anchor->position(provider.getActsGeometryContext()),anchor->direction(),anchorId,measured,holes);
  report["anchor_parameters"]="actual last measurement filtered state; no Truth or refit";
  return report;
}
}
