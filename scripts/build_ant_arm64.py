#!/usr/bin/env python3
"""Build an arm64 ANT libant.dylib from a local ANT-SDK_Mac.3.5 source tree.

Usage: python3 build_ant_arm64.py SDK_DIRECTORY OUTPUT_DIRECTORY
The SDK remains in place. This builds only the ARM library; it does not alter
MyWhoosh or package the Intel slice.
"""

import json
from pathlib import Path
import subprocess
import sys


def run(command):
    subprocess.run(command, check=True)


def main():
    if len(sys.argv) != 3:
        raise SystemExit(__doc__)

    root = Path(sys.argv[1]).resolve()
    output = Path(sys.argv[2]).resolve()
    project_file = root / "ANT_Libraries.xcodeproj/project.pbxproj"
    project = json.loads(
        subprocess.check_output(["plutil", "-convert", "json", "-o", "-", str(project_file)])
    )["objects"]

    # antbase and ant DLL source phases in the SDK's supplied Xcode project.
    target_ids = ["52BFFF92112E06590093FB49", "520968561145BCCB0052AB0C"]
    sources = []
    for target_id in target_ids:
        target = project[target_id]
        for phase_id in target["buildPhases"]:
            phase = project[phase_id]
            if phase["isa"] != "PBXSourcesBuildPhase":
                continue
            for build_id in phase["files"]:
                reference = project[project[build_id]["fileRef"]]
                name = reference.get("path", reference.get("name"))
                matches = list(root.rglob(name))
                if len(matches) != 1:
                    raise RuntimeError(f"{name}: expected one source, found {len(matches)}")
                source = matches[0]
                if source.name != "dsi_thread_win32.c":
                    sources.append(source)

    # This implementation is referenced by other source but omitted from the
    # supplied Xcode target's source phase.
    sources.append(root / "ANT_LIB/software/serial/device_management/dsi_ant_device_polling.cpp")

    output.mkdir(parents=True, exist_ok=True)
    include_dirs = [root / "ANT_DLL"] + [p for p in (root / "ANT_LIB").rglob("*") if p.is_dir()]
    include_args = [f"-I{path}" for path in include_dirs]
    objects = []
    for index, source in enumerate(sources):
        obj = output / f"{index:02d}-{source.stem}.o"
        compiler = "clang++" if source.suffix == ".cpp" else "clang"
        print(f"Compiling {source.relative_to(root)}", flush=True)
        run([compiler, "-arch", "arm64", "-mmacosx-version-min=11.0", "-fPIC", "-O2", "-w",
             "-c", str(source), "-o", str(obj), *include_args])
        objects.append(str(obj))

    library = output / "libant.dylib"
    run(["clang++", "-arch", "arm64", "-dynamiclib", "-mmacosx-version-min=11.0",
         "-Wl,-install_name,@rpath/libant.dylib", "-o", str(library), *objects,
         "-framework", "IOKit", "-framework", "CoreFoundation"])
    print(f"Built {library}")


if __name__ == "__main__":
    main()
