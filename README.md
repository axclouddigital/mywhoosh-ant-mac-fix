# ANT+ on macOS for MyWhoosh

This installer makes a separate, signed copy of **MyWhoosh 6.2.0 build 1240** that can use an ANT+ USB stick on Apple Silicon. It leaves the App Store app untouched and does not include MyWhoosh or Garmin binaries.

| MyWhoosh | Mac | Status |
| --- | --- | --- |
| 6.2.0 (1240) | Apple Silicon | Tested working |
| 6.2.0 (1240) | Intel | Not fixed by this patch |
| Other builds | Any | Untested; installer refuses them |

## Install

You need MyWhoosh 6.2.0 build 1240, an ANT+ USB stick, Python 3.9 or newer, and Apple's Xcode command line tools. Download Garmin's **ANT Mac SDK with source code** from [Garmin](https://developer.garmin.com/ant-program/downloads/); its terms may require acceptance.

Clone this repository and pass either the downloaded SDK ZIP or its extracted folder:

```sh
git clone https://github.com/axclouddigital/mywhoosh-ant-mac-fix.git
cd mywhoosh-ant-mac-fix
python3 scripts/install_mywhoosh_ant.py "/path/to/ANT_Libraries.zip"
```

The installer finds the unique `ANT_Libraries.xcodeproj/project.pbxproj` inside the ZIP or folder, including Garmin's nested `ANT_Libraries/ANT-SDK_Mac.3.5/` layout. It builds the Apple Silicon library, adds USB permission, patches the verified game executable, and signs a copy at `/Applications/MyWhoosh ANT+ 6.2.0.app`. It refuses a different game build or an existing output. Use `--app` and `--output` to choose different paths.

Open the new app, sign in, turn on **Settings → Equipment → ANT+**, save, then check **Ride → Power Source → Available Devices**. The ANT+ icon should be active and nearby devices should appear. Close other software using the stick if no devices appear.

To check packaging:

```sh
APP="/Applications/MyWhoosh ANT+ 6.2.0.app"
codesign --verify --deep --strict --verbose=2 "$APP"
lipo -archs "$APP/Contents/UE/MyWhoosh/Binaries/Mac/libant.dylib"
```

The library should report `x86_64 arm64`. These checks confirm the signature and library slices; seeing the active ANT+ icon and a discovered device confirms operation.

## What changed

In 6.2.0, the bundled ANT library is Intel-only and in an outdated folder. The game also skips the call that starts its ANT connector, even when ANT is enabled. The installer builds a native library and changes 16 bytes in the verified Apple Silicon executable to call the existing ANT startup routine. It does not patch Intel code.

The original USB-entitlement workaround and screenshots are in the [first repository version](https://github.com/axclouddigital/mywhoosh-ant-mac-fix/tree/b6a3b92). This fix applies only to build 1240; updates need a fresh binary check. The Garmin SDK stays on your Mac; follow its [license](https://developer.garmin.com/ant-program/licensing/shared-source-license/).
