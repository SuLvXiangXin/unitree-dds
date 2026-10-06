cmake_minimum_required(VERSION 3.16)
project(unitree_dds LANGUAGES CXX)
set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
if(NOT UNITREE_SDK2_ROOT)
  message(FATAL_ERROR "Set UNITREE_SDK2_ROOT to an explicit Unitree SDK2 checkout")
endif()
set(SDK_LIB "${UNITREE_SDK2_ROOT}/thirdparty/lib/${CMAKE_SYSTEM_PROCESSOR}")
foreach(name ddsc ddscxx)
  if(NOT EXISTS "${SDK_LIB}/lib${name}.so")
    message(FATAL_ERROR "Missing ${SDK_LIB}/lib${name}.so")
  endif()
endforeach()
find_package(Threads REQUIRED)
add_library(unitree_dds SHARED endpoint.cpp)
target_include_directories(unitree_dds SYSTEM PRIVATE
  "${UNITREE_SDK2_ROOT}/include" "${UNITREE_SDK2_ROOT}/thirdparty/include"
  "${UNITREE_SDK2_ROOT}/thirdparty/include/ddscxx")
target_link_libraries(unitree_dds PRIVATE "${UNITREE_SDK2_ROOT}/lib/${CMAKE_SYSTEM_PROCESSOR}/libunitree_sdk2.a" "${SDK_LIB}/libddscxx.so" "${SDK_LIB}/libddsc.so" Threads::Threads)
target_link_options(unitree_dds PRIVATE -Wl,--no-undefined)
target_compile_options(unitree_dds PRIVATE -Wall -Wextra -Werror=return-type)
set_target_properties(unitree_dds PROPERTIES BUILD_WITH_INSTALL_RPATH TRUE INSTALL_RPATH "$ORIGIN/lib")
add_custom_command(TARGET unitree_dds POST_BUILD
  COMMAND ${CMAKE_COMMAND} -E make_directory "$<TARGET_FILE_DIR:unitree_dds>/lib"
  COMMAND ${CMAKE_COMMAND} -E copy_directory "${SDK_LIB}" "$<TARGET_FILE_DIR:unitree_dds>/lib"
  COMMAND ${CMAKE_COMMAND} -E copy_directory "${UNITREE_SDK2_ROOT}/licenses" "$<TARGET_FILE_DIR:unitree_dds>/licenses"
  COMMAND ${CMAKE_COMMAND} -E copy "${UNITREE_SDK2_ROOT}/LICENSE" "$<TARGET_FILE_DIR:unitree_dds>/licenses/UNITREE_SDK2_LICENSE")
