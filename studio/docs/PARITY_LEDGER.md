# Axis Studio parity and action ledger

This ledger is the implementation contract for an independently authored,
open-source operator console. It records what the reference application shows,
where the control belongs, what the current companion actually does, and which
technical boundary owns the action. A visual match never changes a capability
classification: an unavailable hardware or Axis project operation must remain
disabled and plainly explained.

No Noitom screenshot, icon, character mesh, animation, language pack, executable,
or other proprietary asset is stored in this repository. Reference captures used
for manual comparison stay outside Git.

## Provenance

| Code | Source | Use and limits |
| --- | --- | --- |
| `M:pN` | User-supplied 52-page **AXIS STUDIO Manual**, SHA-256 `95bfce2b9184669155544c25ced112b06ababe152f85f2592187f06ad226c6a9` | `pN` is the printed page number; it is PDF page `N+1`. Text and layout observations only. The PDF is not redistributed. |
| `A:UI` | Read-only inventory of the user-supplied **Axis Studio.zip**, SHA-256 `c97a33cf05715dd2b70cf33daf5ef987a3f3cfb3e9a5223efe6d935e20c018d4` | Axis Studio `3.0.14004.2620`, build `20251222173616363`. The inventory identifies Qt Widgets 5.12.12, OpenSceneGraph 3.6.5, Qwt, UI plugin names, action names, and color values. File names prove that a surface/action exists, but not exact runtime geometry. The archive is never committed or redistributed; controlled runtime captures remain private. |
| `A:Run` | Isolated, network-disabled Wine 9 reference run of the supplied Axis Studio executable, SHA-256 `cae99f31598da3339055fcef8eca98e3f80f45bf1f523588767d60e9dfdc17ee` | Confirms the v3 login window opens when Wine's incomplete `hnetcfg` builtin is disabled. It does not establish login, activation, workspace, hardware, or capture compatibility. Runtime screenshot and logs remain outside Git. |
| `A:Capture` | Archive UI inventory: `uic_page` plus calibration, character, actor, device, and control docks | Confirms the modular Capture surface and dock families. Exact dimensions still require a controlled runtime capture. |
| `A:Edit` | Archive UI inventory: `uie_page`, play dock, and contact-editing resources | Confirms the Edit surface, transport dock, and contact workflow. |
| `A:Project` | Archive UI inventory: `uip_page`, motion-data and body-size resources | Confirms a distinct Project surface and both library types. |
| `A:Settings` | Archive UI inventory: device, solver, language, shortcuts, router, BVH, Calc, and OSC settings | Confirms settings categories, not their safe availability through MocapApi. |
| `A:Toolbar` | Archive action inventory: Connect, Refresh, Power Off, Restore AHRS, Sleep, Zero, LED, Record/End, Save As/Export, transport, one-to-four views, follow, name, and curve | Used to ensure the action list is complete. Runtime capture remains the authority for order and spacing. |
| `C:App` | `studio/frontend/src/App.tsx` and `styles.css` | Current layout and visible control behavior. |
| `C:Dialog` | `studio/frontend/src/components/Dialogs.tsx` | Current connection and calibration flows. |
| `C:Viewport` | `studio/frontend/src/components/SkeletonViewport.tsx` | Current Three.js viewport actions. |
| `C:Providers` | `studio/backend/mocap_studio/providers` and `state.py` | Actual Demo and standard-BVH capabilities. |
| `SDK:Command` | `include/MocapApi.h` `EMCPCommand` and `doc/MocapApi_en.md` | Downstream commands exposed by the supplied API: start/stop capture, zero, calibration, start/stop Axis recording, and resume original posture. Redistribution and runtime compatibility remain unresolved. |
| `SDK:Progress` | `IMCPCalibrateMotionProgress` in the supplied headers/API document | Pose name, prepare/countdown/progress, Next, and Cancel can be provider-driven when a compatible runtime is available. |

Printed manual pages 22–26 document the Capture interface, pages 28–34
document record/edit/project/export, and pages 35–37 document broadcast. Pages
5–7 cover transceiver, network, and sensor connection; pages 10–14 cover the
calibration sequence.

