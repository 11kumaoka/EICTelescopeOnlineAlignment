cmake_minimum_required(VERSION 3.20)
project(TelescopeTracking LANGUAGES CXX)
set(CMAKE_CXX_STANDARD 20)
find_package(EICrecon CONFIG REQUIRED)
find_package(JANA REQUIRED)
find_package(DD4hep REQUIRED COMPONENTS DDCore DDRec)
find_package(Acts REQUIRED COMPONENTS PluginDD4hep)
find_package(EDM4EIC REQUIRED)
find_package(algorithms REQUIRED)
find_package(nlohmann_json REQUIRED)
find_package(spdlog REQUIRED)
find_library(ACTS_EXAMPLES_FRAMEWORK NAMES ActsExamplesFramework REQUIRED)
find_path(EICRECON_INCLUDE_DIR extensions/jana/JOmniFactory.h HINTS /opt/local/include/EICrecon REQUIRED)
add_library(TestbenchBarrelMetadata SHARED geometry/BarrelMetadata.cc)
target_link_libraries(TestbenchBarrelMetadata PRIVATE DD4hep::DDCore DD4hep::DDRec)
dd4hep_generate_rootmap(TestbenchBarrelMetadata)
add_library(TelescopeDigitization SHARED TelescopeDigitization.cc)
set_target_properties(TelescopeDigitization PROPERTIES PREFIX "")
target_include_directories(TelescopeDigitization PRIVATE ${EICRECON_INCLUDE_DIR})
target_compile_definitions(TelescopeDigitization PRIVATE USE_MODERN_PODIO_GLUE=1)
target_link_libraries(TelescopeDigitization PRIVATE EICrecon::algorithms_digi_library
  JANA::jana2_shared_lib DD4hep::DDCore DD4hep::DDRec EDM4EIC::edm4eic algorithms::core)
add_library(TelescopeTracking SHARED TelescopeTracking.cc algorithms/tracking/TrackerMeasurementFromHits.cc algorithms/tracking/CKFTracking.cc)
set_target_properties(TelescopeTracking PROPERTIES PREFIX "")
target_include_directories(TelescopeTracking PRIVATE ${CMAKE_CURRENT_SOURCE_DIR}/upstream_support ${EICRECON_INCLUDE_DIR})
target_compile_definitions(TelescopeTracking PRIVATE Acts_VERSION_MAJOR=${Acts_VERSION_MAJOR} Acts_VERSION_MINOR=${Acts_VERSION_MINOR} USE_MODERN_PODIO_GLUE=1)
target_link_libraries(TelescopeTracking PRIVATE EICrecon::algorithms_tracking_library
  JANA::jana2_shared_lib DD4hep::DDCore DD4hep::DDRec Acts::Core Acts::PluginDD4hep
  ${ACTS_EXAMPLES_FRAMEWORK} EDM4EIC::edm4eic algorithms::core nlohmann_json::nlohmann_json spdlog::spdlog)

option(TELESCOPE_BUILD_STREAMING_SERIALIZATION
  "Build the JANA/ZeroMQ-independent PODIO FlatBuffers prototype" OFF)
if(TELESCOPE_BUILD_STREAMING_SERIALIZATION)
  include(CTest)
  add_subdirectory(
    ${CMAKE_CURRENT_SOURCE_DIR}/../../../streaming/serialization
    ${CMAKE_CURRENT_BINARY_DIR}/streaming_serialization)
endif()

option(TELESCOPE_BUILD_ZMQ_TRANSPORT
  "Build the standalone ZeroMQ FlatBuffers sender and receiver" OFF)
if(TELESCOPE_BUILD_ZMQ_TRANSPORT)
  if(NOT TELESCOPE_BUILD_STREAMING_SERIALIZATION)
    message(FATAL_ERROR
      "TELESCOPE_BUILD_ZMQ_TRANSPORT requires TELESCOPE_BUILD_STREAMING_SERIALIZATION")
  endif()
  add_subdirectory(
    ${CMAKE_CURRENT_SOURCE_DIR}/../../../streaming/zeromq
    ${CMAKE_CURRENT_BINARY_DIR}/streaming_zeromq)
endif()

option(TELESCOPE_BUILD_ZMQ_EVENT_SOURCE
  "Build the EICrecon/JANA ZeroMQ PODIO event source" OFF)
