# Copyright 2026 Free Software Foundation, Inc.
#
# This file is part of GNU Radio
#
# SPDX-License-Identifier: GPL-3.0-or-later
#
# Installs a native macOS application bundle ("<NAME>.app") that launches a
# GNU Radio program (GNU Radio Companion) from the surrounding install tree:
#
#   <NAME>.app/Contents/Info.plist
#   <NAME>.app/Contents/PkgInfo
#   <NAME>.app/Contents/MacOS/<EXECUTABLE>      launcher script
#   <NAME>.app/Contents/Resources/<ICON>.icns   Dock / Finder icon
#
# The bundle is installed under GR_MACOS_APP_DESTINATION, relative to
# CMAKE_INSTALL_PREFIX: the install prefix itself for installer builds
# (GR_BUILD_INSTALLER, which is what `cpack -GDragNDrop` packages) and
# "Applications" otherwise.

if(NOT APPLE)
    return()
endif()

option(ENABLE_MACOS_APP_BUNDLE "Install GNU Radio Companion as a macOS .app bundle" ON)

function(gr_install_macos_app_bundle)
    set(options)
    set(one_value_args NAME IDENTIFIER PROGRAM ICON)
    cmake_parse_arguments(APP "${options}" "${one_value_args}" "" ${ARGN})

    foreach(required NAME IDENTIFIER PROGRAM ICON)
        if(NOT APP_${required})
            message(FATAL_ERROR "gr_install_macos_app_bundle: ${required} is required")
        endif()
    endforeach()
    if(NOT EXISTS "${APP_ICON}")
        message(FATAL_ERROR "gr_install_macos_app_bundle: icon ${APP_ICON} not found")
    endif()

    if(NOT GR_MACOS_APP_DESTINATION)
        if(GR_BUILD_INSTALLER)
            set(GR_MACOS_APP_DESTINATION ".")
        else()
            set(GR_MACOS_APP_DESTINATION "Applications")
        endif()
    endif()

    get_filename_component(icon_name "${APP_ICON}" NAME_WE)

    set(GR_MACOS_APP_NAME "${APP_NAME}")
    set(GR_MACOS_APP_IDENTIFIER "${APP_IDENTIFIER}")
    set(GR_MACOS_APP_PROGRAM "${APP_PROGRAM}")
    set(GR_MACOS_APP_ICON_NAME "${icon_name}")
    set(GR_MACOS_APP_EXECUTABLE "${APP_NAME}")
    set(GR_MACOS_APP_VERSION "${GR_VERSION_MAJOR}.${GR_VERSION_API}.${GR_VERSION_MINOR}")
    set(GR_MACOS_APP_BUILD_VERSION "${GR_VERSION_MAJOR}.${GR_VERSION_API}.${GR_VERSION_MINOR}.${GR_VERSION_MAINT}")
    if(CMAKE_OSX_DEPLOYMENT_TARGET)
        set(GR_MACOS_APP_MIN_SYSTEM_VERSION "${CMAKE_OSX_DEPLOYMENT_TARGET}")
    else()
        set(GR_MACOS_APP_MIN_SYSTEM_VERSION "12.0")
    endif()
    if(NOT CURRENT_YEAR)
        string(TIMESTAMP CURRENT_YEAR "%Y")
    endif()
    set(GR_MACOS_APP_COPYRIGHT "Copyright © 2009-${CURRENT_YEAR} Free Software Foundation, Inc.")

    set(resource_dir "${PROJECT_SOURCE_DIR}/release/resources/macos")
    set(staging "${CMAKE_BINARY_DIR}/macos-app")
    file(MAKE_DIRECTORY "${staging}")

    configure_file("${resource_dir}/Info.plist.in" "${staging}/Info.plist" @ONLY)
    configure_file("${resource_dir}/launcher.sh.in" "${staging}/${GR_MACOS_APP_EXECUTABLE}" @ONLY)
    file(WRITE "${staging}/PkgInfo" "APPL????")

    set(contents "${GR_MACOS_APP_DESTINATION}/${APP_NAME}.app/Contents")
    install(FILES "${staging}/Info.plist" "${staging}/PkgInfo" DESTINATION "${contents}")
    install(PROGRAMS "${staging}/${GR_MACOS_APP_EXECUTABLE}" DESTINATION "${contents}/MacOS")
    install(FILES "${APP_ICON}" DESTINATION "${contents}/Resources")

    message(STATUS "Creating install rules for ${APP_NAME}.app in ${GR_MACOS_APP_DESTINATION}")
endfunction()
