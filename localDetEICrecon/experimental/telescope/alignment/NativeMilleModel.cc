#include "NativeMilleModel.h"
#include <Acts/Propagator/EigenStepper.hpp>
#include <Acts/Propagator/Navigator.hpp>
#include <Acts/Propagator/Propagator.hpp>
#include <Acts/Geometry/TrackingVolume.hpp>
#include <Acts/Geometry/Layer.hpp>
#include <Acts/Geometry/ApproachDescriptor.hpp>
#include <stdexcept>
namespace eicrecon::telescope {
void requireDeterministicGeometry(const ActsGeometryProvider& geometry) {
  const auto* world=geometry.trackingGeometry()->highestTrackingVolume();
  bool material=false;
  world->visitVolumes([&](const Acts::TrackingVolume* volume) {
    material |= volume->volumeMaterial()!=nullptr;
    for(const auto& boundary:volume->boundarySurfaces())
      material |= boundary->surfaceRepresentation().surfaceMaterial()!=nullptr;
    if(volume->confinedLayers()) for(const auto& layer:volume->confinedLayers()->arrayObjects()) {
      material |= layer->surfaceRepresentation().surfaceMaterial()!=nullptr;
      if(layer->approachDescriptor()) for(const auto* surface:layer->approachDescriptor()->containedSurfaces())
        material |= surface->surfaceMaterial()!=nullptr;
    }
  });
  for(const auto& [id,surface]:geometry.surfaceMap()) material |= surface->surfaceMaterial()!=nullptr;
  if(material) throw std::runtime_error("Native Mille prototype rejects assigned ACTS material/process noise");
}
NativeEquation nativeEquation(const ActsGeometryProvider& geometry,
    const Acts::BoundTrackParameters& reference, const Acts::Surface& surface) {
  const auto& gctx=geometry.getActsGeometryContext();
  const auto& mctx=geometry.getActsMagneticFieldContext();
  auto cache=geometry.getFieldProvider()->makeCache(mctx);
  const auto startField=geometry.getFieldProvider()->getField(reference.position(gctx),cache);
  // This fixed telescope has no magnetic field. Checking transported sample
  // points below is an additional guard, not a generic proof for arbitrary maps.
  if(!startField.ok() || startField.value().norm()>1e-15)
    throw std::runtime_error("Native Mille prototype requires zero magnetic field");
  Acts::BoundVector bound=reference.parameters();
  Acts::BoundMatrix jacobian=Acts::BoundMatrix::Identity();
  Acts::Vector3 position=reference.position(gctx),direction=reference.direction();
  if(&surface!=&reference.referenceSurface()) {
    using Propagator=Acts::Propagator<Acts::EigenStepper<>,Acts::Navigator>;
    Propagator propagator{Acts::EigenStepper<>(geometry.getFieldProvider()),
        Acts::Navigator({.trackingGeometry=geometry.trackingGeometry()})};
    Propagator::Options<> options(gctx,mctx); options.maxSteps=10000;
    auto result=propagator.propagate(reference,surface,options);
    if(!result.ok() || !result.value().endParameters || !result.value().transportJacobian)
      throw std::runtime_error("ACTS reference-to-measurement propagation/Jacobian unavailable");
    bound=result.value().endParameters->parameters();
    jacobian=*result.value().transportJacobian;
    position=result.value().endParameters->position(gctx);
    direction=result.value().endParameters->direction();
  }
  const auto targetField=geometry.getFieldProvider()->getField(position,cache);
  if(!targetField.ok() || targetField.value().norm()>1e-15)
    throw std::runtime_error("Native Mille prototype rejects magnetic field at measurement");
  NativeEquation equation;
  equation.prediction=bound.head<2>();
  for(int column=0;column<4;++column) equation.local.col(column)=jacobian.block<2,1>(0,column);
  // In this deterministic limit the full position path derivative is direction.
  // A curved/material trajectory requires additional terms and a different model.
  Acts::FreeVector pathDerivative=Acts::FreeVector::Zero();
  pathDerivative.segment<3>(Acts::eFreePos0)=direction;
  const auto alignment=surface.alignmentToBoundDerivative(gctx,position,direction,pathDerivative);
  equation.global.leftCols<3>()=alignment.block<2,3>(0,0);
  // ACTS rotations are about its current local axes (right increments).
  // Physical Global left increments map through R^T at this linearization point.
  equation.global.rightCols<3>()=alignment.block<2,3>(0,3)*surface.localToGlobalTransform(gctx).linear().transpose();
  if(!equation.local.allFinite() || !equation.global.allFinite())
    throw std::runtime_error("Non-finite native measurement derivatives");
  if(jacobian.block<2,2>(0,4).norm()>1e-10)
    throw std::runtime_error("q/p or time affects measured coordinates: four-local-parameter prototype invalid");
  return equation;
}
Acts::Vector2 rayMeasurement(const Acts::Transform3& transform,
    const Acts::Vector3& position,const Acts::Vector3& direction) {
  const Acts::Vector3 normal=transform.linear().col(2);
  const double denominator=normal.dot(direction);
  if(std::abs(denominator)<1e-8) throw std::runtime_error("Grazing ray/plane intersection");
  const double path=normal.dot(transform.translation()-position)/denominator;
  return (transform.linear().transpose()*(position+path*direction-transform.translation())).head<2>();
}
}