if(TELESCOPE_BUILD_ZMQ_EVENT_SOURCE)
  if(NOT TELESCOPE_BUILD_STREAMING_SERIALIZATION OR
     NOT TELESCOPE_BUILD_ZMQ_TRANSPORT)
    message(FATAL_ERROR
      "TELESCOPE_BUILD_ZMQ_EVENT_SOURCE requires serialization and ZeroMQ transport")
  endif()
  find_package(Threads REQUIRED)
  add_library(TelescopeZmqEventSourceCore STATIC
    streaming/TelescopeZmqEventSource.cc)
  set_target_properties(TelescopeZmqEventSourceCore PROPERTIES
    POSITION_INDEPENDENT_CODE ON)
  target_include_directories(TelescopeZmqEventSourceCore PUBLIC
    ${CMAKE_CURRENT_SOURCE_DIR}
    ${CMAKE_CURRENT_SOURCE_DIR}/streaming)
  target_link_libraries(TelescopeZmqEventSourceCore PUBLIC
    JANA::jana2_shared_lib
    Threads::Threads
    TelescopeZmqTransport
    TelescopeTimeframeSerialization)

  add_library(TelescopeStreaming SHARED
    streaming/TelescopeStreaming.cc)
  set_target_properties(TelescopeStreaming PROPERTIES PREFIX "")
  target_link_libraries(TelescopeStreaming PRIVATE
    TelescopeZmqEventSourceCore)

  add_executable(TelescopeZmqEventSourceTest
    tools/TelescopeZmqEventSourceTest.cc)
  target_link_libraries(TelescopeZmqEventSourceTest PRIVATE
    TelescopeZmqEventSourceCore)
  if(BUILD_TESTING)
    add_test(NAME TelescopeZmqEventSourceTest
      COMMAND TelescopeZmqEventSourceTest 1)
    add_test(NAME TelescopeZmqEventSourceMultithreadTest
      COMMAND TelescopeZmqEventSourceTest 4)
  endif()
endif()

