// Telescope-only zero-field seed producer. Factory contracts follow EICrecon TrackSeeding.
#pragma once
#include <algorithms/algorithm.h>
#include <algorithms/interfaces/WithPodConfig.h>
#include <edm4eic/TrackerHitCollection.h>
#include <edm4eic/TrackSeedCollection.h>
#include <edm4eic/TrackParametersCollection.h>
#include <Acts/Definitions/Algebra.hpp>
#include <cmath>
#include <stdexcept>
namespace eicrecon::telescope {
using SeedAlgorithm = algorithms::Algorithm<algorithms::Input<edm4eic::TrackerHitCollection>,
    algorithms::Output<edm4eic::TrackSeedCollection, edm4eic::TrackParametersCollection>>;
class TelescopeSeed : public SeedAlgorithm, public WithPodConfig<NoConfig> {
public:
  TelescopeSeed(std::string_view name) : SeedAlgorithm{name, {"inputTrackerHits"},
      {"outputTrackSeeds", "outputTrackParameters"}, "Zero-field telescope beam seed"} {}
  void init() final {}
  void process(const Input& input, const Output& output) const final {
    const auto [hits] = input;
    auto [seeds, parameters] = output;
    if (hits->size() != 5) throw std::runtime_error("The synthetic telescope seed requires five SiMAPS hits");
    auto point = [](const auto& hit) { const auto p = hit.getPosition(); return Acts::Vector3(p.x, p.y, p.z); };
    const Acts::Vector3 first = point((*hits)[0]), last = point((*hits)[4]);
    const Acts::Vector3 direction = (last-first).normalized();
    if (direction.x() <= 0) throw std::runtime_error("Expected outward radial beam");
    const Acts::Vector3 position = first + ((249.-first.x())/direction.x())*direction;
    auto parameter = parameters->create();
    parameter.setType(-1);
    parameter.setLoc({0, 0});
    parameter.setPhi(std::atan2(direction.y(), direction.x()));
    parameter.setTheta(std::acos(direction.z()));
    // Momentum is a beam prior, not a zero-field momentum measurement.
    parameter.setQOverP(-1.f);
    parameter.setTime(0.f);
    edm4eic::Cov6f covariance{};
    const float diagonal[] = {1.f,1.f,1e-3f,1e-3f,1e-4f,1.f};
    for (int i=0; i<6; ++i) covariance(i,i)=diagonal[i];
    parameter.setCovariance(covariance);
    auto seed = seeds->create();
    seed.setPerigee({float(position.x()),float(position.y()),float(position.z())});
    seed.setParams(parameter);
    for (const auto hit : *hits) seed.addToHits(hit);
  }
};
}
