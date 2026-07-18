import type { Joint, Sensor, StudioState } from "./types";

const joint = (
  name: string,
  parent: string | null,
  x: number,
  y: number,
  z = 0,
  sensorId?: number,
): Joint => ({
  id: name,
  name,
  parent,
  position: { x, y, z },
  rotation: { x: 0, y: 0, z: 0, w: 1 },
  sensorId,
});

export const demoJoints: Joint[] = [
  joint("Hips", null, 0, 1.02, 0, 1),
  joint("Spine", "Hips", 0, 1.18, 0, 2),
  joint("Spine1", "Spine", 0, 1.35, 0, 3),
  joint("Spine2", "Spine1", 0, 1.5, 0, 4),
  joint("Neck", "Spine2", 0, 1.64, 0, 5),
  joint("Head", "Neck", 0, 1.82, 0, 6),
  joint("LeftShoulder", "Spine2", -0.16, 1.55),
  joint("LeftArm", "LeftShoulder", -0.38, 1.53, 0, 7),
  joint("LeftForeArm", "LeftArm", -0.66, 1.48, 0, 8),
  joint("LeftHand", "LeftForeArm", -0.88, 1.45, 0, 9),
  joint("RightShoulder", "Spine2", 0.16, 1.55),
  joint("RightArm", "RightShoulder", 0.38, 1.53, 0, 10),
  joint("RightForeArm", "RightArm", 0.66, 1.48, 0, 11),
  joint("RightHand", "RightForeArm", 0.88, 1.45, 0, 12),
  joint("LeftUpLeg", "Hips", -0.12, 0.94, 0, 13),
  joint("LeftLeg", "LeftUpLeg", -0.14, 0.52, 0, 14),
  joint("LeftFoot", "LeftLeg", -0.14, 0.08, 0.09, 15),
  joint("RightUpLeg", "Hips", 0.12, 0.94, 0, 16),
  joint("RightLeg", "RightUpLeg", 0.14, 0.52, 0, 17),
  joint("RightFoot", "RightLeg", 0.14, 0.08, 0.09, 18),
];

const sensorNames = [
  "Hips", "Spine", "Chest", "Upper Chest", "Head", "Left Upper Arm",
  "Left Forearm", "Left Hand", "Right Upper Arm", "Right Forearm", "Right Hand",
  "Left Thigh", "Left Shin", "Left Foot", "Right Thigh", "Right Shin", "Right Foot", "Reference",
];

export const demoSensors: Sensor[] = sensorNames.map((bodyPart, index) => ({
  id: index + 1,
  name: `PNS-${String(index + 1).padStart(2, "0")}`,
  bodyPart,
  signal: index === 8 ? 72 : 92 - (index % 4) * 3,
  battery: 76 + (index % 5) * 4,
  temperature: 31.5 + (index % 4) * 0.6,
  packetRate: 96 - (index % 3),
  magnetic: index === 8 ? "unstable" : "steady",
  connected: true,
}));

export const initialState: StudioState = {
  connection: {
    status: "disconnected",
    mode: "demo",
    transport: "udp",
    host: "127.0.0.1",
    port: 7012,
    message: "Ready",
  },
  session: {
    capturing: false,
    recording: false,
    recordingTarget: null,
    takeName: "take001",
    elapsedMs: 0,
  },
  avatars: [{
    id: "avatar-1",
    name: "Performer 01",
    joints: demoJoints,
    frame: 0,
    fps: 60,
    calibrated: null,
  }],
  sensors: demoSensors,
  rigidBodies: [],
  trackers: [],
  takes: [
    {
      id: "take-demo-1",
      name: "walk_cycle_01",
      notes: "Demo take",
      startedAt: new Date(Date.now() - 3600_000).toISOString(),
      durationMs: 18_420,
      frames: 1105,
      status: "complete",
    },
  ],
  events: [
    {
      id: "welcome",
      timestamp: new Date().toISOString(),
      level: "info",
      source: "Studio",
      message: "Preview dataset loaded. Start the local bridge to enable provider actions.",
    },
  ],
  diagnostics: {
    receivedFrames: 0,
    droppedFrames: 0,
    jitterMs: 0,
    latencyMs: null,
    packetsPerSecond: 0,
    bytesPerSecond: 0,
    lastFrameAt: null,
  },
  capabilities: {
    receiveMotion: true,
    receiveSensors: true,
    serverCommands: true,
    calibrationCommands: true,
    axisRecording: true,
    localRecording: true,
    reason: "Demo mode simulates Axis-side operations without contacting hardware.",
  },
};
