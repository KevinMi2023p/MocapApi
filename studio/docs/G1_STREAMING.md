# BVH to g1_deploy streaming (design)

This document is a design plan only. No bridge implementation exists in this
repository yet, and nothing in this plan has run against a robot. It records
the verified receiving-side interfaces of `g1_deploy` and the work required
to feed it live Axis Studio BVH, so a future implementer does not have to
re-derive them. All file:line citations were read and verified on 2026-07-21
against local checkouts of `g1_deploy`, `gr00t`, and
`GR00T-WholeBodyControl`.

## Purpose and pipeline

Goal: drive the Unitree G1 whole-body tracking policy in `g1_deploy` from a
Perception Neuron suit, using the Windows-VM capture path described in
[WINDOWS_VM.md](WINDOWS_VM.md).

```text
Noitom sensors -> 2.4GHz -> USB/RNDIS transceiver (VM passthrough)
  -> Windows guest + Axis Studio
  -> standard BVH over UDP unicast -> 192.168.77.1:7012
  -> Mocap Studio / MocapApi (Linux host)
  -> [BVH->SMPL bridge: DOES NOT EXIST YET]
  -> ZMQ PUB bind tcp://*:5556, topic "command", msgpack
  -> g1_deploy (SUB connect), 50 Hz control loop
```

The upstream leg is the canonical stream contract from WINDOWS_VM.md: mode
`bvh`, transport `udp`, host `192.168.77.1`, port `7012`, rotation order
`YXZ`, unit `centimeters`. The bridge can consume frames either through
Mocap Studio's decoder (`studio/backend/mocap_studio/providers/bvh.py`
already yields world-space joint positions and rotations per frame) or by
binding UDP 7012 itself; it cannot do both on one port, so tapping the
decoder module is the expected route.

## Where g1_deploy actually listens (verified)

- The robot side is a ZMQ **SUB** socket that **connects** to
  `tcp://<host>:5556` and subscribes to topic `command`
  (`g1_deploy/src/input/src/zmq_interface.cpp:472-490`). The bridge must
  therefore be a **PUB** socket that **binds** port 5556, exactly like the
  reference sender (`sim_test/sim_harness/command_sender.py:26-46`).
- Wire format: a single ZMQ frame containing the topic bytes immediately
  followed by a msgpack-encoded map, with no delimiter
  (`zmq_interface.cpp:540-555`; sender side
  `command_sender.py:134-137`).
- The control loop ticks at 50 Hz (`src/shared/src/task_config.hpp:3`).
  Each tick drains the socket and keeps only the newest message
  (`zmq_interface.cpp:503-537`). Because only the newest message survives,
  every message must carry the full desired state: control booleans such as
  `start` must be re-sent in every message, not sent once. `start`/`stop`
  are additionally sticky once observed (`zmq_interface.cpp:557-566`), but
  a bridge must not rely on a single message being observed at all.

Do not confuse this with the upstream `gear_sonic` deployment in
GR00T-WholeBodyControl: its streaming input uses a different packed wire
protocol (a fixed 1280-byte JSON header followed by concatenated binary
fields, `gear_sonic_deploy/src/g1/g1_deploy_onnx_ref/include/`
`input_interface/zmq_packed_message_subscriber.hpp:9-24`, default topic
`pose`). Its tutorial (`docs/source/tutorials/zmq.md`) documents the same
field semantics and the IsaacLab joint-order conventions, and is useful
reading, but a bridge targeting that encoding will not talk to `g1_deploy`.

## Message schema (streaming mode)

Full schema: `g1_deploy/src/input/src/zmq_command_message.hpp:12-105`.
Streaming (non-planner) messages use these fields; N is the frame count in
the message window.

| Field         | Shape       | Verified constraints                        |
| ---           | ---         | ---                                         |
| `start`/`stop`/`planner` | bool | `planner=false` selects streaming |
| `frame_index` | [N] int64   | strictly increasing, 50 Hz global timeline  |
| `body_quat_w` | [N][4]      | (w, x, y, z); unit norm within 1e-2         |
| `joint_pos`   | [N][29]     | IsaacLab order, radians                     |
| `joint_vel`   | [N][29]     | rad/s; zeros accepted in smpl mode          |
| `smpl_joints` | [N][24][3]  | root-local SMPL joint positions, meters     |
| `smpl_pose`   | [N][21][3]  | SMPL body pose, axis-angle                  |
| `encoder_mode`| string    | `g1`/`teleop`/`smpl`/`token`; max 11 chars |

