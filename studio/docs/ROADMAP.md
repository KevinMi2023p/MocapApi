# Roadmap and parity policy

Mocap Studio follows a provider-first roadmap. A feature is complete only when its data
source, failure mode, UI state, tests, and platform support are all explicit.

## Pass 1 — operator console baseline

- [x] Original Axis-inspired desktop workspace and procedural 3D skeleton
- [x] Demo provider for macOS/Linux smoke testing
- [x] Standard BVH string receiver
- [x] Published 64-byte binary receiver
- [x] UDP/TCP profiles and rotation/unit controls
- [x] Entity tree, sensor map, inspector, events and diagnostics
- [x] Capability-gated capture/calibration/record controls
- [x] Crash-recoverable local take format
- [x] Per-user curl installer and release bundle checks
- [x] Protocol, state, recorder, HTTP, UI and build tests

## Pass 2 — live interoperability matrix

- [x] Add a provenance-backed parity/action ledger and deterministic open-source visual fixtures
- [x] Add Capture/Edit/Project navigation and an explicitly local take-library surface
- [x] Add selectable one-to-four-panel live viewports in the documented toolbar order
- [x] Move suit operations right and recording into lower-right Take Information
- [ ] Capture private, fixed-version Windows reference states and record measured placement deltas
- [ ] Validate string and both published 64-byte binary header generations against captured Axis Studio 2/3 data
- [ ] Validate 1–4 avatars, all six rotations, displacement modes, UDP loss and TCP reconnect
- [ ] Validate the attached Windows VM path on hardware: transceiver VID:PID capture, viewport under llvmpipe, end-to-end BVH stream
- [ ] Add saved connection profiles and automatic reconnect with bounded backoff
- [ ] Add BVH file playback and deterministic regression fixtures
- [ ] Add local BVH/CSV export with coordinate/unit presets
- [ ] Add long-running ingestion and recording soak tests

## Pass 3 — optional MocapApi runtime

- [ ] Obtain an explicit upstream source/binary license or written redistribution permission
- [ ] Pin one internally consistent header/runtime bundle by version and SHA-256
- [ ] Runtime-load the C proc-table ABI on supported Linux architectures
- [ ] Add Avatar/Calc/sensor/rigid-body/tracker/marker events
- [ ] Correlate concurrent command handles through Response → Running → Result
- [ ] Drive calibration pose/countdown/progress from live results
- [ ] Validate Axis-side recording and take notifications
- [ ] Build an opt-in Windows/Linux relay for command access from macOS

## Explicit non-goals without a new implementation or license

- Direct Noitom sensor pairing, radio management, firmware, or activation
- Noitom sensor fusion, anti-magnetic solving, or body reconstruction
- Axis project creation/repair or `.mbx` editing
- Axis contact/data processing and solver parameters
- Native Axis FBX/USD/export parity
- Copying Noitom branding, UI assets, meshes, screenshots, or animations
