// Experimental deterministic measurement equations; no fit or Geometry update.
#pragma once
#include <Acts/EventData/TrackParameters.hpp>
#include <algorithms/tracking/ActsGeometryProvider.h>
#include <Eigen/Cholesky>
namespace eicrecon::telescope {
struct NativeEquation {
  Acts::Vector2 prediction;
  Acts::Matrix<2,4> local;
  // Physical Global translations (mm), then Global rotation increments (rad).
  Acts::Matrix<2,6> global;
};
// Valid only with zero field, no ACTS material/process noise and loc0/loc1 readout.
// Reference coordinates are (loc0,loc1,phi,theta); q/p and time are unmeasured.
NativeEquation nativeEquation(const ActsGeometryProvider& geometry,
    const Acts::BoundTrackParameters& reference, const Acts::Surface& surface);
void requireDeterministicGeometry(const ActsGeometryProvider& geometry);
// Independent ray/plane calculation for finite differences only.
Acts::Vector2 rayMeasurement(const Acts::Transform3& transform,
    const Acts::Vector3& position, const Acts::Vector3& direction);
}