## Classification

| Class | Meaning |
| --- | --- |
| `LIVE` | Works with a non-simulated source today. |
| `DEMO` | UI and action contract exist, but success is simulated by Demo mode. |
| `READ` | Data is shown read-only; the reference editing/action path is absent. |
| `LOCAL` | Missing, but independently implementable without a proprietary format or hardware protocol. |
| `RUNTIME` | Requires a compatible, user-supplied/licensed MocapApi runtime or a separately installed Windows relay. |
| `BLOCKED` | Requires undocumented hardware control, proprietary solving, activation, or `.mbx` project behavior. Keep disabled unless an authorized interface becomes available. |
| `OUT` | Intentionally excluded from this independent companion. |

## Placement deltas that drive the visual work

| Reference zone | Axis evidence | Current companion | Required parity direction |
| --- | --- | --- | --- |
| Workspace navigation | Capture, Edit, and Project are distinct surfaces (`A:Capture`, `A:Edit`, `A:Project`; `M:p28–32`). | Capture/Edit tabs sit in the title bar; project identity is static (`C:App`). | Add the measured Project entry and preserve the three-surface hierarchy. Do not imply `.mbx` editing support. |
| Suit/device actions | Connect, calibrate, restore, LED, sleep, and power actions belong to the Suits/device area (`M:p23`, `A:Toolbar`). | Connect, Capture, Zero, Calibrate, Resume, and Record are centralized in a top command bar. | Move or mirror reference actions into the measured Capture docks. Capability gating remains authoritative. |
| Viewport toolbar | The reference begins with one-to-four panel layouts, then follow and avatar-name controls (`M:p22`, `A:Toolbar`). | One viewport exposes Perspective, Front, Right, Reset, Follow, Labels, and Sensors (`C:Viewport`). | Match reference order and add panel-layout state; retain extra companion tools only in a clearly secondary group. |
| Character/device properties | Body Dimensions and Device Detail are dedicated tabs (`M:p26`, `A:Capture`). | The right pane is a joint/sensor inspector; body dimensions are absent. | Recreate the tab/dock placement. Body-dimension writes remain `BLOCKED`; telemetry can be `READ`/`RUNTIME`. |
| Capture recording | Take Information contains name, notes, record/end, and a clock at the lower right (`M:p26`, `M:p28`, `A:Toolbar`). | Target, name, clock, and Record are in the top command bar; notes are read-only below. | Relocate the recording cluster to the measured Take Information dock and distinguish local versus Axis recording. |
| Edit transport | Playback controls sit at the bottom of Edit (`M:p32`, `A:Edit`). | A bottom Timeline tab has disabled first/play/last controls. | The placement is directionally correct; implement deterministic playback and match the complete measured transport order. |
| Project library | MotionData and BodySize, search, context actions, import/export/delete, and Explorer reveal live in Project (`M:p28–31`, `A:Project`). | No project browser exists. | Add an honest local-library surface first. Never label local NDJSON as an Axis project or `.mbx`. |
| Utility/settings | Device state is upper-right; the hamburger opens Settings (`M:p5–6`). Settings include device/solver/language/shortcuts/router/BVH/Calc/OSC (`A:Settings`). | Upper-right shows downstream connection status and one connection dialog. | Separate application/stream settings from Axis device settings; unavailable device/solver pages remain explanatory and disabled. |

## Global chrome and workspace actions