- Validation (finite values, quaternion norm, strictly increasing
  `frame_index`, per-field shapes): `zmq_interface.cpp:179-212` and the
  helpers at `zmq_interface.cpp:34-51`.
- Capacity limits: at most 10 frames per message; extra frames are
  **silently truncated** at `kMaxFrames`
  (`src/shared/src/interface/streamed_motion_input.hpp:8-46`; the
  encoder-mode name limit `kEncoderModeNameMax = 12` including the
  terminator is at line 28). The gr00t reference sender uses a 5-frame
  sliding window (`pico_manager_thread_server.py:2322-2327`).
- Frame merging: chunks are aligned by `frame_index` into a sliding window;
  the stride between indices is auto-detected, and a gap larger than
  `MAX_GAP_FRAMES = 200` triggers a catch-up reset to the newest chunk
  (`src/motion/src/streamed_motion_merger.hpp:12-40, 67-69`).
- Per-mode required fields are derived from the deployed observation YAML,
  not hardcoded: observation names map to wire-field bits
  (`src/control/src/control_policy.cpp:21-55`), and a packet missing a
  required field is dropped with a log line
  (`src/motion/src/streamed_motion_handler.cpp:91-115, 175-202`). Changing
  `encoder_mode` mid-session resets the handler
  (`streamed_motion_handler.cpp:140-173`).

## Encoder mode choice

Mode names and their required observations come from the deployed config
(e.g. `g1_deploy/policy/token/three_mode/`
`chip_token_compliance_obs_both.yaml:93-120`). Lookahead per observation is
`(N-1)*S+1` ticks for `_Nframe_stepS` observations
(`src/shared/src/observation_config.hpp:440-478`), and a mode's required
lookahead is the max over its required observations
(`control_policy.cpp:343-399`). Playback stalls at the lookahead gate until
enough future frames have arrived
(`streamed_motion_handler.cpp:295-319`), so lookahead is a hard latency
floor.

**`smpl` — recommended for live teleop.** Requires the SMPL fields plus
`body_quat_w`; in the shipped config its observations are
`smpl_*_10frame_step1`, giving a 10-tick (200 ms at 50 Hz) lookahead. The
bridge must produce:

- `smpl_joints`: 24 root-local SMPL joint positions per frame (see the
  transform below);
- `smpl_pose`: 21 axis-angle body poses per frame;
- `joint_pos`: zeros except the 6 wrist joints, IsaacLab indices 23-28
  (`src/shared/src/policy_parameters.hpp:88`; upstream tutorial
  `GR00T-WholeBodyControl/docs/source/tutorials/zmq.md:276` documents the
  same wrist-only convention);
- `joint_vel`: zeros (the gr00t reference sender does exactly this,
  `pico_manager_thread_server.py:1561-1606`).

Note: the shipped three-mode YAML also lists VR 3-point observations under
the `smpl` mode. The handler intentionally does not enforce a VR wire bit
(`streamed_motion_handler.cpp:113`), but the safest bridge mirrors the
gr00t sender, which always includes `vr_position`/`vr_orientation`.

**`g1` — full retarget.** Requires meaningful `joint_pos`/`joint_vel` for
all 29 DoF, i.e. an external BVH-to-G1 retargeter (GMR or a mink-based IK
stack). Under the default multi-frame observations
(`motion_*_10frame_step5`) the lookahead is 46 ticks (920 ms), which is
unusable for live teleop but fine for near-line playback.

**`token`** bypasses the encoder entirely (pre-computed latents) and is out
of scope for this bridge.

## What exists today vs. what must be built

Exists (none of it BVH-live):

- `g1_deploy` contains nothing BVH-related; retargeting is sender-side by
  design.
- gr00t's `groot/control/utils/pico_manager_thread_server.py` is the
  working precedent for a live SMPL streamer: VR body tracking to SMPL,
  50 Hz, 5-frame window, PUB bind
  (`pico_manager_thread_server.py:2322-2327, 2505-2507`).
