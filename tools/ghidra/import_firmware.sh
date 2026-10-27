#!/usr/bin/env bash
# Import a decoded Mono Station application image into a Ghidra project, headless.
#
# Usage: GHIDRA_HOME=/opt/ghidra tools/ghidra/import_firmware.sh [image.bin] [project_dir] [base]
#
# Defaults: image = firmware/monostation-firmware-800.bin, project = ./ghidra-project (git-ignored),
# base = 0x0800C000. For the bootloader dump: firmware/bootloader-740.bin ghidra-project 0x08000000.
# Produces the raw-image analysis DB; the DB itself is not committed, only this script and the
# post-script are. Re-run after decoding a new build.
set -euo pipefail

: "${GHIDRA_HOME:?set GHIDRA_HOME to your Ghidra install directory}"
here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
repo="$(cd "$here/../.." && pwd)"

image="${1:-$repo/firmware/monostation-firmware-800.bin}"
project_dir="${2:-$repo/ghidra-project}"
base="${3:-0x0800C000}"
project_name="monostation"

mkdir -p "$project_dir"

# Cortex-M4, little-endian. Load at the image base; the post-script adds the other regions.
"$GHIDRA_HOME/support/analyzeHeadless" "$project_dir" "$project_name" \
    -import "$image" \
    -processor "ARM:LE:32:Cortex" \
    -loader BinaryLoader \
    -loader-baseAddr "$base" \
    -scriptPath "$here" \
    -preScript MapMemory.java \
    -analysisTimeoutPerFile 1200 \
    -overwrite
