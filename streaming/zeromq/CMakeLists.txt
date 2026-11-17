cmake_minimum_required(VERSION 3.20)

if(CMAKE_SOURCE_DIR STREQUAL CMAKE_CURRENT_SOURCE_DIR)
  project(TelescopeStreamingZeroMQ LANGUAGES CXX)
  set(CMAKE_CXX_STANDARD 20)
  set(CMAKE_CXX_STANDARD_REQUIRED ON)
  find_package(podio CONFIG REQUIRED)
  find_package(EDM4EIC CONFIG REQUIRED)
  find_package(EDM4HEP CONFIG REQUIRED)
  find_package(nlohmann_json CONFIG REQUIRED)
  include(CTest)
endif()

find_package(cppzmq CONFIG REQUIRED)

if(NOT TARGET TelescopeTimeframeSerialization)
  add_subdirectory(
    ${CMAKE_CURRENT_SOURCE_DIR}/../serialization
    ${CMAKE_CURRENT_BINARY_DIR}/serialization)
endif()

add_library(TelescopeZmqTransport STATIC src/TelescopeZmqTransport.cc)
set_target_properties(TelescopeZmqTransport PROPERTIES
  POSITION_INDEPENDENT_CODE ON)
target_include_directories(TelescopeZmqTransport PUBLIC
  ${CMAKE_CURRENT_SOURCE_DIR}/include)
target_link_libraries(TelescopeZmqTransport PUBLIC cppzmq)

add_executable(TelescopeZmqSender tools/TelescopeZmqSender.cc)
target_link_libraries(TelescopeZmqSender PRIVATE
  TelescopeZmqTransport
  TelescopeTimeframeSerialization
  podio::podioRootIO)

add_executable(TelescopeZmqReceiver tools/TelescopeZmqReceiver.cc)
target_link_libraries(TelescopeZmqReceiver PRIVATE
  TelescopeZmqTransport
  TelescopeTimeframeSerialization
  podio::podioRootIO)

if(BUILD_TESTING)
  add_executable(TelescopeZmqTransportTest
    tests/TelescopeZmqTransportTest.cc)
  target_link_libraries(TelescopeZmqTransportTest PRIVATE
    TelescopeZmqTransport
    TelescopeTimeframeSerialization)
  add_test(NAME TelescopeZmqTransportTest COMMAND TelescopeZmqTransportTest)
endif()