- GR00T-WholeBodyControl has offline BVH tooling for dataset preparation
  (`gear_sonic/data_process/extract_soma_joints_from_bvh.py`), not live
  streaming.
- This repo has a hardware-free BVH source
  (`studio/tools/windows-vm/send_test_bvh.py`) and a decoder
  (`studio/backend/mocap_studio/providers/bvh.py`) usable as the bridge's
  input during development.

Must be built (the actual bridge):

1. **Noitom-59-joint to SMPL-24 mapping.** A static joint-correspondence
   table from the Appendix B skeleton to SMPL joints, plus skeleton-scale
   normalization (the SMPL encoder was trained on SMPL-proportioned
   bodies; raw Noitom limb lengths will not match).
2. **Coordinate and root-local transform.** BVH is Y-up; the robot stack is
   Z-up. Replicate gr00t's `process_smpl_joints`: rotate the global orient
   Y-up to Z-up, remove the SMPL base rotation, then rotate all joints into
   the root frame with the inverse root quaternion
   (`pico_manager_thread_server.py:552-593`). `body_quat_w` is the
   resulting root orientation, w-first, unit norm.
3. **Rate conversion.** Axis Studio broadcasts 60-240 Hz; the policy
   timeline is 50 Hz. Resample (interpolate, do not decimate-and-jitter)
   and emit strictly increasing 50 Hz `frame_index` values.
4. **Publisher.** msgpack + ZMQ PUB bind :5556, 5-frame sliding window
   re-sent at 50 Hz with `start=True, stop=False, planner=false,
   encoder_mode="smpl"` in every message, modeled on
   `command_sender.py:26-46, 134-137`.
5. **Wrist extraction.** Map Noitom hand/forearm rotations to the 6 wrist
   DoF at IsaacLab indices 23-28; zeros elsewhere; `joint_vel` zeros.

Dependency note: the bridge needs `pyzmq` and `msgpack`, which violate the
studio backend's stdlib-only policy. It must therefore live as a separate
tool with its own environment (like the g1_deploy sim harness), not inside
`studio/backend`.

## Validation plan

Ordered from zero-risk to hardware:

1. **Offline, no streaming.** Convert a recorded BVH take to a
   reference-motion CSV folder and play it in `g1_deploy`'s Normal
   (reference replay) mode. Proves retargeting quality with no wire
   protocol and no timing constraints.
2. **Loopback wire test.** pytest that binds the bridge PUB, connects a
   SUB, and validates every emitted message against a schema mirror
   (shapes, dtypes, quaternion norm, monotone `frame_index`, window size
   at most 10). Use `send_test_bvh.py` as the motion source so the test
   is hardware-free end to end.
3. **Simulator.** `./deploy.sh sim -y --input-type zmq` with the MuJoCo
   sim. Watch the `[ZmqInterface]` logs (`miss start`/`miss end`,
   `drained N queued msgs`, msgpack errors) and `[StreamedMotionHandler]`
   logs (`unknown encoder_mode`, `missing required field`, `stall at
   lookahead gate`).
4. **Closed-loop assertion.** Subscribe to the proprioception stream the
   policy publishes on `tcp://:5557`, topic `g1_debug`
   (`sim_test/sim_harness/proprio_reader.py`), and assert tracking error
   against the commanded motion.
5. Only after all of the above: real hardware, with the same E-stop
   discipline the upstream tutorials require.

## Open questions

- **SMPL conventions of the trained encoder.** Neutral-body assumption,
  exact base-rotation removal, and joint regressor must match gr00t's
  `torch_transform` implementation; "close" is not verified equal.
- **Skeleton mapping validation.** The Noitom-to-SMPL correspondence table
  has never been fit or evaluated; finger data is unused in `smpl` mode
  and its effect on wrist quality is unknown.
- **Latency budget.** Suit radio + VM + UDP + resampler + 5-frame window
  (100 ms) + 10-tick lookahead (200 ms) has not been measured end to end;
  whether the total is acceptable for teleop is open.
- **Hand/Dex3 data.** `left_hand_joints`/`right_hand_joints` are 7-DoF
  Dex3 vectors; whether and how Noitom glove data should map to them is
  undecided.
