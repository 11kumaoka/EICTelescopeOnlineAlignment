// Noiseless mathematical solver fixture, not a CKF/RawHit or physics closure study.
#include "alignment/NativeMilleModel.h"
#include <DD4hep/Detector.h>
#include <algorithms/geo.h>
#include <algorithms/interfaces/ActsSvc.h>
#include <Mille/MilleFactory.h>
#include <Mille/MilleRecord.h>
#include <nlohmann/json.hpp>
#include <spdlog/sinks/stdout_color_sinks.h>
#include <fstream>
#include <filesystem>
#include <iostream>
using namespace eicrecon::telescope;
int main(int argc,char** argv) {
  if(argc!=4 || std::filesystem::exists(argv[3])) {std::cerr<<"Usage: TelescopeMilleFixture XML NATIVE_REPORT NEW_OUTPUT_DIRECTORY\n";return 64;}
  try {
    const std::filesystem::path output(argv[3]);
    if(!std::filesystem::create_directory(output))throw std::runtime_error("Output must be new");
    auto& detector=dd4hep::Detector::getInstance();detector.fromXML(argv[1]);detector.apply("DD4hepVolumeManager",0,nullptr);
    algorithms::GeoSvc::instance().init(&detector);
    auto provider=std::make_shared<ActsGeometryProvider>();auto log=spdlog::stdout_color_mt("fixture_geometry");
    provider->initialize(&detector,"",log,log);algorithms::ActsSvc::instance().init(provider);
    requireDeterministicGeometry(*provider);
    const auto& context=provider->getActsGeometryContext();
    const auto input=nlohmann::json::parse(std::ifstream(argv[2]));
    std::array<const Acts::Surface*,5> surfaces{};
    std::array<Acts::SquareMatrix2,5> whitening;
    for(const auto& e:input.at("equations")) {
      const int l=e.at("layer");surfaces[l]=provider->surfaceMap().at(e.at("volume_id").get<uint64_t>());
      Acts::SquareMatrix2 covariance;for(int i=0;i<2;++i)for(int j=0;j<2;++j)covariance(i,j)=e.at("covariance")[i][j];
      Eigen::LLT<Acts::SquareMatrix2> factor(covariance);
      if(factor.info()!=Eigen::Success)throw std::runtime_error("Invalid native covariance");
      whitening[l]=factor.matrixL().solve(Acts::SquareMatrix2::Identity());
    }
    auto record=Mille::spawnMilleRecord((output/"fixture.dat").string(),true,false);
    nlohmann::json report={{"scope","noiseless measurement-equation fixture, not CKF or physics closure"},
      {"injected_physical_global_y_mm",{{"700102",.150},{"700202",.200}}},
      {"track_count",16},{"uncertainties","unchanged native calibrated measurement covariance"},
      {"reference","constructed solely from unshifted L0/L4 measurement endpoints"},
      {"fixed","all parameters except L1/L2 physical Global Y"}};
    const std::vector<unsigned int> indices{1,2,3,4};
    for(int event=0;event<16;++event) {
      const Acts::Vector3 start(250.,-8.+event,4.-.3*event);
      const Acts::Vector3 direction=Acts::Vector3(1.,-.008+.001*event,.005-.0004*event).normalized();
      std::array<Acts::Vector2,5> measured;
      for(int l=0;l<5;++l) {
        Acts::Transform3 physical=surfaces[l]->localToGlobalTransform(context);
        if(l==1)physical.translation().y()+=.150;
        if(l==2)physical.translation().y()+=.200;
        measured[l]=rayMeasurement(physical,start,direction);
      }
      // Inference sees only nominal Geometry and the synthetic measurements.
      const auto first=surfaces[0]->localToGlobal(context,measured[0],Acts::Vector3::UnitX());
      const auto last=surfaces[4]->localToGlobal(context,measured[4],Acts::Vector3::UnitX());
      const Acts::Vector3 seedDirection=(last-first).normalized();
      Acts::BoundVector parameters=Acts::BoundVector::Zero();parameters.head<2>()=measured[0];
      parameters[2]=std::atan2(seedDirection.y(),seedDirection.x());parameters[3]=std::acos(seedDirection.z());parameters[4]=-1.;
      Acts::BoundTrackParameters reference(surfaces[0]->getSharedPtr(),parameters,Acts::BoundMatrix::Identity(),Acts::ParticleHypothesis::electron());
      for(int l=0;l<5;++l) {
        const auto equation=nativeEquation(*provider,reference,*surfaces[l]);
        const Acts::Vector2 residual=whitening[l]*(measured[l]-equation.prediction);
        const Acts::Matrix<2,4> local=whitening[l]*equation.local;
        const Acts::Vector2 global=whitening[l]*equation.global.col(1);
        for(int row=0;row<2;++row) {
          std::vector<double> derivatives;for(int k=0;k<4;++k)derivatives.push_back(local(row,k));
          std::vector<int> labels;std::vector<double> globals;
          if(l==1 || l==2) {labels.push_back(700002+100*l);globals.push_back(global[row]);}
          record->addData(residual[row],1.,indices,derivatives,labels,globals);
        }
      }
      record->writeRecord();
    }
    record->flushOutputFile();record.reset();
    std::ofstream json(output/"fixture.json");json<<report.dump(2)<<'\n';
    // Retain the established numerical method and minimum entries setting.
    std::ofstream steering(output/"pede.txt");steering<<"Cfiles\n"<<std::filesystem::absolute(output/"fixture.dat").string()
      <<"\n\nentries 5\nmethod inversion 3 0.001\n\nParameter\n700102 0.0 1.0\n700202 0.0 1.0\n\nprintcounts\nend\n";
    return 0;
  } catch(const std::exception& error) {std::cerr<<error.what()<<'\n';return 2;}
}
