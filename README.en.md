# Ge Launcher

[简体中文](README.md) | English

A minimal Minecraft: Java Edition launcher made for a few friends. Python 3.11+ / CustomTkinter
(`pip install -r requirements.txt`) — no build step required.

Supports Vanilla / Fabric / Forge / NeoForge and offline accounts (Microsoft accounts are pending
Mojang approval and not usable yet). It can take over an existing `.minecraft` created by HMCL,
PCL2, or the official launcher.

Design notes (Chinese): [简化启动器方案.md](简化启动器方案.md).

## Features

### Launching & instances

- Parses version JSON, merges `inheritsFrom`, filters libraries by rules, extracts natives,
  and builds the `java` command line
- **An instance is a folder**: `versions/<instance>/`, with the real game version stored in
  `clientVersion`; duplicate instance names are rejected
- Version isolation by default: mods, saves, and options.txt live inside the instance folder
- Per-instance settings: memory, Java, window size, log window, JVM arguments, process priority
- Instance settings can be reset to global defaults

### Loader installation

- Fabric / Forge / NeoForge, always installed into the existing instance (same idea as HMCL patches)
- Pick a loader version from a list before installing
- Forge / NeoForge install through `install_profile` / processors and do not rely on the installer's
  `--installClient`
  - Legacy Forge (1.7.10 / 1.12.2) also installs through `install_profile`
  - The original instance JSON is backed up as `<id>.json.vanilla-backup`; "uninstall loader"
    restores it
  - Only one loader per instance at a time
- An installer JAR is executed only after its **sha1 checksum is verified**. If no checksum is
  available, the launcher falls back to profile-based installation instead of running the JAR

### Modpack import

- Modrinth `.mrpack`, CurseForge `manifest.json`, MCBBS, MultiMC/Prism, HMCL exports, and plain
  `.minecraft`-style zips
- Detects game version and loader automatically, then installs vanilla, installs the loader,
  downloads manifest files, and extracts overrides
- The detected game version is read-only by default; click "Edit" if detection is wrong
- CurseForge resolves through `mod.mcimirror.top` by default (no API key). You can also configure
  `GE_CURSE_API_KEY` or `curse_api_key` to use the official API

### Java

- Picks Java according to the version's `javaVersion` requirement; old versions that require
  Java 8 will not accidentally use Java 17/25
- **Scans local Java before every launch**, including runtimes installed by the launcher itself
- If no suitable Java is available, offers to download Temurin JDK automatically
- Downloaded Java lives in `%APPDATA%\GeLauncher\java\`, not inside the game directory
- Supports system proxies (`HTTP_PROXY` / `HTTPS_PROXY` / `ALL_PROXY` / `NO_PROXY`, plus the
  Windows system proxy settings)

### UI

- CustomTkinter with three pages: Versions / Accounts / Settings
- Light / Dark / Follow system themes
- Custom title bar with a high-DPI icon, adjustable fonts, minimize / close, and dragging
- Instance details are split into Instance Settings / Auto Install / Browse / Manage, with a
  scrollable right-hand panel
- Settings and instance settings share the same row layout; choice-only dropdowns are read-only

### Reliability & security

- sha1 verification for all downloads, multi-source fallback, `.part` temp files, and a 4 GB
  per-file limit
- Installer JARs must pass sha1 verification before execution
- Download and extraction size/file-count limits to prevent zip bombs and disk exhaustion
- `--accessToken` is redacted in log windows
- Corrupt `config.json` is backed up as `.corrupt-<timestamp>` instead of being silently reset
- Windows single-instance lock so two launchers cannot overwrite configs or install into the same
  instance concurrently
- Install/import tasks can be cancelled; partial files created by the task are cleaned up
- If the game fails to start or exits with a non-zero code, a dialog appears with access to the log

## Quick start

```bash
# List versions in a game directory
python -m gelauncher versions --game-dir "D:\Minecraft\.minecraft"

# Open the GUI (or double-click 启动界面.bat in the repo root)
python -m gelauncher ui