| ID | Target placement and reference action | Evidence | Current mapping | Class | Acceptance checkpoint |
| --- | --- | --- | --- | --- | --- |
| `NAV-01` | Capture workspace/page | `A:Capture`, `M:p22–28` | Capture title-bar tab | `LIVE` | Runtime capture confirms tab order, dimensions, and selected treatment. |
| `NAV-02` | Edit workspace/page | `A:Edit`, `M:p31–34` | Edit title-bar tab only switches to the placeholder timeline | `READ` | Selecting a take opens Edit; the same selected take drives viewport and transport. |
| `NAV-03` | Project workspace/page | `A:Project`, `M:p28–31` | Static “Live Session” project label | `LOCAL` | A distinct Project surface appears in measured position and labels local versus Axis-owned files. |
| `NAV-04` | Active transceiver/device status in upper-right | `M:p5` | Downstream provider host/port status | `RUNTIME` | The label distinguishes Axis hardware state from companion network-source state. |
| `NAV-05` | Hamburger/settings entry in upper-right | `M:p6`, `A:Settings` | Gear opens Connection & streaming | `LOCAL` | Settings shell matches measured placement; categories declare their owners. |
| `NAV-06` | Connection/activity/error status | `M:p5–7`, `A:Toolbar` | Title, command, status bar, and diagnostics duplicate state | `READ` | One primary status treatment matches reference; diagnostics retain detailed secondary state. |
| `NAV-07` | Application/window styling | `A:UI` | Browser chrome surrounds a dark web shell | `LOCAL` | Native launcher/window work defines title behavior; web content uses measured in-app chrome. |
| `NAV-08` | Dark palette and blue active state | `A:UI` (`#14171b`, `#1a1c21`, `#23262b`, `#404450`, text `#c9c9d8`, blue `#5693e7`) | Similar independent palette with different values | `LOCAL` | Tokens are measured, centrally defined, and checked by screenshots; no proprietary textures are used. |

## Capture, suits, sensor, and viewport actions

