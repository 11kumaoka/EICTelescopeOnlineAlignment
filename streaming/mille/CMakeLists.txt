cmake_minimum_required(VERSION 3.20)

find_package(flatbuffers CONFIG REQUIRED)
find_package(Threads REQUIRED)

flatbuffers_generate_headers(
  TARGET TelescopeMilleSchema
  SCHEMAS ${CMAKE_CURRENT_SOURCE_DIR}/schema/MilleTrackRecord.fbs
  FLAGS --scoped-enums)

add_library(TelescopeMilleStreaming STATIC
  src/MilleTrackRecordSerialization.cc
  src/MilleZmqRecordOutput.cc
  src/MilleCollector.cc)
set_target_properties(TelescopeMilleStreaming PROPERTIES
  POSITION_INDEPENDENT_CODE ON)
target_include_directories(TelescopeMilleStreaming PUBLIC
  ${CMAKE_CURRENT_SOURCE_DIR}/include
  ${CMAKE_CURRENT_SOURCE_DIR}/../../localDetEICrecon/experimental/telescope/alignment)
target_link_libraries(TelescopeMilleStreaming PUBLIC
  TelescopeMilleSchema
  TelescopeZmqTransport
  Mille2
  flatbuffers::flatbuffers_shared
  nlohmann_json::nlohmann_json
  Threads::Threads)

add_executable(TelescopeMilleCollector tools/TelescopeMilleCollector.cc)
set_target_properties(TelescopeMilleCollector PROPERTIES
  RUNTIME_OUTPUT_DIRECTORY ${CMAKE_BINARY_DIR})
target_link_libraries(TelescopeMilleCollector PRIVATE TelescopeMilleStreaming)

if(BUILD_TESTING)
  add_executable(TelescopeMilleStreamingTest
    tests/TelescopeMilleStreamingTest.cc)
  target_link_libraries(TelescopeMilleStreamingTest PRIVATE
    TelescopeMilleStreaming)
  add_test(NAME TelescopeMilleStreamingTest COMMAND TelescopeMilleStreamingTest)
endif()
