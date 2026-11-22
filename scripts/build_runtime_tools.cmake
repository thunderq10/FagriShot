# SPDX-License-Identifier: GPL-3.0-or-later
# Force the same assembler for the vendored dav1d across clean Windows builds.
cmake_minimum_required(VERSION 3.25)
if(NOT DEFINED DOWNLOAD_DIR)
    message(FATAL_ERROR "Set DOWNLOAD_DIR to the workspace dependency directory")
endif()
file(MAKE_DIRECTORY "${DOWNLOAD_DIR}")
file(LOCK "${DOWNLOAD_DIR}/nasm.lock" GUARD PROCESS TIMEOUT 60)
set(archive "${DOWNLOAD_DIR}/nasm-3.01-win64.zip")
# microsoft/vcpkg scripts/cmake/vcpkg_find_acquire_program(NASM).cmake, blob eda7b5d97fe3c51b47f1058a6ec06df3ec60e028
file(DOWNLOAD "https://www.nasm.us/pub/nasm/releasebuilds/3.01/win64/nasm-3.01-win64.zip"
    "${archive}" TLS_VERIFY ON TIMEOUT 90
    EXPECTED_HASH SHA512=771c238ddb17c98d5736ccaba4ade1d1601d896f09e588489cb43a4f6381bc0ae14d1869f5316fe94f847f54867e65cf12665529b1e7ad88e5e7d3e162719a4f)
file(ARCHIVE_EXTRACT INPUT "${archive}" DESTINATION "${DOWNLOAD_DIR}")
