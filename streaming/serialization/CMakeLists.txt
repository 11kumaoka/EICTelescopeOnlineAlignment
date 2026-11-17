cmake_minimum_required(VERSION 3.20)

if(CMAKE_SOURCE_DIR STREQUAL CMAKE_CURRENT_SOURCE_DIR)
  project(TelescopeStreamingSerialization LANGUAGES CXX)
  set(CMAKE_CXX_STANDARD 20)
  set(CMAKE_CXX_STANDARD_REQUIRED ON)
  find_package(podio CONFIG REQUIRED)
  find_package(EDM4EIC CONFIG REQUIRED)
  find_package(EDM4HEP CONFIG REQUIRED)
  find_package(nlohmann_json CONFIG REQUIRED)
  include(CTest)
endif()

find_package(flatbuffers CONFIG REQUIRED)

flatbuffers_generate_headers(
  TARGET TelescopeTimeframeSchema
  SCHEMAS ${CMAKE_CURRENT_SOURCE_DIR}/schema/TelescopeTimeframe.fbs
  FLAGS --scoped-enums)

add_library(TelescopeTimeframeSerialization STATIC
  src/TelescopeTimeframeSerialization.cc)
set_target_properties(TelescopeTimeframeSerialization PROPERTIES
  POSITION_INDEPENDENT_CODE ON)
target_include_directories(TelescopeTimeframeSerialization PUBLIC
  ${CMAKE_CURRENT_SOURCE_DIR}/include)
target_link_libraries(TelescopeTimeframeSerialization
  PUBLIC
    TelescopeTimeframeSchema
    flatbuffers::flatbuffers_shared
    podio::podio
    EDM4EIC::edm4eic
    EDM4HEP::edm4hep)

add_executable(TelescopeFrameRoundTrip tools/TelescopeFrameRoundTrip.cc)
target_link_libraries(TelescopeFrameRoundTrip PRIVATE
  TelescopeTimeframeSerialization
  podio::podioRootIO
  nlohmann_json::nlohmann_json)

add_library(TelescopeRootEventComparison STATIC
  src/TelescopeRootEventComparison.cc)
target_include_directories(TelescopeRootEventComparison PUBLIC
  ${CMAKE_CURRENT_SOURCE_DIR}/include)
target_link_libraries(TelescopeRootEventComparison PUBLIC
  TelescopeTimeframeSerialization
  podio::podioRootIO)

add_executable(TelescopeRootEventCompare
  tools/TelescopeRootEventCompare.cc)
target_link_libraries(TelescopeRootEventCompare PRIVATE
  TelescopeRootEventComparison)

if(BUILD_TESTING)
  add_executable(TelescopeTimeframeSerializationTest
    tests/TelescopeTimeframeSerializationTest.cc)
  target_link_libraries(TelescopeTimeframeSerializationTest PRIVATE
    TelescopeTimeframeSerialization)
  add_test(NAME TelescopeTimeframeSerializationTest
    COMMAND TelescopeTimeframeSerializationTest)

  add_executable(TelescopeRootEventComparisonTest
    tests/TelescopeRootEventComparisonTest.cc)
  target_link_libraries(TelescopeRootEventComparisonTest PRIVATE
    TelescopeRootEventComparison)
  add_test(NAME TelescopeRootEventComparisonTest
    COMMAND TelescopeRootEventComparisonTest)
endif()