| ID | Target placement and reference action | Evidence | Current mapping | Class | Acceptance checkpoint |
| --- | --- | --- | --- | --- | --- |
| `CAP-01` | Connect sensors from the Suits window; sensors remain still during progress | `M:p7`, `M:p23`, `A:Toolbar` | Top Connect configures Demo/BVH downstream source | `BLOCKED` | Rename downstream action clearly; direct sensor Connect is disabled until an authorized device API exists. |
| `CAP-02` | Refresh device/suit state | `A:Toolbar` | No equivalent | `RUNTIME` | Visible in reference position and capability-gated; never fabricates refreshed hardware. |
| `CAP-03` | Start/stop capture | `SDK:Command` | Top Capture; real BVH is receive-only, Demo simulates command | `DEMO` | Optional runtime/relay controls Axis and reports command lifecycle; BVH remains disabled. |
| `CAP-04` | Zero position | `SDK:Command`, `A:Toolbar` | Top Zero, Demo only | `DEMO` | Result/error is correlated to provider command handle. |
| `CAP-05` | Calibrate all active suits | `M:p10–14`, `M:p23`, `SDK:Command` | Top Calibrate opens dialog, Demo only | `DEMO` | Pose/countdown/progress comes from `SDK:Progress`, not a hard-coded success. |
| `CAP-06` | Calibration Next/Cancel | `SDK:Progress` | Dialog actions exist; progress text is a placeholder | `DEMO` | Next is enabled only when provider state permits; cancel/result/error are displayed. |
| `CAP-07` | Resume original hand posture with V-Pose | `M:p23` | No dedicated action | `BLOCKED` | Show only if a documented command appears; do not map it silently to resume whole posture. |
| `CAP-08` | Resume original posture with A-Pose between takes | `M:p23`, `SDK:Command` | Top Resume calls `resume_original_posture`, Demo only | `DEMO` | Runtime command and error/progress states are verified. |
| `CAP-09` | Restore AHRS | `A:Toolbar` | No equivalent | `BLOCKED` | Disabled explanatory control unless an authorized protocol defines it. |
| `CAP-10` | Sensor LED on/off | `M:p23`, `A:Toolbar` | No equivalent | `BLOCKED` | Disabled for unsupported hardware; state must be read back, not optimistically invented. |
| `CAP-11` | Put sensors to sleep | `M:p23`, `A:Toolbar` | No equivalent | `BLOCKED` | Requires explicit confirmation and documented hardware command. |
| `CAP-12` | Power sensors off | `M:p23`, `A:Toolbar` | No equivalent | `BLOCKED` | Requires explicit confirmation and documented hardware command. |
| `CAP-13` | Round signal marker: green/yellow/red/gray | `M:p24` | Sensor map uses round status markers with good/warning/danger/offline | `DEMO` | Threshold provenance is documented; runtime telemetry replaces simulated values. |
| `CAP-14` | Square magnetic marker: green/red/gray | `M:p24–25` | Square marker with steady/unstable/offline semantics | `DEMO` | State has text as well as color and is driven by runtime telemetry. |
| `CAP-15` | Character name field at lower left | `M:p25` | Static avatar name in cards/title; no rename | `RUNTIME` | Placement matches reference; editing requires provider support and a verified result. |
| `CAP-16` | Mocap mode and frame count at lower right | `M:p25` | Working mode disabled in left pane; frame appears in several locations | `READ` | One measured primary placement; provider-owned mode remains read-only. |
| `CAP-17` | Body Dimensions tab with template and ruler | `M:p26`, `A:Capture` | Absent | `BLOCKED` | Read-only/disabled reference surface may explain boundary; no solver dimension write is claimed. |
| `CAP-18` | Device Detail health columns | `M:p26`, `A:Capture` | Sensor inspector bars/facts | `DEMO` | Reposition to measured tab and map only verified runtime fields. |
| `VIEW-01` | Central 3D actor viewport; pan/orbit/zoom | `M:p22`, `A:UI` | Three.js viewport with orbit/pan/zoom | `LIVE` | Mouse mapping and camera movement are verified against the target version. |
| `VIEW-02` | One-panel view | `M:p22`, `A:Toolbar` | Single viewport only | `LIVE` | First layout control selects one panel and is screenshot-covered. |
| `VIEW-03` | Two-panel view | `M:p22`, `A:Toolbar` | Absent | `LOCAL` | Layout and active-view outline match runtime measurement. |
| `VIEW-04` | Three-panel view | `M:p22`, `A:Toolbar` | Absent | `LOCAL` | Layout and active-view outline match runtime measurement. |
| `VIEW-05` | Four-panel view | `M:p22`, `A:Toolbar` | Absent | `LOCAL` | Layout and active-view outline match runtime measurement. |
| `VIEW-06` | Active Follow Cam | `M:p22`, `A:Toolbar` | Follow toggle | `LIVE` | Position/order and active treatment match reference capture. |
| `VIEW-07` | Toggle Avatar Name | `M:p22`, `A:Toolbar` | Labels toggle | `LIVE` | Position/order and visible label behavior match reference capture. |
| `VIEW-08` | Curve display/action | `A:Toolbar` | No equivalent | `LOCAL` | Runtime observation defines its precise surface and semantics before implementation. |
| `VIEW-09` | Reference camera/panel action order | `M:p22`, `A:Toolbar` | Perspective/Front/Right/Reset precede Follow/Labels/Sensors | `LOCAL` | Reference actions lead; companion-only presets move to a secondary, labeled group. |

## Take, Edit, Project, and export actions

