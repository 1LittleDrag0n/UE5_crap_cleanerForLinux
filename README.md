# UE5_crap_cleaner For Linux

Free up disk space in your Unreal Engine install by removing files for platforms you
don't build for: Android, Windows, macOS, iOS, tvOS, visionOS, HoloLens and Linux ARM64.

On a stock UE 5.8.4 Linux build this freed about **13 GB** and the editor kept working
normally.

![UE Cleaner screenshot](docs/screenshot.png)

## Features

- **Scans** any Unreal Engine folder and shows how much each platform takes up.
- **Tick what to remove.** Every item explains what you lose by removing it.
- **Safe by default.** Files are *moved* to a holding folder first, not deleted. Test
  the editor, then empty the holding folder, or restore everything with one click.
- **Refuses to run while Unreal Editor is open** from the same engine.
- **Never touches** Linux x64 files or engine source code.
- **Native Qt app** that runs on Wayland and X11 and follows your desktop theme.

## Download

Grab `UE_Cleaner-x86_64.AppImage` from the [latest release](../../releases/latest).

```sh
chmod +x UE_Cleaner-x86_64.AppImage
./UE_Cleaner-x86_64.AppImage
```

You can also pass the engine folder directly:

```sh
./UE_Cleaner-x86_64.AppImage ~/UnrealEngine/Linux_Unreal_Engine_5.8.4
```

**Requirements:** x86_64 Linux with glibc 2.34 or newer (most distros from 2021 onward)
and FUSE. If the AppImage won't start, install FUSE (`fuse3` on Arch, `libfuse2` on
Ubuntu/Debian) or run it with `--appimage-extract-and-run`.

## How to use

1. Close Unreal Editor.
2. Open UE Cleaner, click **Browse…** and pick your engine folder (the one that
   contains `Engine/`). It scans automatically.
3. Tick the platforms you don't need. Click a group title to toggle the whole group.
4. Leave **Move to holding folder** selected and click **Clean selected**.
5. Open the editor and package your project once to make sure everything still works.
6. Back in UE Cleaner:
   - all good → **Empty holding folder** to actually free the space
   - something broke → **Restore everything**

The holding folder sits next to your engine as `_slim_trash_<engine folder name>`, on the
same drive, so moving is instant.

## What gets removed

### Other platforms (ticked by default)

| Item | What you lose |
|---|---|
| Android | Packaging games for Android |
| Linux ARM64 | Packaging for ARM Linux devices. Normal x64 Linux is untouched |
| Windows (Win64) | Nothing. A Linux engine can't package Windows builds |
| macOS, iOS, tvOS, visionOS | Packaging for Apple platforms |
| HoloLens | Packaging for HoloLens |

For each platform, only these folders are removed:

- `Engine/Platforms/<Platform>`: platform extensions
- `…/Binaries/<Platform>` and `…/Binaries/ThirdParty/…/<Platform>`: prebuilt binaries
- `…/Intermediate/Build/<Platform>`: prebuilt libraries used for packaging
- `Engine/Source/ThirdParty/…/lib/…/<Platform>`: third-party libraries

Source code folders are never touched, so compiling C++ projects and plugins keeps
working.

### Extras (unticked; read before using)

| Item | What you lose |
|---|---|
| Debug symbols (`*.debug`) | Readable crash callstacks. Keep these if you're chasing crashes |
| Engine cache (`DerivedDataCache`) | Nothing permanent. It rebuilds, but the first launch afterwards is slower |
| Engine logs & crash dumps (`Engine/Saved`) | Old logs and crash reports |
| Templates, samples & feature packs | The New Project browser loses its templates |

## Run from source

```sh
# Arch / CachyOS
sudo pacman -S --needed pyside6
python3 ue_cleaner.py
```

On other distros, install PySide6 from your package manager or with
`pip install PySide6-Essentials`.


### Project layout

```
ue_cleaner.py            the app
packaging/
  AppRun                 AppImage entry point
  ue-cleaner.desktop     app name, icon, category
  ue-cleaner.png         icon
  requirements.txt       pinned PySide6 version
build/, dist/            build output (not committed)
```



## License

This project is licensed under the GPL-3.0. See [LICENSE](LICENSE).

The AppImage bundles third-party software under its own licenses:

- **Qt** and **PySide6**: LGPLv3. Source: [qt.io](https://www.qt.io/download-open-source)
  and [PySide6 on PyPI](https://pypi.org/project/PySide6/)
- **Python**: [PSF License](https://docs.python.org/3/license.html)

## Disclaimer

Not affiliated with or endorsed by Epic Games. Unreal and Unreal Engine are trademarks
of Epic Games, Inc.

This tool moves and deletes files inside your engine folder. It's designed to be safe:
files go to a holding folder first, and only prebuilt platform files are touched. Still,
use it at your own risk and keep a backup of anything you can't re-download.