# Environment check (Java / assets / libraries)
python -m gelauncher doctor
```

Common commands:

```bash
python -m gelauncher install --version-id 1.21.8 --game-dir <dir>
python -m gelauncher install --version-id 1.21.8 --instance-name Survival --game-dir <dir>
python -m gelauncher install-loader --loader fabric --mc 1.21.8
python -m gelauncher install-loader --loader forge  --mc 1.21.8
python -m gelauncher install-loader --loader neoforge --mc 26.3
python -m gelauncher remove-loader  --loader fabric --mc 1.21.8
python -m gelauncher import-modpack --file pack.mrpack --instance-name MyPack
python -m gelauncher launch --version-id 1.21.8 --dry-run
python -m gelauncher launch --version-id 1.21.8
```

## Directory layout

- **Version isolation**: the game working directory defaults to `versions/<id>/`
- The `.minecraft` root only contains `libraries/`, `assets/`, and `versions/`
- Launcher config: `%APPDATA%\GeLauncher\config.json`
- Auto-installed Java: `%APPDATA%\GeLauncher\java\`
- Game log: `<game dir>/logs/gelauncher-<instance>.log` (rotated to `.1` at 20 MB)

## Accounts

- **Offline account**: only letters, digits, and underscores, up to 16 characters.
  UUID is derived from `md5("OfflinePlayer:<name>")`
- **Microsoft accounts are not usable yet.** The login code is implemented, but Mojang approval is
  still pending. Please use an offline account for now.
  See [msa-setup.md](msa-setup.md) (Chinese)

> Account tokens are stored only in the local `config.json` and are never uploaded.
> Do not share your `config.json` with others.

## FAQ

**The game is stuck at `Atlas Stitching ... shulker_boxes.png`**

Wait 2–3 minutes first. The first launch stitches texture atlases for every mod and can be very
slow on HDDs or while antivirus scans mod jars. If the log stops growing entirely, try removing
suspicious mods (e.g. Xaero's mods) and update your GPU driver.

**Old versions (1.12.2, etc.) crash with `ClassCastException: AppClassLoader`**

That means Forge is running on Java 9+. The launcher automatically chooses Java 8 for versions that
require it; you can also set Java 8 manually in the instance settings.

**Forge installation says "cannot get installer sha1"**

For safety, the launcher refuses to execute installer JARs without a checksum. If the version ships
an `install_profile`, the launcher automatically uses profile-based installation instead. Otherwise
check whether your network can reach BMCLAPI.

**Downloads hang / I need a proxy**

Set `HTTPS_PROXY` / `HTTP_PROXY`, or rely on the Windows system proxy. Use `NO_PROXY` to exclude
local hosts.

**Windows SmartScreen warns on first run**

Release builds are not code-signed. Click "More info → Run anyway". See
[packaging/signing.md](packaging/signing.md).

## Known limitations

- **Microsoft accounts are not supported yet** (waiting for Mojang approval)
- Installing very old Forge versions depends on `install_profile` inside the installer. If an
  installer has neither a usable profile nor a verified checksum, only already-installed instances
  can be launched
- The official CurseForge API is blocked by Cloudflare on some networks; the third-party proxy
  `mod.mcimirror.top` is used by default and its availability is outside this project's control
- No multiplayer hosting, mod management, skins, or auto-update
- Automatic Java installation is Windows x64 only

## Development

```bash
python -m pip install -r requirements.txt   # runtime dependency (customtkinter)
python -m pip install pytest                # test dependency
python -m pytest                            # 181 tests
```

Code structure:

```
gelauncher/
├── core/                    # UI-free core, does not import tkinter
│   ├── cancel.py            # cooperative cancellation (CancelToken)
│   ├── java.py              # locate Java, choose by version
│   ├── java_scan.py         # scan system/launcher Java, deduplicate
│   ├── java_install.py      # download Temurin JDK (sha256 verified)
│   ├── forge_processors.py  # Forge/NeoForge processors and install_profile
│   ├── install_loader.py    # Fabric / Forge / NeoForge installation
│   ├── install_vanilla.py   # vanilla installation
│   ├── modpack.py           # modpack detection and import
│   ├── models.py            # version JSON → dataclasses
│   ├── layout.py            # .minecraft directory conventions
│   ├── rules.py             # rules[] evaluation
│   ├── libraries.py         # Maven coordinates / classpath / natives
│   ├── inherit.py           # inheritsFrom merging
│   ├── natives.py           # natives extraction (zip-slip protected)
│   ├── command.py           # placeholders / offline UUID
│   ├── net.py               # downloader: retries / mirrors / sha1 / proxy / size limits
│   ├── process_priority.py  # Windows process priority
│   ├── repair.py            # repair missing files
│   ├── microsoft.py         # Microsoft device-code login
│   ├── accounts.py          # selected account → launch account
│   ├── logfmt.py            # log4j XML → readable log lines
│   └── launch.py            # checks + command building + process + log pump
├── cli.py                   # command-line entry point
└── ui/                      # CustomTkinter UI
    ├── app.py               # main window / navigation / tasks / single instance
    ├── pages.py             # versions, accounts, settings pages
    ├── titlebar.py          # custom title bar
    ├── winframe.py          # native caption removal / high-DPI icon
    ├── theme.py             # themes and palette
    ├── instance_list.py     # canvas-drawn list
    ├── scrollbar.py         # lightweight scrollbar
    ├── widgets.py           # Card/Combo/label/button/Dialog
    ├── confirm.py           # danger confirmation dialog
    ├── single_instance.py   # single-instance lock
    ├── log_window.py        # standalone log window
    ├── install_dialog.py / loader_dialog.py / import_dialog.py
    ├── offline_dialog.py / msa_login.py
    └── dpi.py               # DPI awareness
```

## Packaging

```bash
powershell -ExecutionPolicy Bypass -File packaging/build.ps1
```

The output is a directory distribution: `dist/GeLauncher/GeLauncher.exe` (ship `_internal/` with it).

- Icon: `packaging/GeLauncher.ico` is generated from `icon瓜.png` (16–256 px, nearest-neighbor),
  regenerate with `python packaging/make_icon.py`
- Release zips are uploaded to GitHub Releases together with `SHA256SUMS.txt`
- Code signing: see [packaging/signing.md](packaging/signing.md)

## Progress

- [x] M0 skeleton + fixtures + pytest
- [x] M1 offline launch of Vanilla / Fabric / Forge
- [x] M2 vanilla installation (manifest + mirrors + sha1)
- [x] M3 Fabric (meta profile)
- [x] M4 Forge / NeoForge (processors / install_profile)
- [x] M5 UI (CustomTkinter)
- [x] M5.5 themes, cancellation, auto Java, preloading
- [x] M6 packaging & distribution
- [x] M7 single instance, system proxy, download limits, log rotation, security checks

## Disclaimer

This is an **unofficial** tool. It is not affiliated with or endorsed by Mojang Studios or Microsoft.

- It does not contain or distribute any Minecraft game files
- You must own Minecraft: Java Edition to use it
- Downloaded metadata, libraries, and assets come from public APIs and remain the property of their
  respective owners
- Follow the [Minecraft EULA](https://www.minecraft.net/en-us/eula) and Microsoft's terms

## License

[MIT](LICENSE)