| ID | Target placement and reference action | Evidence | Current mapping | Class | Acceptance checkpoint |
| --- | --- | --- | --- | --- | --- |
| `TAKE-01` | Take Information dock shows other takes | `M:p26` | Bottom Takes list | `LIVE` | Dock placement and columns match measurement while retaining local status labels. |
| `TAKE-02` | Editable data/take name | `M:p26`, `M:p28` | Name editable only before local recording; recorded metadata read-only | `LIVE` | Local rename is atomic; Axis rename remains disabled without API support. |
| `TAKE-03` | Editable notes/remarks | `M:p26`, `M:p28` | Notes read-only | `LOCAL` | Local manifest notes can be edited without touching Axis files. |
| `TAKE-04` | Record/End at bottom of Take Information | `M:p26`, `M:p28`, `A:Toolbar` | Record is in top command bar | `LIVE` | Local record moves to measured cluster; Axis target remains separately capability-gated. |
| `TAKE-05` | Recording clock beside Record | `M:p26` | Clock in top command bar | `LIVE` | Uses monotonic session duration and measured placement. |
| `TAKE-06` | Axis-side recording | `SDK:Command` | Target selector; Demo only | `DEMO` | Optional runtime handles start/stop/finish notifications and take path/name. |
| `EDIT-01` | Selecting a recording automatically opens Edit | `M:p32` | Double-click switches to Edit/timeline; data does not play | `READ` | One selection loads deterministic frames and switches exactly once. |
| `EDIT-02` | Bottom transport and timeline navigation | `M:p32`, `A:Edit`, `A:Toolbar` | First/play/last disabled; decorative clip | `LOCAL` | Play/pause, seek, frame step, range, and timecode operate on local take frames. |
| `EDIT-03` | Loop playback for historical broadcast | `M:p36` | Absent | `LOCAL` | Loop controls deterministic local playback; broadcast remains separately gated. |
| `EDIT-04` | Process button at lower-right of 3D viewport | `M:p32` | Absent | `BLOCKED` | Do not imitate proprietary fusion; an independent processor must have separate provenance and naming. |
| `EDIT-05` | Scene: Flat Ground | `M:p33`, `A:Edit` | Ground plane is visual only | `BLOCKED` | Reference option may be shown disabled with solver-ownership explanation. |
| `EDIT-06` | Scene: Climbing | `M:p33`, `A:Edit` | Absent | `BLOCKED` | Same boundary as `EDIT-05`. |
| `EDIT-07` | Scene: Hip Lock | `M:p33`, `A:Edit` | Absent | `BLOCKED` | Same boundary as `EDIT-05`. |
| `EDIT-08` | Scene: In-Place | `M:p33`, `A:Edit` | Absent | `BLOCKED` | Same boundary as `EDIT-05`. |
| `EDIT-09` | Contact editing | `M:p33`, `A:Edit` | Absent | `BLOCKED` | Requires independently specified editing/processing; never claims Axis processing parity. |
| `EDIT-10` | Constraint mode and pitch adjustment | `M:p33` | Absent | `BLOCKED` | Same boundary as `EDIT-09`. |
| `PROJ-01` | Project contains MotionData and BodySize | `M:p28–30`, `A:Project` | Local take library only | `BLOCKED` | Local library must not masquerade as an Axis project; `.mbx` remains opaque. |
| `PROJ-02` | Folder hierarchy mirrors Windows Explorer | `M:p29–30` | Flat take list | `LOCAL` | Local folders can be implemented; Axis folder mutation remains external. |
| `PROJ-03` | Keyword search | `M:p30–31` | Joint search only | `LOCAL` | Search local take names/notes with deterministic results. |
| `PROJ-04` | Import/export/delete files | `M:p30` | Absent | `LOCAL` | Only documented local formats; deletion requires explicit confirmation and recoverability. |
| `PROJ-05` | Open in File Explorer | `M:p31` | Local path is data only, no action | `LOCAL` | Opens Finder/file manager on a validated local take path. |
| `PROJ-06` | Context rename and quick export | `M:p31` | Absent | `LOCAL` | Context placement matches reference; supported formats are explicit. |
| `EXP-01` | Save As/Export from lower-right Edit and Project context | `M:p33–34`, `A:Toolbar` | No export action | `LOCAL` | Export origin, range, units, rotation order, and destination are explicit. |
| `EXP-02` | `.mbx` export/trim | `M:p34` | Absent | `BLOCKED` | Never emit or edit undocumented `.mbx`. |
| `EXP-03` | FBX options/version/binary/ASCII | `M:p34` | Absent | `LOCAL` | Requires a separately licensed implementation and conformance fixtures. |
| `EXP-04` | BVH export with hierarchy/rotation/displacement options | `M:p34`, `M:p42–43` | BVH ingest only | `LOCAL` | Round-trip fixtures verify hierarchy, rotation order, unit, and frame count. |
| `EXP-05` | Calc CSV export | `M:p34`, `M:p43–50` | Absent | `RUNTIME` | Export only fields actually received; do not synthesize raw sensor values from solved BVH. |

## Settings, broadcast, and integration actions

