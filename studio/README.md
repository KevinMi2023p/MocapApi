# Mocap Studio

Mocap Studio is a local, cross-platform operator console for viewing and
recording solved motion broadcast by Axis Studio. It runs on macOS and Linux,
uses a browser for its UI, and keeps recordings on the local machine.

> **Important legal and technical boundary:** the upstream
> [`pnmocap/MocapApi`](https://github.com/pnmocap/MocapApi) tree has no root
> license, so its code and binaries cannot be assumed to be open source or
> redistributable. The upstream tree also provides no macOS `.dylib`. This
> Apache-2.0 project therefore ships no upstream headers, examples, artwork, or
> native libraries. It receives the documented standard BVH network stream
> directly. See [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).

This project takes layout cues from the workflow documented in the Axis Studio
manual—capture controls across the top, scene/sensor navigation, a 3D viewport,
properties, takes, timeline, and diagnostics—but it does not copy Noitom assets
and is not a drop-in replacement for Axis Studio. Axis Studio still owns the
hardware connection, sensor fusion, calibration solver, and data broadcast.

## Install

Requirements are macOS or Linux, Python 3.10+, a modern browser, and `curl` for
remote installation. Installation is per-user and never needs `sudo`.

```sh
curl -fsSL https://raw.githubusercontent.com/KevinMi2023p/MocapApi/refs/heads/codex/mocap-studio/install.sh \
  | sh -s -- --version 0.1.0 --launch
```

The default command location is `~/.local/bin`. When that directory is not
already available, the installer adds an idempotent, clearly marked PATH block
to the current user's zsh, Bash, fish, or POSIX shell startup files. Open a new
terminal before running `mocap-studio`; the `--launch` shown above starts the
first session immediately. Use `--no-modify-path` (or
`MOCAP_STUDIO_NO_MODIFY_PATH=1`) to leave shell files unchanged, in which case
the installer prints the full command path. Custom `--prefix` and `--bin-dir`
locations are never added automatically.

Application versions are stored under `~/.local/share/mocap-studio` on Linux and
`~/Library/Application Support/Mocap Studio` on macOS. Takes use the platform's
normal user-data directory and are never removed by the uninstaller.

Useful installer operations:

```sh
# Install a locally built checkout into a disposable/custom prefix
./install.sh --local . --prefix "$HOME/opt/mocap-studio" --launch

# Override release hosting/repository
./install.sh --repo KevinMi2023p/MocapApi --version 0.1.0
./install.sh --release-base-url https://downloads.example.test/releases --version 0.1.0

# Launch an existing installation, or uninstall with confirmation
./install.sh --launch
./install.sh --uninstall
```

The uninstaller leaves the managed `~/.local/bin` PATH block in place because
that standard per-user command directory may also contain unrelated tools.

The remote installer detects `linux-x86_64`, `linux-aarch64`,
`darwin-x86_64`, or `darwin-arm64`, downloads a versioned `.tar.gz` and its
`.sha256`, verifies the checksum before extraction, rejects unsafe archive
paths, refuses root/sudo use, and will not replace an unmanaged command or an
existing version directory. `--prefix`, `--data-home`, and `--bin-dir` provide
explicit location overrides. `--yes` is accepted only to confirm uninstall in
a non-interactive shell.

## Configure Axis Studio

Axis Studio itself is Windows-only according to the supplied v3 manual. It can
run on a Windows computer while this console runs on another Mac/Linux computer
on the same trusted network.

1. In Axis Studio, open a project, connect the suit, check the Sensor Map, and
   complete posture calibration.
2. Enable **standard BVH – Capture** for live motion, or **BVH – Edit** while
   playing a recorded take. Do not select Advanced BVH, which the manual
   reserves for specific Maya/MotionBuilder plugins.
3. For UDP, configure Axis Studio to send to the Mac/Linux machine's LAN IP and
   chosen port. In Mocap Studio select BVH, UDP, `0.0.0.0`, and the same port
   (the UI defaults to 7012). Allow that inbound UDP port in the host firewall.
4. For TCP, configure Axis Studio's TCP broadcast and enter the Windows host/IP
   and port in Mocap Studio; Mocap Studio acts as the TCP client.
5. Match rotation order and source unit to the Axis broadcast settings, then
   connect. Use a LAN address—not `127.0.0.1`—when the applications are on
   different computers.

Only use motion networks you trust. The web service binds to loopback, rejects
foreign Host headers, and exposes no cross-origin command API, but BVH packets
themselves are not encrypted or authenticated.

## Capability matrix

| Capability | Demo | Standard BVH from Axis | Notes |
| --- | :---: | :---: | --- |
| Live skeleton and multi-actor scene tree | Yes | Yes | Receives multiple solved avatars; the viewport focuses the selected actor. |
| 3D viewport, hierarchy, properties | Yes | Yes | Axis-inspired independent UI. |
| Signal/battery/magnetic sensor telemetry | Simulated | No | Requires Calc/MocapApi runtime support. |
| Capture/zero/calibration/resume commands | Simulated | No | BVH is a downstream motion-only transport. |
| Axis-side recording control | Simulated | No | Requires compatible native MocapApi command support. |
| Crash-recoverable local recording | Yes | Yes | NDJSON take format; original Axis data is untouched. |
| Local take list and diagnostics | Yes | Yes | Includes frame/network health and event history. |
| `.mbx` project browsing/editing | No | No | `.mbx` is proprietary; no project repair API exists. |
| Axis playback, processing, FBX/BVH/CSV export | No | No | Continue to use Axis Studio for these workflows. |
| Direct suit pairing, firmware, sensor fusion | No | No | Remains in Axis Studio on Windows. |

Controls whose provider capability is unavailable are deliberately disabled.
Demo responses prove the UI flow only; they never contact or calibrate hardware.

## Develop and test

```sh
cd studio/frontend
npm ci
npm test -- --passWithNoTests
npm run build

cd ../..
PYTHONPATH=studio/backend python3 -m unittest discover -s studio/backend/tests -v
PYTHONPATH=studio/backend python3 -m mocap_studio
```

The backend has no runtime package dependencies outside Python's standard
library. For a deterministic release archive after building the UI:

```sh
python3 studio/packaging/build_release.py \
  --version 0.1.0 --target linux-x86_64 --output-dir dist
```

The builder has an explicit allow-list. It packages only the new backend,
prebuilt static UI, launcher, README, Apache license, `NOTICE`, and generated
third-party notices. It normalizes
archive order, ownership, modes, timestamps, and gzip metadata, emits a SHA256
file, and refuses to overwrite an asset. It never traverses the upstream
`bin/`, `lib/`, `include/`, `demo/`, or `doc/` trees.

For identical prebuilt UI/backend inputs and the same `SOURCE_DATE_EPOCH`, the
archive assembly is byte-reproducible. CI's double-build check measures this
tar/gzip assembly property; it does not claim that two independent npm builds
produce byte-identical web assets.

Runtime notices are derived from the packages actually present in Vite's
production source maps, checked against `package-lock.json`, and populated with
each installed package's exact license file. After dependency or UI changes,
regenerate and verify them with:

```sh
python3 studio/packaging/generate_third_party_notices.py
python3 studio/packaging/generate_third_party_notices.py --check
```

## Scope, ownership, and trademarks

Mocap Studio is licensed under [Apache-2.0](LICENSE). It covers newly authored
files under `studio/`, the root `install.sh`, and the
`.github/workflows/mocap-studio-*.yml` workflows, as identified by their SPDX
headers and notices. It does not relicense the inherited root README or any
other part of the surrounding upstream snapshot, which remains under its
copyright holder's terms. This is an independent community project, not
affiliated with or endorsed by Noitom Ltd. Axis Studio, Noitom, and Perception
Neuron are used only to identify interoperability targets and may be trademarks
of Noitom Ltd.

The repository is currently owner-maintained. Contributor access is not
offered, and unsolicited pull requests are not promised review or acceptance.

## Primary references

- [Axis Studio customer manual](https://support.noitom.com.cn/s/customer-manual-en/doc/1-software-installation-and-activation-yC49l3MCA2)
- [Noitom software downloads](https://noitom.com/serviceinfo.html?id=1)
- [Official MocapApi reference](https://mocap-api.noitom.com/)
- [`pnmocap/MocapApi` source repository](https://github.com/pnmocap/MocapApi)
- [MocapApi English API document](https://github.com/pnmocap/MocapApi/blob/main/doc/MocapApi_en.md)

The attached 52-page “AXIS STUDIO MANUAL” was also used as a local design and
workflow reference, especially its interface, Sensor Map, take, recording,
project, export, and data-broadcast chapters.
