// SPDX-License-Identifier: LGPL-3.0-or-later
// Copyright (C) 2022 Whitney Armstrong, Wouter Deconinck

#pragma once

#include <Acts/EventData/VectorMultiTrajectory.hpp>
#include <Acts/EventData/VectorTrackContainer.hpp>
#include <Acts/Geometry/TrackingGeometry.hpp>
#include <Acts/MagneticField/MagneticFieldProvider.hpp>
#include <Acts/TrackFinding/CombinatorialKalmanFilter.hpp>
#include <Acts/TrackFinding/MeasurementSelector.hpp>
#include <Acts/Utilities/Logger.hpp>
#include <Acts/Utilities/Result.hpp>
#include <ActsExamples/EventData/Track.hpp>
#include <algorithms/algorithm.h>
#include <edm4eic/Measurement2DCollection.h>
#include <edm4eic/TrackSeedCollection.h>
#include <memory>
#include <string>
#include <string_view>
#include <vector>

#include "CKFTrackingConfig.h"
#include <algorithms/tracking/CKFTracking.h>
#include "algorithms/interfaces/ActsSvc.h"
#include "algorithms/interfaces/WithPodConfig.h"
#include "algorithms/tracking/ActsGeometryProvider.h"

namespace eicrecon::telescope {

using CKFTrackingAlgorithm = algorithms::Algorithm<
    algorithms::Input<edm4eic::TrackSeedCollection, edm4eic::Measurement2DCollection>,
    algorithms::Output<Acts::ConstVectorMultiTrajectory*, Acts::ConstVectorTrackContainer*>>;

/** Fitting algorithm implementation .
 *
 * \ingroup tracking
 */

class CKFTracking : public CKFTrackingAlgorithm, public WithPodConfig<CKFTrackingConfig> {
public:
  // Reuse the installed official CKF implementation and its interface types.
  using TrackFinderOptions = eicrecon::CKFTracking::TrackFinderOptions;
  using CKFTrackingFunction = eicrecon::CKFTracking::CKFTrackingFunction;

  CKFTracking(std::string_view name)
      : CKFTrackingAlgorithm{name,
                             {"inputTrackParameters", "inputMeasurements"},
                             {"outputActsTrackStates", "outputActsTracks"},
                             "Combinatorial Kalman Filter track finding"} {}

  void init() final;
  void process(const Input&, const Output&) const final;

private:
  std::shared_ptr<const Acts::Logger> m_acts_logger{nullptr};
  std::shared_ptr<CKFTrackingFunction> m_trackFinderFunc;
  std::shared_ptr<const ActsGeometryProvider> m_geoSvc{
      algorithms::ActsSvc::instance().acts_geometry_provider()};
  std::shared_ptr<const Acts::MagneticFieldProvider> m_BField{m_geoSvc->getFieldProvider()};

  Acts::MeasurementSelector::Config m_sourcelinkSelectorCfg;

  /// Private access to the logging instance
  const Acts::Logger& acts_logger() const { return *m_acts_logger; }
};

} // namespace eicrecon::telescope
