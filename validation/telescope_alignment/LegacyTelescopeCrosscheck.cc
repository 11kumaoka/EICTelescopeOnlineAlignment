// Numerical comparison only: unchanged historical writer in explicitly rotated axes.
#include "BeamTestMilleWriter.h"
#include <nlohmann/json.hpp>
#include <fstream>
#include <filesystem>
#include <iostream>
#include <cmath>
int main(int argc,char** argv) {
  if(argc!=3 || std::filesystem::exists(argv[2])) {std::cerr<<"Usage: LegacyTelescopeCrosscheck NATIVE_REPORT NEW_MILLE_DAT\n";return 64;}
  try {
    const auto report=nlohmann::json::parse(std::ifstream(argv[1]));
    std::array<local_eicrecon::BeamTestMilleWriter::Hit2D,5> hits;
    for(const auto& equation:report.at("equations")) {
      const int layer=equation.at("layer");
      const auto& rotation=equation.at("rotation");
      const double expected[3][3]={{0,0,1},{-1,0,0},{0,-1,0}};
      for(int i=0;i<3;++i)for(int j=0;j<3;++j)if(std::abs(rotation[i][j].get<double>()-expected[i][j])>1e-10)
        throw std::runtime_error("Legacy comparison requires the explicitly validated nominal radial basis");
      const auto& m=equation.at("measurement");const auto& c=equation.at("center_mm");const auto& covariance=equation.at("covariance");
      if(std::abs(covariance[0][1].get<double>())>1e-12)throw std::runtime_error("Legacy diagonal comparison rejects correlated measurements");
      // Proper world rotation: (old X,old Y,old Z)=(-new Z,new Y,new X).
      hits.at(layer)={layer,m[1][0].get<double>()-c[2].get<double>(),
        c[1].get<double>()-m[0][0].get<double>(),c[0].get<double>(),
        std::sqrt(covariance[1][1].get<double>()),std::sqrt(covariance[0][0].get<double>())};
    }
    local_eicrecon::BeamTestMilleWriter::Config config;config.outputPath=argv[2];config.zRefMm=250.;
    local_eicrecon::BeamTestMilleWriter writer(config);
    if(!writer.writeTrackRecord(hits))return 2;writer.flush();return 0;
  } catch(const std::exception& error) {std::cerr<<error.what()<<'\n';return 2;}
}