- **Config drift.** Required fields per mode follow the deployed
  observation YAML; the bridge needs a startup check against the actual
  config rather than assuming the three-mode example.

## Verified interface reference

All references verified 2026-07-21 by reading the cited lines.

- `g1_deploy/src/input/src/zmq_interface.cpp:472-490` — SUB connects
  `tcp://<host>:5556`, topic `command`.
- `zmq_interface.cpp:503-537` — per-tick drain, newest message wins.
- `zmq_interface.cpp:540-555` — topic-prefix strip + msgpack map parse.
- `zmq_interface.cpp:557-566` — sticky `start`/`stop` handling.
- `zmq_interface.cpp:179-212, 34-51` — streaming validation: shapes,
  finiteness, unit quaternions (1e-2), strictly increasing frame index.
- `src/input/src/zmq_command_message.hpp:12-105` — full wire schema and
  dimension constants (29 joints, SMPL 24/21).
- `src/shared/src/interface/streamed_motion_input.hpp:8-46` —
  `kMaxFrames = 10`, `kEncoderModeNameMax = 12` (11 usable chars).
- `src/shared/src/task_config.hpp:3` — 50 Hz task frequency.
- `src/motion/src/streamed_motion_merger.hpp:12-40, 67-69` — frame_index
  window merge, `MAX_GAP_FRAMES = 200`.
- `src/motion/src/streamed_motion_handler.cpp:91-115, 140-173, 175-202,
  295-319` — required-field enforcement, mode-change reset, unknown-mode
  drop, lookahead gating.
- `src/control/src/control_policy.cpp:21-55, 343-399` — observation-name
  to wire-field bits; per-mode required lookahead computation.
- `src/shared/src/observation_config.hpp:440-478` — lookahead formula
  `(N-1)*S+1`.
- `src/shared/src/policy_parameters.hpp:6-15, 68-89` — MuJoCo/IsaacLab
  ordering note and remap tables; IsaacLab wrist indices 23-28 (line 88).
- `policy/token/three_mode/chip_token_compliance_obs_both.yaml:93-120` —
  `g1`/`teleop`/`smpl` mode definitions and required observations.
- `sim_test/sim_harness/command_sender.py:26-46, 134-137` — PUB bind
  :5556; topic bytes + msgpack packing; continuous re-send rationale.
- `sim_test/sim_harness/proprio_reader.py:3, 19` — proprio PUB
  `tcp://*:5557`, topic `g1_debug`.
- `gr00t/groot/control/utils/pico_manager_thread_server.py:552-593` —
  root-local SMPL joint processing (`process_smpl_joints`).
- `pico_manager_thread_server.py:1561-1606` — the streamed `smpl` message
  dict (wrist `joint_pos`, zero `joint_vel`, `encoder_mode="smpl"`).
- `pico_manager_thread_server.py:2322-2327, 2505-2507` — 50 Hz target,
  5-frame window default, PUB bind.
- `GR00T-WholeBodyControl/docs/source/tutorials/zmq.md` — IsaacLab joint
  order and protocol-version tables (wrist indices at line 276). NOTE:
  documents `gear_sonic`'s JSON-header packed protocol
  (`gear_sonic_deploy/.../zmq_packed_message_subscriber.hpp:9-24`), which
  is NOT the `g1_deploy` msgpack protocol above.

## References

- [SMPL body model](https://smpl.is.tue.mpg.de/)
- [GMR: General Motion Retargeting](https://github.com/YanjieZe/GMR)
- [mink: differential inverse kinematics for MuJoCo](https://github.com/kevinzakka/mink)
- [MessagePack specification](https://github.com/msgpack/msgpack/blob/master/spec.md)
- [ZeroMQ socket API (PUB/SUB)](https://zeromq.org/socket-api/)
- [Isaac Lab documentation](https://isaac-sim.github.io/IsaacLab/)
- [Noitom Axis Studio manual: BVH broadcasting](https://noitom.gitbook.io/axis-studio-manual)
- Internal: [WINDOWS_VM.md](WINDOWS_VM.md), [USB_ON_LINUX.md](USB_ON_LINUX.md)
