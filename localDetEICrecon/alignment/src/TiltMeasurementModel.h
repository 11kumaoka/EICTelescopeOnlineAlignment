#pragma once

#include <Eigen/Dense>
#include <cmath>
#include <stdexcept>

namespace local_eicrecon {
// F = (x0+tx*(mz-zref)-mx, y0+ty*(mz-zref)-my).
// The sensor covariance lives in ACTS u/v, not in independent Global X/Y.
inline Eigen::Matrix2d tiltResidualJacobian(const Eigen::Vector3d& u,
                                           const Eigen::Vector3d& v,
                                           double tx, double ty) {
  Eigen::Matrix2d h;
  h << tx*u.z()-u.x(), tx*v.z()-v.x(),
       ty*u.z()-u.y(), ty*v.z()-v.y();
  return h;
}

inline Eigen::Matrix2d tiltWhitening(const Eigen::Vector3d& u,
                                    const Eigen::Vector3d& v,
                                    double sigmaU, double sigmaV,
                                    double tx, double ty) {
  const auto h=tiltResidualJacobian(u,v,tx,ty);
  Eigen::Matrix2d c=h*Eigen::Vector2d(sigmaU*sigmaU,sigmaV*sigmaV).asDiagonal()*h.transpose();
  Eigen::LLT<Eigen::Matrix2d> factor(c);
  if(factor.info()!=Eigen::Success)throw std::runtime_error("Singular tilted-plane residual covariance");
  return factor.matrixL().solve(Eigen::Matrix2d::Identity());
}

inline Eigen::Vector2d physicalRotationDerivative(const Eigen::Vector3d& axis,
                                                 const Eigen::Vector3d& offset,
                                                 double tx, double ty) {
  const Eigen::Vector3d dm=axis.cross(offset);
  return {tx*dm.z()-dm.x(),ty*dm.z()-dm.y()};
}
} // namespace local_eicrecon
