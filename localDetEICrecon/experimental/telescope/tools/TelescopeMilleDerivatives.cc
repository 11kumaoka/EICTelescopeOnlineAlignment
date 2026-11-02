// Mathematical gate: official derivatives versus independent ray/plane differences.
#include "alignment/NativeMilleModel.h"
#include <DD4hep/Detector.h>
#include <DDRec/CellIDPositionConverter.h>
#include <algorithms/geo.h>
#include <algorithms/interfaces/ActsSvc.h>
#include <nlohmann/json.hpp>
#include <spdlog/sinks/stdout_color_sinks.h>
#include <fstream>
#include <filesystem>
#include <iostream>
using namespace eicrecon::telescope;
int main(int argc,char** argv) {
  if(argc!=4 || std::filesystem::exists(argv[3])) {
    std::cerr<<"Usage: TelescopeMilleDerivatives BARREL_XML NATIVE_AUDIT_OR_geometry-center NEW_REPORT_JSON\n";return 64;
  }
  try {
    auto& detector=dd4hep::Detector::getInstance();detector.fromXML(argv[1]);
    detector.apply("DD4hepVolumeManager",0,nullptr);algorithms::GeoSvc::instance().init(&detector);
    auto provider=std::make_shared<ActsGeometryProvider>();auto log=spdlog::stdout_color_mt("derivative_geometry");
    provider->initialize(&detector,"",log,log);algorithms::ActsSvc::instance().init(provider);
    requireDeterministicGeometry(*provider);
    const auto& context=provider->getActsGeometryContext();
    const auto converter=algorithms::GeoSvc::instance().cellIDPositionConverter();
    const bool geometryOnly=std::string(argv[2])=="geometry-center";
    const auto input=geometryOnly?nlohmann::json::object():nlohmann::json::parse(std::ifstream(argv[2]));
    const auto* first=provider->surfaceMap().at(converter->findContext(31ULL|(1ULL<<22)|(1ULL<<28))->identifier);
    Acts::Vector3 point,direction;
    if(geometryOnly) {
      direction=Acts::Vector3(1.,.003,-.002).normalized();
      point=first->localToGlobal(context,Acts::Vector2(2.,3.),direction);
    } else {
      bool found=false;
      for(const auto& state:input.at("smoothed_states")) if(state.at("geometry_id").get<uint64_t>()==first->geometryId().value()) {
        for(int i=0;i<3;++i)point[i]=state.at("position_mm").at(i).get<double>();found=true;
      }
      if(!found)throw std::runtime_error("No native L0 smoothed state");
      for(int i=0;i<3;++i)direction[i]=input.at("edm").at("momentum_GeV").at(i).get<double>();direction.normalize();
    }
    Acts::BoundVector parameters=Acts::BoundVector::Zero();
    parameters.head<2>()=first->globalToLocal(context,point,direction).value();
    parameters[Acts::eBoundPhi]=std::atan2(direction.y(),direction.x());
    parameters[Acts::eBoundTheta]=std::acos(direction.z());parameters[Acts::eBoundQOverP]=-1.;
    Acts::BoundTrackParameters reference(first->getSharedPtr(),parameters,Acts::BoundMatrix::Identity(),Acts::ParticleHypothesis::electron());
    nlohmann::json report={{"passed",false},{"model_sha256",TELESCOPE_MILLE_MODEL_SHA},{"global_convention","physical Global XYZ translations / Global left rotation increments"},
      {"local_convention","reference loc0,loc1,phi,theta"},{"finite_difference_method","independent ray/plane intersections"}};
    double maximumLocal=0.,maximumGlobal=0.;int checks=0;
    auto positionFrom=[&](const Acts::BoundVector& p) {
      const auto d=Acts::makeDirectionFromPhiTheta(p[Acts::eBoundPhi],p[Acts::eBoundTheta]);
      return first->localToGlobal(context,p.head<2>(),d);
    };
    for(int layer=0;layer<5;++layer) {
      const auto volume=converter->findContext(31ULL|(uint64_t(layer)<<11)|(1ULL<<22)|(1ULL<<28))->identifier;
      const auto* surface=provider->surfaceMap().at(volume);
      const auto equation=nativeEquation(*provider,reference,*surface);
      const Acts::Transform3 transform=surface->localToGlobalTransform(context);
      const auto expected=rayMeasurement(transform,reference.position(context),reference.direction());
      if((expected-equation.prediction).norm()>1e-7)throw std::runtime_error("ACTS prediction differs from independent ray");
      for(double scale:{1.,.5}) {
        for(int k=0;k<4;++k) {
          const double step=scale*(k<2?1e-4:1e-6);
          Acts::BoundVector plus=parameters,minus=parameters;plus[k]+=step;minus[k]-=step;
          const auto dp=Acts::makeDirectionFromPhiTheta(plus[2],plus[3]);
          const auto dm=Acts::makeDirectionFromPhiTheta(minus[2],minus[3]);
          const Acts::Vector2 finite=(rayMeasurement(transform,positionFrom(plus),dp)-rayMeasurement(transform,positionFrom(minus),dm))/(2.*step);
          maximumLocal=std::max(maximumLocal,(finite-equation.local.col(k)).cwiseAbs().maxCoeff());++checks;
          if((finite-equation.local.col(k)).norm()>2e-5)throw std::runtime_error("Local ACTS Jacobian finite difference failed");
        }
        for(int k=0;k<6;++k) {
          const double step=scale*(k<3?1e-4:1e-6);
          Acts::Transform3 plus=transform,minus=transform;
          if(k<3) {plus.translation()[k]+=step;minus.translation()[k]-=step;}
          else {
            const Acts::Vector3 axis=Acts::Vector3::Unit(k-3);
            plus.linear()=Eigen::AngleAxisd(step,axis).toRotationMatrix()*transform.linear();
            minus.linear()=Eigen::AngleAxisd(-step,axis).toRotationMatrix()*transform.linear();
          }
          const Acts::Vector2 finite=(rayMeasurement(plus,point,direction)-rayMeasurement(minus,point,direction))/(2.*step);
          maximumGlobal=std::max(maximumGlobal,(finite-equation.global.col(k)).cwiseAbs().maxCoeff());++checks;
          if((finite-equation.global.col(k)).norm()>2e-5)throw std::runtime_error("Global ACTS alignment finite difference failed");
        }
      }
      report["surfaces"].push_back({{"layer",layer},{"volume_id",volume},{"geometry_id",surface->geometryId().value()}});
    }
    report["passed"]=true;report["scalar_vector_checks"]=checks;
    report["maximum_local_absolute_error"]=maximumLocal;report["maximum_global_absolute_error"]=maximumGlobal;
    std::ofstream output(argv[3]);output<<report.dump(2)<<'\n';
    return 0;
  } catch(const std::exception& error) {std::cerr<<error.what()<<'\n';return 2;}
}
