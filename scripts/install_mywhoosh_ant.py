#!/usr/bin/env python3
"""Create a local Apple Silicon ANT+ copy of MyWhoosh 6.2.0 build 1240."""

import argparse
import hashlib
import plistlib
import shutil
import stat
import struct
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path
from pathlib import PurePosixPath


DEFAULT_APP = Path("/Applications/MyWhoosh Indoor Cycling App.app")
DEFAULT_OUTPUT = Path("/Applications/MyWhoosh ANT+ 6.2.0.app")
EXECUTABLE = Path("Contents/MacOS/MyWhoosh Indoor Cycling App")
ORIGINAL_SHA256 = "4d4dac569b3176cb24188ce03328eac19d031600d16092f82c7c9ec7fdc02bf8"
PATCH_ADDRESS = 0x1064E80E4
ORIGINAL_INSTRUCTIONS = bytes.fromhex(
    "e8 07 40 f9 08 21 40 f9 08 01 00 b4 01 00 00 14"
)
PATCHED_INSTRUCTIONS = bytes.fromhex(
    "e0 07 40 f9 41 00 80 52 30 00 00 94 07 00 00 14"
)


def run(*command: str) -> None:
    subprocess.run(command, check=True)


def check_source(app: Path) -> None:
    info = plistlib.loads((app / "Contents/Info.plist").read_bytes())
    version = (info.get("CFBundleShortVersionString"), str(info.get("CFBundleVersion")))
    if version != ("6.2.0", "1240"):
        raise ValueError(f"expected MyWhoosh 6.2.0 build 1240; found {version}")
    digest_state = hashlib.sha256()
    with (app / EXECUTABLE).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest_state.update(chunk)
    digest = digest_state.hexdigest()
    if digest != ORIGINAL_SHA256:
        raise ValueError("game executable differs from the tested 6.2.0 build; refusing to patch")


def patch_arm64(executable: Path) -> None:
    """Call the existing protocol-2/ANT start dispatcher from the ANT feature branch."""
    with executable.open("r+b") as stream:
        if stream.read(8) != bytes.fromhex("ca fe ba be 00 00 00 02"):
            raise ValueError("unexpected universal executable header")
        arm_offset = None
        for _ in range(2):
            cpu, _subtype, offset, _size, _align = struct.unpack(">IIIII", stream.read(20))
            if cpu == 0x0100000C:
                arm_offset = offset
        if arm_offset is None:
            raise ValueError("ARM64 slice absent")

        # Tested ARM64 __TEXT,__text: VM 0x100014000 maps to thin-file offset 81920.
        file_offset = arm_offset + 81920 + PATCH_ADDRESS - 0x100014000
        stream.seek(file_offset)
        found = stream.read(len(ORIGINAL_INSTRUCTIONS))
        if found != ORIGINAL_INSTRUCTIONS:
            raise ValueError(f"unexpected instructions at {file_offset:#x}: {found.hex(' ')}")
        stream.seek(file_offset)
        stream.write(PATCHED_INSTRUCTIONS)


def signing_entitlements(app: Path, destination: Path) -> None:
    original = plistlib.loads(
        subprocess.check_output(
            ["codesign", "-d", "--entitlements", ":-", str(app)],
            stderr=subprocess.DEVNULL,
        )
    )
    # Ad hoc signatures cannot claim the App Store developer's team/application IDs.
    entitlements = {
        key: value for key, value in original.items()
        if key.startswith("com.apple.security.")
    }
    if not entitlements.get("com.apple.security.app-sandbox"):
        raise ValueError("source app has no expected sandbox entitlement")
    entitlements["com.apple.security.device.usb"] = True
    destination.write_bytes(plistlib.dumps(entitlements))


def prepare_sdk(sdk: Path, destination: Path) -> Path:
    """Find the unique Garmin SDK root in an extracted directory or ZIP."""
    if sdk.is_dir():
        sdk_root = sdk.resolve()
        projects = list(sdk_root.rglob("ANT_Libraries.xcodeproj/project.pbxproj"))
        roots = {
            project.parent.parent.resolve()
            for project in projects
            if project.resolve().is_relative_to(sdk_root)
        }
        if len(roots) != 1:
            raise ValueError("expected one ANT Mac SDK project in the selected directory")
        return roots.pop()

    if not sdk.is_file() or sdk.suffix.lower() != ".zip":
        raise ValueError("provide the Garmin ANT Mac SDK ZIP or an extracted SDK directory")

    with zipfile.ZipFile(sdk) as archive:
        entries = []
        for info in archive.infolist():
            name = info.filename
            member = PurePosixPath(name)
            if ("\\" in name or member.is_absolute() or ".." in member.parts
                    or (member.parts and ":" in member.parts[0])):
                raise ValueError(f"unsafe path in SDK ZIP: {name}")
            mode = info.external_attr >> 16
            if stat.S_ISLNK(mode):
                raise ValueError(f"symlinks are not supported in SDK ZIP: {name}")
            entries.append((info, member))

        roots = {
            PurePosixPath(*member.parts[:-2])
            for _info, member in entries
            if member.parts[-2:] == ("ANT_Libraries.xcodeproj", "project.pbxproj")
        }
        if len(roots) != 1:
            raise ValueError("expected one ANT Mac SDK project in the selected ZIP")
        prefix = next(iter(roots)).parts
        written = set()
        for info, member in entries:
            if member.parts[:len(prefix)] != prefix:
                continue
            relative = member.parts[len(prefix):]
            if not relative:
                continue
            target = destination.joinpath(*relative)
            if target in written:
                raise ValueError(f"duplicate path in SDK ZIP: {info.filename}")
            written.add(target)
            if info.is_dir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            with archive.open(info) as source, target.open("xb") as output:
                shutil.copyfileobj(source, output)

    project = destination / "ANT_Libraries.xcodeproj/project.pbxproj"
    if not project.is_file():
        raise ValueError("ANT Mac SDK project was not found inside the ZIP")
    return destination


