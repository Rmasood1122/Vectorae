cmake_minimum_required(VERSION 3.18)
project(vectorae_native_cpp LANGUAGES CXX)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_POSITION_INDEPENDENT_CODE ON)

add_library(vectorae_core STATIC src/deliverability.cpp src/triage.cpp src/claims.cpp)
target_include_directories(vectorae_core PUBLIC include)
if(NOT MSVC)
  target_compile_options(vectorae_core PRIVATE -Wall -Wextra -Werror)
  # No fused multiply-add contraction: the core's floating-point results must
  # not depend on the compiler version or target CPU, and must match the
  # Python reference (app/agents/reference.py), which never fuses.
  target_compile_options(vectorae_core PRIVATE -ffp-contract=off)
endif()

# The Python module (built by scikit-build-core from pyproject.toml).
if(SKBUILD)
  find_package(Python REQUIRED COMPONENTS Interpreter Development.Module)
  find_package(pybind11 CONFIG REQUIRED)
  pybind11_add_module(vectorae_native_cpp src/bindings.cpp)
  target_link_libraries(vectorae_native_cpp PRIVATE vectorae_core)
  install(TARGETS vectorae_native_cpp LIBRARY DESTINATION .)
else()
  # Plain CMake build: the core plus its tests (ctest).
  enable_testing()
  add_executable(test_core tests/test_core.cpp)
  target_link_libraries(test_core PRIVATE vectorae_core)
  # asserts must stay live in every build type
  target_compile_options(test_core PRIVATE -UNDEBUG)
  add_test(NAME core COMMAND test_core)
endif()