# Opt-in alignment mathematics validation; reconstruction targets are unchanged.
option(TELESCOPE_BUILD_MILLE_PROBE "Build the isolated native-state Mille prototype" OFF)
if(TELESCOPE_BUILD_MILLE_PROBE)
  # Use exactly the same prefix selection as the Python runtime and provenance.
  find_package(Python3 REQUIRED COMPONENTS Interpreter)
  execute_process(
    COMMAND "${Python3_EXECUTABLE}" -B -c "from research_common import prefix; print(prefix())"
    WORKING_DIRECTORY "${CMAKE_CURRENT_SOURCE_DIR}/../../.."
    OUTPUT_VARIABLE _mille_prefix OUTPUT_STRIP_TRAILING_WHITESPACE
    RESULT_VARIABLE _mille_status ERROR_VARIABLE _mille_error)
  if(NOT _mille_status EQUAL 0)
    message(FATAL_ERROR "Cannot select Mille/Pede installation: ${_mille_error}")
  endif()
  message(STATUS "Alignment Mille/Pede prefix: ${_mille_prefix}")
  set(Mille_DIR "${_mille_prefix}/cmake" CACHE PATH "Shared Mille/Pede installation (MILLEPEDE_PREFIX)" FORCE)
  find_package(Mille CONFIG REQUIRED NO_DEFAULT_PATH PATHS "${Mille_DIR}")
  add_library(NativeMilleModel STATIC alignment/NativeMilleModel.cc)
  set_target_properties(NativeMilleModel PROPERTIES POSITION_INDEPENDENT_CODE ON)
  set_property(DIRECTORY APPEND PROPERTY CMAKE_CONFIGURE_DEPENDS alignment/NativeMilleModel.cc alignment/NativeMilleModel.h)
  file(SHA256 "${CMAKE_CURRENT_SOURCE_DIR}/alignment/NativeMilleModel.cc" _model_cc_sha)
  file(SHA256 "${CMAKE_CURRENT_SOURCE_DIR}/alignment/NativeMilleModel.h" _model_h_sha)
  string(SHA256 _model_sha "${_model_cc_sha}${_model_h_sha}")
  target_compile_definitions(NativeMilleModel PUBLIC Acts_VERSION_MAJOR=${Acts_VERSION_MAJOR} Acts_VERSION_MINOR=${Acts_VERSION_MINOR} TELESCOPE_MILLE_MODEL_SHA="${_model_sha}")
  target_include_directories(NativeMilleModel PUBLIC ${CMAKE_CURRENT_SOURCE_DIR} ${EICRECON_INCLUDE_DIR})
  target_link_libraries(NativeMilleModel PUBLIC EICrecon::algorithms_tracking_library Acts::Core
    ${ACTS_EXAMPLES_FRAMEWORK} DD4hep::DDCore DD4hep::DDRec EDM4EIC::edm4eic)
  add_library(TelescopeMille SHARED
    alignment/TelescopeMille.cc
    alignment/MilleRecordOutput.cc)
  set_target_properties(TelescopeMille PROPERTIES PREFIX "")
  target_compile_definitions(TelescopeMille PRIVATE USE_MODERN_PODIO_GLUE=1 Acts_VERSION_MAJOR=${Acts_VERSION_MAJOR} Acts_VERSION_MINOR=${Acts_VERSION_MINOR})
  target_link_libraries(TelescopeMille PRIVATE NativeMilleModel JANA::jana2_shared_lib Mille2 nlohmann_json::nlohmann_json)
  if(TELESCOPE_BUILD_ZMQ_TRANSPORT)
    add_subdirectory(
      ${CMAKE_CURRENT_SOURCE_DIR}/../../../streaming/mille
      ${CMAKE_CURRENT_BINARY_DIR}/streaming_mille)
    target_compile_definitions(TelescopeMille PRIVATE TELESCOPE_MILLE_ZMQ=1)
    target_link_libraries(TelescopeMille PRIVATE TelescopeMilleStreaming)
  endif()
  add_executable(TelescopeChannels tools/TelescopeChannels.cc)
  target_link_libraries(TelescopeChannels PRIVATE NativeMilleModel nlohmann_json::nlohmann_json spdlog::spdlog)
  add_executable(TelescopeMilleFixture ../../../validation/telescope_alignment/TelescopeMilleFixture.cc)
  target_link_libraries(TelescopeMilleFixture PRIVATE NativeMilleModel Mille2 nlohmann_json::nlohmann_json spdlog::spdlog)
  add_executable(LegacyTelescopeCrosscheck ../../../validation/telescope_alignment/LegacyTelescopeCrosscheck.cc ../../alignment/src/BeamTestMilleWriter.cc)
  target_include_directories(LegacyTelescopeCrosscheck PRIVATE ../../alignment/src)
  target_link_libraries(LegacyTelescopeCrosscheck PRIVATE NativeMilleModel Mille2 nlohmann_json::nlohmann_json)
  add_executable(TelescopeMilleDerivatives tools/TelescopeMilleDerivatives.cc)
  target_link_libraries(TelescopeMilleDerivatives PRIVATE NativeMilleModel nlohmann_json::nlohmann_json spdlog::spdlog)
  add_executable(TelescopePhysicalGeometryAudit tools/TelescopePhysicalGeometryAudit.cc)
  add_dependencies(TelescopePhysicalGeometryAudit Components_TestbenchBarrelMetadata)
  target_include_directories(TelescopePhysicalGeometryAudit PRIVATE ${EICRECON_INCLUDE_DIR})
  target_link_libraries(TelescopePhysicalGeometryAudit PRIVATE EICrecon::algorithms_tracking_library Acts::Core Acts::PluginDD4hep
    DD4hep::DDCore DD4hep::DDRec nlohmann_json::nlohmann_json spdlog::spdlog)
  add_executable(TelescopeNavigationAudit tools/TelescopeNavigationAudit.cc)
  add_dependencies(TelescopeNavigationAudit Components_TestbenchBarrelMetadata)
  target_include_directories(TelescopeNavigationAudit PRIVATE ${CMAKE_CURRENT_SOURCE_DIR} ${EICRECON_INCLUDE_DIR})
  target_link_libraries(TelescopeNavigationAudit PRIVATE EICrecon::algorithms_tracking_library Acts::Core Acts::PluginDD4hep
    DD4hep::DDCore DD4hep::DDRec EDM4EIC::edm4eic algorithms::core nlohmann_json::nlohmann_json spdlog::spdlog)
  # EICrecon public headers select GeometryContext initialization by ACTS version.
  # These standalone tools do not inherit NativeMilleModel usage requirements.
  foreach(_target TelescopePhysicalGeometryAudit TelescopeNavigationAudit)
    target_compile_definitions(${_target} PRIVATE Acts_VERSION_MAJOR=${Acts_VERSION_MAJOR} Acts_VERSION_MINOR=${Acts_VERSION_MINOR})
  endforeach()
  add_executable(TelescopeSerializationAudit tools/TelescopeSerializationAudit.cc algorithms/tracking/TrackerMeasurementFromHits.cc)
  target_compile_definitions(TelescopeSerializationAudit PRIVATE Acts_VERSION_MAJOR=${Acts_VERSION_MAJOR} Acts_VERSION_MINOR=${Acts_VERSION_MINOR})
  add_dependencies(TelescopeSerializationAudit Components_TestbenchBarrelMetadata)
  target_include_directories(TelescopeSerializationAudit PRIVATE ${CMAKE_CURRENT_SOURCE_DIR} ${EICRECON_INCLUDE_DIR})
  target_link_libraries(TelescopeSerializationAudit PRIVATE EICrecon::algorithms_tracking_library Acts::Core Acts::PluginDD4hep
    DD4hep::DDCore DD4hep::DDRec EDM4EIC::edm4eic algorithms::core nlohmann_json::nlohmann_json spdlog::spdlog)
endif()
