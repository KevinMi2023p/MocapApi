# Mocap Studio architecture

Mocap Studio is an independently authored operator console for solved motion data.
It is not a sensor driver or a reimplementation of Noitom's proprietary solver.

```text
Noitom sensors
      │ proprietary radio, pairing, calibration and fusion
      ▼
Axis Studio on Windows
      │ standard BVH over UDP/TCP       optional MocapApi command/runtime path
      ├──────────────────────────────┐  ┌──────────────────────────────────┐
      ▼                              │  ▼                                  │
BVH provider (macOS/Linux)           │  MocapApi provider (Linux, optional)│
      └──────────────────────────────┴──────────────┬───────────────────────┘
                                                    ▼
                                      normalized immutable scene frames
                                                    │
                             ┌──────────────────────┼─────────────────────┐
                             ▼                      ▼                     ▼
                    3D/telemetry UI        local take recorder      diagnostics
```

## Process boundary

The application is a loopback-only Python service plus a bundled web interface.
This choice gives macOS and Linux the same rendering and interaction model without
requiring Qt, Electron, Node, or a compiler on the user's machine. Python 3.10 or
newer is the only runtime dependency.

The service exposes:

- `GET /api/state` for an immutable snapshot;
- `GET /api/events` for a same-origin server-sent event stream;
- `POST /api/connect` and `POST /api/disconnect`;
- `POST /api/commands` for capability-checked actions;
- bundled, content-addressed frontend assets.

It binds only to `127.0.0.1`, rejects non-loopback `Host` headers, does not enable
CORS, limits JSON bodies to 64 KiB, adds a restrictive Content Security Policy, and
never accepts arbitrary output paths through the API.

## Provider contract

Every data source reports capabilities independently of its connection state:

| Capability | Demo | Standard BVH | MocapApi runtime |
| --- | ---: | ---: | ---: |
| Solved avatar motion | yes | yes | yes |
| Raw sensor/Calc telemetry | simulated | no | runtime/version dependent |
| Capture/zero commands | simulated | no | runtime/version dependent |
| Calibration command/progress | simulated | no | runtime/version dependent |
| Axis-side recording | simulated | no | runtime/version dependent |
| Crash-safe local recording | yes | yes | yes |

The UI must bind control availability to these flags. It must never make an
unsupported action look successful.

### Demo provider

Generates a deterministic original procedural skeleton and telemetry. It exists for
installation verification, UI development, screenshots, tests, and evaluation without
a suit or Windows host. Demo results are labeled as simulated.

### BVH provider

Receives Noitom's published standard BVH stream directly on macOS or Linux. It supports:

- UDP listener and TCP client transports;
- documented string frames ending in `||`;
- both published 64-byte legacy binary header layouts;
- packets split or coalesced by TCP;
- 59-joint legacy and 60-joint current public hierarchies;
- with-displacement and root-displacement-only layouts;
- all six Euler channel orders;
- multiple avatar indices;
- bounded buffers and finite-number validation.

The current/new binary format is intentionally rejected until a legal, representative
fixture set is available. The UI asks users to select **String** or **Binary with old
frame header** in Axis Studio.

### MocapApi provider

This is optional. The upstream repository currently has no macOS desktop library,
contains inconsistent header/binary generations, and has no redistribution license.
An adapter may load a user-supplied, version-pinned runtime at execution time, but
release bundles must not contain upstream headers or binaries unless Noitom grants
explicit permission covering both.

## Scene model

Provider-specific handles and packet layouts stop at the provider boundary. The UI sees
plain avatars, joints, rigid bodies, trackers, sensors, timestamps, and diagnostics.
Positions are meters; rotations are normalized `x/y/z/w` quaternions; time is UTC plus
a monotonic arrival clock for rate and jitter calculations.

I/O workers never touch UI state directly. The controller keeps only the latest frame
per avatar, publishes at a bounded display cadence, and gives local recording its own
append-only path.

## Local take format

The first local format is deliberately simple and documented:

```text
<take>/
  manifest.json
  frames.ndjson
```

During capture the files end in `.partial` and a `.recording` marker is present. Data is
flushed and `fsync`ed in bounded chunks. Completion atomically renames the frame stream,
writes the final manifest, and then removes the marker. Startup recovers interrupted
takes without overwriting a completed directory.

This format is not `.mbx` and does not claim compatibility with Axis project folders.

## Packaging boundary

Release archives are built from an explicit allow-list containing only `studio/` source,
the compiled frontend, launcher metadata, and the Apache-2.0 license for newly authored
work. The root MocapApi tree, native libraries, headers, samples, and Noitom assets are
excluded. This boundary remains mandatory until upstream licensing is resolved.
