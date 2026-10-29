// Origin: local implementation for seven-layer beam-test framework
// Local purpose: write official Mille binary records for five-layer SiMAPS alignment

#include "BeamTestMilleWriter.h"
#include "TiltMeasurementModel.h"

#include <Mille/MilleFactory.h>
#include <Mille/MilleRecord.h>

#include <cmath>
#include <algorithm>
#include <iomanip>
#include <iostream>
#include <vector>

namespace local_eicrecon {

namespace {

constexpr unsigned int kLocalIndices[4] = {1U, 2U, 3U, 4U};

} // namespace

BeamTestMilleWriter::BeamTestMilleWriter(const Config& cfg) : m_cfg(cfg) {
  m_record = Mille::spawnMilleRecord(m_cfg.outputPath, m_cfg.doublePrecision, m_cfg.writeZeroDerivatives);
  if (!m_record) {
    throw std::runtime_error("failed to create Mille writer for output: " + m_cfg.outputPath);
  }
}

BeamTestMilleWriter::~BeamTestMilleWriter() {
  try {
    flush();
  } catch (...) {
  }
}

int BeamTestMilleWriter::labelAx(int layer) {
  // Stable global labels for layer-x alignment bias parameters.
  return 1000 + layer;
}

int BeamTestMilleWriter::labelAy(int layer) {
  // Stable global labels for layer-y alignment bias parameters.
  return 2000 + layer;
}

bool BeamTestMilleWriter::writeTrackRecord(const std::array<Hit2D, 5>& hits) {
  std::lock_guard<std::mutex> lock(m_mutex);
  if (!m_record) {
    return false;
  }

  std::vector<unsigned int> localIdx(std::begin(kLocalIndices), std::end(kLocalIndices));

  if(m_cfg.tiltMeasurementModel) {
    using AType=Eigen::Matrix<double,2,4>;
    Eigen::Vector4d reference=Eigen::Vector4d::Zero();
    std::array<Eigen::Matrix2d,5> whitening;
    bool converged=false;
    for(int iteration=0;iteration<16;++iteration) {
      Eigen::Matrix4d normal=Eigen::Matrix4d::Zero();Eigen::Vector4d rhs=Eigen::Vector4d::Zero();
      for(const auto& h:hits) {
        const int l=h.layer;const auto& u=m_cfg.surfaceU.at(l);const auto& v=m_cfg.surfaceV.at(l);
        // RecHit xx/yy are sensor X/Z; ACTS u=-sensor Z and v=sensor X.
        whitening[l]=tiltWhitening(Eigen::Vector3d(u.data()),Eigen::Vector3d(v.data()),h.sigma_y_mm,h.sigma_x_mm,reference[2],reference[3]);
        AType a;a<<1.,0.,h.z_mm-m_cfg.zRefMm,0.,0.,1.,0.,h.z_mm-m_cfg.zRefMm;
        const AType wa=whitening[l]*a;const Eigen::Vector2d wm=whitening[l]*Eigen::Vector2d(h.x_mm,h.y_mm);
        normal+=wa.transpose()*wa;rhs+=wa.transpose()*wm;
      }
      Eigen::LDLT<Eigen::Matrix4d> factor(normal);
      if(factor.info()!=Eigen::Success || !factor.isPositive())throw std::runtime_error("Unobservable tilted-plane reference trajectory");
      const Eigen::Vector4d next=factor.solve(rhs);
      if(!next.allFinite())throw std::runtime_error("Non-finite tilted-plane reference trajectory");
      const bool stable=(next-reference).cwiseAbs().maxCoeff()<1.e-12;
      reference=next;
      if(stable){converged=true;break;}
    }
    if(!converged)throw std::runtime_error("Tilt reference covariance iteration did not converge");
    const auto precision=std::cerr.precision();
    std::cerr<<std::setprecision(17)<<"BeamTestTiltReference record="<<m_recordsWritten<<" tx="<<reference[2]<<" ty="<<reference[3]<<std::endl;
    std::cerr.precision(precision);
    for(const auto& h:hits) {
      const int l=h.layer;const auto& center=m_cfg.sensitiveCenterMm.at(l);
      const auto& u=m_cfg.surfaceU.at(l);const auto& v=m_cfg.surfaceV.at(l);
      const Eigen::Matrix2d w=tiltWhitening(Eigen::Vector3d(u.data()),Eigen::Vector3d(v.data()),h.sigma_y_mm,h.sigma_x_mm,reference[2],reference[3]);
      AType a;a<<1.,0.,h.z_mm-m_cfg.zRefMm,0.,0.,1.,0.,h.z_mm-m_cfg.zRefMm;
      const AType wa=w*a;const Eigen::Vector2d wm=w*Eigen::Vector2d(h.x_mm,h.y_mm);
      const Eigen::Vector3d offset(h.x_mm-center[0],h.y_mm-center[1],h.z_mm-center[2]);
      std::vector<int> labels{labelAx(l),labelAy(l)};
      std::vector<Eigen::Vector2d> derivatives{Eigen::Vector2d(1.,0.),Eigen::Vector2d(0.,1.)};
      // Preserve the established Rz bias sign: physical delta_z = -parameter.
      if(m_cfg.rotationEnabled.at(l)){labels.push_back(3000+l);derivatives.push_back(-physicalRotationDerivative(Eigen::Vector3d::UnitZ(),offset,reference[2],reference[3]));}
      if(m_cfg.zEnabled.at(l)){labels.push_back(4000+l);derivatives.emplace_back(reference[2],reference[3]);}
      if(m_cfg.rxEnabled.at(l)){labels.push_back(5000+l);derivatives.push_back(physicalRotationDerivative(Eigen::Vector3d::UnitX(),offset,reference[2],reference[3]));}
      if(m_cfg.ryEnabled.at(l)){labels.push_back(6000+l);derivatives.push_back(physicalRotationDerivative(Eigen::Vector3d::UnitY(),offset,reference[2],reference[3]));}
      for(auto& derivative:derivatives)derivative=w*derivative;
      for(int row=0;row<2;++row) {
        std::vector<float> local(4),global;
        for(int j=0;j<4;++j)local[j]=static_cast<float>(wa(row,j));
        for(const auto& derivative:derivatives)global.push_back(static_cast<float>(derivative[row]));
        m_record->addData(wm[row],1.,localIdx,local,labels,global);
      }
    }
    m_record->writeRecord();++m_recordsWritten;m_measurementsWritten+=10;return true;
  }

  double referenceTx=0.,referenceTy=0.;
  const bool zAlignment=std::any_of(m_cfg.zEnabled.begin(),m_cfg.zEnabled.end(),[](bool v){return v;});
  if (zAlignment) {
    // Profile the existing four-local-parameter model at the current Geometry.
    // Freeze these reference slopes for this linear solve; relinearize externally.
    auto slope=[&](bool x) {
      double sw=0.,sz=0.,sm=0.;
      for(const auto& h:hits) {
        const double w=1./std::pow(x?h.sigma_x_mm:h.sigma_y_mm,2);
        sw+=w;sz+=w*(h.z_mm-m_cfg.zRefMm);sm+=w*(x?h.x_mm:h.y_mm);
      }
      const double zbar=sz/sw,mbar=sm/sw;double numerator=0.,denominator=0.;
      for(const auto& h:hits) {
        const double w=1./std::pow(x?h.sigma_x_mm:h.sigma_y_mm,2),dz=h.z_mm-m_cfg.zRefMm-zbar;
        numerator+=w*dz*((x?h.x_mm:h.y_mm)-mbar);denominator+=w*dz*dz;
      }
      if(!(denominator>0.))throw std::runtime_error("Unobservable local reference slope");
      const double value=numerator/denominator;
      if(!std::isfinite(value))throw std::runtime_error("Non-finite local reference slope");
      return value;
    };
    referenceTx=slope(true);referenceTy=slope(false);
    const auto precision=std::cerr.precision();
    std::cerr<<std::setprecision(17)<<"BeamTestZReference record="<<m_recordsWritten
             <<" tx="<<referenceTx<<" ty="<<referenceTy<<std::endl;
    std::cerr.precision(precision);
  }

  for (const auto& h : hits) {
    const double dz = h.z_mm - m_cfg.zRefMm;

    // x measurement equation:
    // x_meas = x0 + tx*(z-zref) + a_x[layer] + noise
    // local derivatives wrt (x0, y0, tx, ty): (1, 0, dz, 0)
    // global derivative wrt a_x[layer]: +1
    std::vector<float> dLocalX{1.0F, 0.0F, static_cast<float>(dz), 0.0F};
    std::vector<int> gLabelX{labelAx(h.layer)};
    std::vector<float> dGlobalX{1.0F};

    if (m_cfg.rotationEnabled.at(h.layer)) {
      // F = prediction + bias - measurement; physical correction is -bias.
      // dF/da_rz = J (measurement - pivot), in mm/rad.
      gLabelX.push_back(3000+h.layer);
      dGlobalX.push_back(static_cast<float>(-(h.y_mm-m_cfg.pivotMm[h.layer][1])));
    }
    if(m_cfg.zEnabled.at(h.layer)) {
      // Physical Global +Z correction, mm: dF/d(delta_z)=reference tx (mm/mm).
      gLabelX.push_back(4000+h.layer);dGlobalX.push_back(static_cast<float>(referenceTx));
    }
    m_record->addData(h.x_mm, h.sigma_x_mm, localIdx, dLocalX, gLabelX, dGlobalX);

    // y measurement equation:
    // y_meas = y0 + ty*(z-zref) + a_y[layer] + noise
    // local derivatives wrt (x0, y0, tx, ty): (0, 1, 0, dz)
    // global derivative wrt a_y[layer]: +1
    std::vector<float> dLocalY{0.0F, 1.0F, 0.0F, static_cast<float>(dz)};
    std::vector<int> gLabelY{labelAy(h.layer)};
    std::vector<float> dGlobalY{1.0F};

    if (m_cfg.rotationEnabled.at(h.layer)) {
      gLabelY.push_back(3000+h.layer);
      dGlobalY.push_back(static_cast<float>(h.x_mm-m_cfg.pivotMm[h.layer][0]));
    }
    if(m_cfg.zEnabled.at(h.layer)) {
      gLabelY.push_back(4000+h.layer);dGlobalY.push_back(static_cast<float>(referenceTy));
    }
    m_record->addData(h.y_mm, h.sigma_y_mm, localIdx, dLocalY, gLabelY, dGlobalY);
  }

  m_record->writeRecord();
  ++m_recordsWritten;
  m_measurementsWritten += 10;
  return true;
}

void BeamTestMilleWriter::flush() {
  std::lock_guard<std::mutex> lock(m_mutex);
  if (m_record) {
    m_record->flushOutputFile();
  }
}

} // namespace local_eicrecon