def build_app_copy(source: Path, destination: Path, arm_library: Path, scratch: Path) -> None:
    # APFS copy-on-write cloning avoids writing another 2.5 GB of game data.
    run("cp", "-cR", str(source), str(destination))
    receipt = destination / "Contents/_MASReceipt"
    if receipt.is_dir():
        shutil.rmtree(receipt)

    intel = destination / "Contents/UE4/MyWhoosh/Content/Libraries/Mac/libant.dylib"
    if not intel.is_file():
        raise ValueError("the game's bundled Intel ANT library is missing")
    current = destination / "Contents/UE/MyWhoosh/Binaries/Mac/libant.dylib"
    current.parent.mkdir(parents=True, exist_ok=True)
    run("lipo", "-create", str(intel), str(arm_library), "-output", str(current))
    if set(subprocess.check_output(["lipo", "-archs", str(current)]).decode().split()) != {
        "x86_64", "arm64"
    }:
        raise ValueError("combined ANT library is not universal")

    patch_arm64(destination / EXECUTABLE)
    entitlements = scratch / "entitlements.plist"
    signing_entitlements(source, entitlements)

    for library in sorted((destination / "Contents").rglob("*.dylib")):
        if library.is_file():
            run("codesign", "--force", "--sign", "-", "--timestamp=none", str(library))
    reporter = destination / "Contents/UE4/Engine/Binaries/Mac/CrashReportClient.app"
    if reporter.is_dir():
        run("codesign", "--force", "--sign", "-", "--timestamp=none", str(reporter))
    for code in (destination / EXECUTABLE, destination):
        run("codesign", "--force", "--sign", "-", "--timestamp=none",
            "--entitlements", str(entitlements), str(code))
    run("codesign", "--verify", "--deep", "--strict", "--verbose=2", str(destination))


def install(source: Path, sdk: Path, output: Path) -> None:
    source = source.resolve()
    sdk = sdk.expanduser().resolve()
    output = output.expanduser().absolute()
    # Resolve aliases in the parent before checking that the copy cannot land
    # inside the App Store bundle through a symlinked output directory.
    output = output.parent.resolve() / output.name
    if not source.is_dir():
        raise ValueError(f"source app not found: {source}")
    if output.suffix != ".app" or output == source or output.is_relative_to(source):
        raise ValueError("output must be a separate .app bundle")
    if not output.parent.is_dir():
        raise ValueError(f"output directory does not exist: {output.parent}")
    if output.exists() or output.is_symlink():
        raise ValueError(f"refusing to overwrite: {output}")
    check_source(source)

    with tempfile.TemporaryDirectory(prefix="mywhoosh-ant-") as scratch_name:
        scratch = Path(scratch_name)
        sdk_root = prepare_sdk(sdk, scratch / "sdk")
        library_output = scratch / "build"
        builder = Path(__file__).with_name("build_ant_arm64.py")
        run(sys.executable, str(builder), str(sdk_root), str(library_output))

        # Build beside the requested output so the final rename stays on one volume.
        with tempfile.TemporaryDirectory(prefix=".mywhoosh-ant-", dir=output.parent) as stage_name:
            staged_app = Path(stage_name) / output.name
            build_app_copy(source, staged_app, library_output / "libant.dylib", scratch)
            if output.exists() or output.is_symlink():
                raise ValueError(f"refusing to overwrite: {output}")
            staged_app.rename(output)
    print(f"Ready: {output}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("sdk", type=Path, help="Garmin ANT Mac SDK ZIP or extracted SDK directory")
    parser.add_argument("--app", type=Path, default=DEFAULT_APP, help="original App Store app")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT, help="new app bundle")
    args = parser.parse_args()
    try:
        install(args.app.resolve(), args.sdk.resolve(), args.output.expanduser().absolute())
    except (OSError, subprocess.CalledProcessError, ValueError, zipfile.BadZipFile) as error:
        parser.exit(1, f"Installation stopped: {error}\n")


if __name__ == "__main__":
    main()
