cmake_minimum_required(VERSION 3.16)
project(TelescopeGeometryDiagnostic LANGUAGES CXX)
set(CMAKE_CXX_STANDARD 20)
find_package(Acts REQUIRED COMPONENTS PluginDD4hep)
find_package(DD4hep REQUIRED COMPONENTS DDCore DDRec)
find_package(EICrecon CONFIG REQUIRED)
find_package(nlohmann_json REQUIRED)
find_package(fmt REQUIRED)
find_package(spdlog REQUIRED)
add_executable(TelescopeGeometryProbe TelescopeGeometryProbe.cc)
target_compile_definitions(TelescopeGeometryProbe PRIVATE Acts_VERSION_MAJOR=${Acts_VERSION_MAJOR})
target_link_libraries(TelescopeGeometryProbe PRIVATE
  EICrecon::algorithms_tracking_library Acts::Core Acts::PluginDD4hep
  nlohmann_json::nlohmann_json DD4hep::DDCore DD4hep::DDRec fmt::fmt spdlog::spdlog)

# Normal DD4hep XML plugin discovery; no installed library is changed.
add_library(TestbenchBarrelMetadata SHARED BarrelMetadata.cc)
target_link_libraries(TestbenchBarrelMetadata PRIVATE DD4hep::DDCore DD4hep::DDRec)
dd4hep_generate_rootmap(TestbenchBarrelMetadata)

add_executable(TelescopePhysicalGeometryAudit TelescopePhysicalGeometryAudit.cc)
add_dependencies(TelescopePhysicalGeometryAudit Components_TestbenchBarrelMetadata)
target_link_libraries(TelescopePhysicalGeometryAudit PRIVATE
  EICrecon::algorithms_tracking_library Acts::Core Acts::PluginDD4hep
  nlohmann_json::nlohmann_json DD4hep::DDCore DD4hep::DDRec spdlog::spdlog)