| ID | Target placement and reference action | Evidence | Current mapping | Class | Acceptance checkpoint |
| --- | --- | --- | --- | --- | --- |
| `SET-01` | Devices/network selection | `M:p6`, `A:Settings` | Downstream host/port field | `BLOCKED` | Axis device NIC/router settings are explanatory only; companion network input is separately labeled. |
| `SET-02` | Solver and advanced joint-gap settings | `M:p32`, `A:Settings` | Absent | `BLOCKED` | No proprietary solver controls are enabled. |
| `SET-03` | Language | `A:Settings` | English-only | `LOCAL` | Localization framework and keyboard layout are screenshot-tested; archive language packs are not reused. |
| `SET-04` | Keyboard shortcuts | `A:Settings` | Modal Escape/Tab only | `LOCAL` | Shortcut list is discoverable, conflict-checked, and platform aware. |
| `SET-05` | Router settings | `A:Settings` | Absent | `BLOCKED` | No router mutation without documented hardware API. |
| `SET-06` | BVH Capture broadcast toggle | `M:p35–36`, `A:Settings` | Companion receives standard BVH; it does not configure Axis | `RUNTIME` | Windows relay may expose the Axis setting; receiver UI remains clearly downstream. |
| `SET-07` | BVH Edit broadcast toggle | `M:p36`, `A:Settings` | Companion receives stream regardless of Axis source | `RUNTIME` | Same ownership rule as `SET-06`. |
| `SET-08` | Advanced BVH Capture/Edit | `M:p35–36` | Deliberately unsupported | `OUT` | UI recommends standard BVH; no undocumented parser is claimed. |
| `SET-09` | Per-output port uniqueness | `M:p36` | Single connection profile | `LOCAL` | Saved profiles validate port collisions and transport roles. |
| `SET-10` | Calc output/settings | `M:p34`, `M:p43–50`, `A:Settings` | Types can represent sensors, but no runtime adapter exists | `RUNTIME` | Version-pinned runtime exposes only verified fields and units. |
| `SET-11` | OSC output/settings | `A:Settings` | Absent | `OUT` | Add only after an independently documented interoperability requirement. |
| `SET-12` | UDP/TCP standard BVH receiver | `M:p35–37` | Implemented, including string/binary standard BVH | `LIVE` | Captured Axis fixtures validate versions, actor counts, loss, reconnect, and units. |
| `SET-13` | Saved profiles and reconnect | Current roadmap | Absent | `LOCAL` | Profiles persist in user config, never in source, and reconnect uses bounded backoff. |
| `SET-14` | Unity/Unreal/coordinate presets | MocapApi configuration surface | Rotation/unit only; up axis and handedness disabled | `LOCAL` | Every preset has coordinate round-trip fixtures and an explicit source/target convention. |

## Visual acceptance protocol

1. Lock the target to Axis Studio `3.0.14004.2620`, Windows display scaling,
   locale, theme, and exact window size. A later Axis build gets a separate
   comparison profile instead of silently replacing this one.
2. Capture reference screens outside Git for disconnected Capture, connected
   Capture, calibration prepare/countdown/progress, recording, sensor warning,
   Project, Edit playback, contact editing, export, and every settings category.
3. For every ledger row, record runtime-observed order, bounding rectangle,
   enabled/pressed/hover state, tooltip, shortcut, and resulting state change.
   Archive names alone are not sufficient evidence for geometry or semantics.
4. Reproduce structure and behavior with original code and assets. Do not trace
   or extract proprietary icons, meshes, images, animations, or strings beyond
   the minimum labels needed for interoperability.
5. Compare private reference captures side by side with the open-source
   Playwright baselines. CI stores and compares only screenshots of this project.
6. A control passes only when both visual placement and action classification
   are correct. A pixel-perfect control that reports fake hardware success fails.

## Update rule

Any change that adds, moves, enables, or renames a reference-facing control must
update its stable ledger row, cite new provenance, add or update a deterministic
screenshot scenario, and include a behavioral test when the action is enabled.
Rows move from `DEMO`, `READ`, `LOCAL`, or `RUNTIME` to `LIVE` only after a
non-simulated fixture or authorized runtime integration verifies the result and
failure path.
