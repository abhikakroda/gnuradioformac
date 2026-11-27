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
#   <NAME>.app/Contents/MacOS/<EXECUTABLE>      native launcher (embeds Python)
#   <NAME>.app/Contents/Resources/<ICON>.icns   Dock / Finder icon
#   <NAME>.app/Contents/Resources/grc-runtime/  GRC, blocks, PyQt6 and Qt
#                                               (GR_MACOS_BUNDLE_RUNTIME), then
#                                               the bundle is ad-hoc signed
#
# The bundle is installed under GR_MACOS_APP_DESTINATION, relative to
# CMAKE_INSTALL_PREFIX: the install prefix itself for installer builds
# (GR_BUILD_INSTALLER, which is what `cpack -GDragNDrop` packages) and
# "Applications" otherwise.

if(NOT APPLE)
    return()
endif()

option(ENABLE_MACOS_APP_BUNDLE "Install GNU Radio Companion as a macOS .app bundle" ON)
option(GR_MACOS_NATIVE_LAUNCHER "Build the .app launcher as a native executable embedding Python" ON)
option(GR_MACOS_BUNDLE_RUNTIME
    "Copy GRC, its blocks and PyQt6/Qt into the .app and ad-hoc sign it, so it runs without the install tree" ON)
set(GR_MACOS_APP_PYQT_SITE "" CACHE PATH
    "site-packages directory holding PyQt6 to bundle (default: the one PYTHON_EXECUTABLE imports)")
set(GR_MACOS_APP_EXTRA_BLOCKS_DIRS "" CACHE STRING
    "Further GRC block directories to bundle underneath the installed ones (semicolon separated)")

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
    file(WRITE "${staging}/PkgInfo" "APPL????")

    # Preferred executable: a small native launcher that embeds Python, so the
    # running process *is* the bundle (Dock icon/name, Keep in Dock, document
    # events).  It needs the interpreter's headers and shared library; without
    # them the shell-script launcher, which execs python, is used.
    set(native_launcher FALSE)
    if(GR_MACOS_NATIVE_LAUNCHER AND PYTHON_EXECUTABLE)
        execute_process(
            COMMAND "${PYTHON_EXECUTABLE}" -c
                "import sysconfig as s; print(';'.join([s.get_config_var('INCLUDEPY') or '', s.get_config_var('LIBDIR') or '', s.get_config_var('LDVERSION') or '']))"
            OUTPUT_VARIABLE py_build_info
            OUTPUT_STRIP_TRAILING_WHITESPACE
            RESULT_VARIABLE py_build_info_result
            ERROR_QUIET)
        if(py_build_info_result EQUAL 0)
            list(GET py_build_info 0 py_include_dir)
            list(GET py_build_info 1 py_lib_dir)
            list(GET py_build_info 2 py_ldversion)
            if(EXISTS "${py_include_dir}/Python.h" AND EXISTS "${py_lib_dir}/libpython${py_ldversion}.dylib")
                set(native_launcher TRUE)
            endif()
        endif()
    endif()

    set(contents "${GR_MACOS_APP_DESTINATION}/${APP_NAME}.app/Contents")
    install(FILES "${staging}/Info.plist" "${staging}/PkgInfo" DESTINATION "${contents}")
    if(native_launcher)
        set(GR_MACOS_APP_PYTHON "${PYTHON_EXECUTABLE}")
        configure_file("${resource_dir}/launcher.cpp.in" "${staging}/launcher.cpp" @ONLY)
        set(launcher_target gr_macos_app_launcher)
        add_executable(${launcher_target} "${staging}/launcher.cpp")
        target_include_directories(${launcher_target} PRIVATE "${py_include_dir}")
        target_link_directories(${launcher_target} PRIVATE "${py_lib_dir}")
        target_link_libraries(${launcher_target} PRIVATE "python${py_ldversion}")
        # Shared libraries that Python modules dlopen by bare name (GLib for
        # PyGObject, Qt, ...) are found through the executable's rpaths, so
        # list the usual Homebrew/MacPorts locations and the install tree.
        set(launcher_rpath
            "${py_lib_dir}"
            "@executable_path/../../../../lib" "@executable_path/../../../lib"
            "/opt/homebrew/lib" "/usr/local/lib" "/opt/local/lib")
        set_target_properties(${launcher_target} PROPERTIES
            CXX_STANDARD 17
            BUILD_RPATH "${launcher_rpath}"
            INSTALL_RPATH "${launcher_rpath}"
            OUTPUT_NAME "${GR_MACOS_APP_EXECUTABLE}")
        install(TARGETS ${launcher_target} RUNTIME DESTINATION "${contents}/MacOS")
        message(STATUS "Using the native Python launcher for ${APP_NAME}.app")
    else()
        configure_file("${resource_dir}/launcher.sh.in" "${staging}/${GR_MACOS_APP_EXECUTABLE}" @ONLY)
        install(PROGRAMS "${staging}/${GR_MACOS_APP_EXECUTABLE}" DESTINATION "${contents}/MacOS")
        message(STATUS "Using the shell launcher for ${APP_NAME}.app")
    endif()
    install(FILES "${APP_ICON}" DESTINATION "${contents}/Resources")

    if(GR_MACOS_BUNDLE_RUNTIME AND PYTHON_EXECUTABLE)
        set(GR_MACOS_EMBED_SCRIPT "${resource_dir}/embed_runtime.py")
        set(GR_MACOS_EMBED_EXTRA_ARGS "")
        foreach(extra_blocks ${GR_MACOS_APP_EXTRA_BLOCKS_DIRS})
            string(APPEND GR_MACOS_EMBED_EXTRA_ARGS "        --blocks \"${extra_blocks}\"\n")
        endforeach()
        set(GR_MACOS_EMBED_PYQT_ARGS "")
        if(GR_MACOS_APP_PYQT_SITE)
            set(GR_MACOS_EMBED_PYQT_ARGS "        --pyqt-site \"${GR_MACOS_APP_PYQT_SITE}\"")
        endif()
        configure_file("${resource_dir}/embed_runtime.cmake.in" "${staging}/embed_runtime.cmake" @ONLY)
        # Defined after the GRC python/blocks/scripts install rules of the
        # callers, so it runs once those files are in place.
        install(SCRIPT "${staging}/embed_runtime.cmake")
    endif()

    message(STATUS "Creating install rules for ${APP_NAME}.app in ${GR_MACOS_APP_DESTINATION}")
endfunction()
