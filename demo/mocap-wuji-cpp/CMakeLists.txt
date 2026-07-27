cmake_minimum_required(VERSION 3.16)
project(mocap_wuji_bridge LANGUAGES CXX)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_CXX_EXTENSIONS OFF)
set(CMAKE_EXPORT_COMPILE_COMMANDS ON)

# MocapApi v73 is bundled with the Python demo, but this target consumes its
# native C++ header and shared library directly.
set(MOCAP_API_ROOT
    "${CMAKE_CURRENT_SOURCE_DIR}/../demo-py/MocapApi/mocap_api"
    CACHE PATH "Root of the bundled MocapApi v73 package")
set(MOCAP_API_INCLUDE_DIR
    "${MOCAP_API_ROOT}/include"
    CACHE PATH "Directory containing MocapApi/MocapApi.h")

string(TOLOWER "${CMAKE_SYSTEM_PROCESSOR}" _mocap_processor)
if(_mocap_processor MATCHES "^(x86_64|amd64)$")
    set(_mocap_arch_dir x86_64)
elseif(_mocap_processor MATCHES "^(aarch64|arm64)$")
    set(_mocap_arch_dir arm64)
else()
    set(_mocap_arch_dir "")
endif()

set(_mocap_default_lib "")
if(_mocap_arch_dir)
    set(_mocap_default_lib
        "${MOCAP_API_ROOT}/lib/${_mocap_arch_dir}/libMocapApi.so")
endif()
set(MOCAP_API_LIB
    "${_mocap_default_lib}"
    CACHE FILEPATH "Path to the native MocapApi v73 shared library")

# Either point at an extracted Wuji C SDK root, or supply both paths
# separately:
#
#   -DWUJI_SDK_ROOT=/path/to/wuji-sdk-c-2026.7.21-x86_64-linux-gnu
#
# or:
#
#   -DWUJI_SDK_INCLUDE_DIR=/path/to/include
#   -DWUJI_SDK_LIB=/path/to/lib/libwuji_sdk_c.so
set(WUJI_SDK_ROOT "" CACHE PATH "Root of an extracted Wuji C SDK release")
set(WUJI_SDK_INCLUDE_DIR "" CACHE PATH "Directory containing wuji_sdk.h")
set(WUJI_SDK_LIB "" CACHE FILEPATH "Path to libwuji_sdk_c.so")

if(NOT WUJI_SDK_INCLUDE_DIR AND WUJI_SDK_ROOT)
    set(WUJI_SDK_INCLUDE_DIR "${WUJI_SDK_ROOT}/include")
endif()
if(NOT WUJI_SDK_LIB AND WUJI_SDK_ROOT)
    set(WUJI_SDK_LIB "${WUJI_SDK_ROOT}/lib/libwuji_sdk_c.so")
endif()

if(NOT EXISTS "${MOCAP_API_INCLUDE_DIR}/MocapApi/MocapApi.h")
    message(FATAL_ERROR
        "MocapApi/MocapApi.h not found under: ${MOCAP_API_INCLUDE_DIR}\n"
        "Set MOCAP_API_ROOT or MOCAP_API_INCLUDE_DIR to the bundled v73 SDK.")
endif()
if(NOT MOCAP_API_LIB OR NOT EXISTS "${MOCAP_API_LIB}")
    message(FATAL_ERROR
        "Native MocapApi v73 library not found: ${MOCAP_API_LIB}\n"
        "Supported bundled architectures are x86_64 and arm64. Set "
        "MOCAP_API_LIB explicitly for another layout.")
endif()
if(NOT WUJI_SDK_INCLUDE_DIR OR
   NOT EXISTS "${WUJI_SDK_INCLUDE_DIR}/wuji_sdk.h")
    message(FATAL_ERROR
        "wuji_sdk.h not found under: ${WUJI_SDK_INCLUDE_DIR}\n"
        "Extract the Wuji C SDK 2026.7.21 tarball and set WUJI_SDK_ROOT, "
        "or set WUJI_SDK_INCLUDE_DIR explicitly.")
endif()
if(NOT WUJI_SDK_LIB OR NOT EXISTS "${WUJI_SDK_LIB}")
    message(FATAL_ERROR
        "libwuji_sdk_c.so not found: ${WUJI_SDK_LIB}\n"
        "Set WUJI_SDK_ROOT or WUJI_SDK_LIB.")
endif()

get_filename_component(_mocap_lib_dir "${MOCAP_API_LIB}" DIRECTORY)
get_filename_component(_wuji_lib_dir "${WUJI_SDK_LIB}" DIRECTORY)
if(NOT EXISTS "${_wuji_lib_dir}/libwujihandcpp.so")
    message(FATAL_ERROR
        "libwujihandcpp.so not found beside libwuji_sdk_c.so in: "
        "${_wuji_lib_dir}\n"
        "Keep both libraries together exactly as shipped in the Wuji C SDK "
        "Linux tarball.")
endif()

find_package(Threads REQUIRED)

add_executable(mocap_wuji_bridge mocap_wuji_bridge.cpp)
target_include_directories(mocap_wuji_bridge SYSTEM PRIVATE
    "${MOCAP_API_INCLUDE_DIR}"
    "${WUJI_SDK_INCLUDE_DIR}"
)
target_link_libraries(mocap_wuji_bridge PRIVATE
    "${MOCAP_API_LIB}"
    "${WUJI_SDK_LIB}"
    Threads::Threads
)

if(CMAKE_CXX_COMPILER_ID MATCHES "GNU|Clang")
    target_compile_options(mocap_wuji_bridge PRIVATE
        -Wall
        -Wextra
        -Wpedantic
    )
endif()

# The build-tree executable can find both SDKs without LD_LIBRARY_PATH.
# libwuji_sdk_c.so resolves libwujihandcpp.so from the same Wuji directory.
set_target_properties(mocap_wuji_bridge PROPERTIES
    BUILD_RPATH "${_mocap_lib_dir};${_wuji_lib_dir}"
    INSTALL_RPATH "${_mocap_lib_dir};${_wuji_lib_dir}"
)

include(CTest)
if(BUILD_TESTING)
    add_test(NAME mocap_wuji_bridge_help
             COMMAND mocap_wuji_bridge --help)
endif()

message(STATUS "MOCAP_API_INCLUDE_DIR = ${MOCAP_API_INCLUDE_DIR}")
message(STATUS "MOCAP_API_LIB         = ${MOCAP_API_LIB}")
message(STATUS "WUJI_SDK_INCLUDE_DIR  = ${WUJI_SDK_INCLUDE_DIR}")
message(STATUS "WUJI_SDK_LIB          = ${WUJI_SDK_LIB}")
