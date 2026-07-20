import { initialState } from "../../src/demoState";
import type { StudioState } from "../../src/types";

const clone = <T>(value: T): T => JSON.parse(JSON.stringify(value)) as T;

const fixedBase = (): StudioState => {
  const state = clone(initialState);
  state.avatars = state.avatars.map((avatar) => ({
    ...avatar,
    calibrated: true,
    fps: 60,
    frame: 4_260,
  }));
  state.takes = [
    {
      id: "visual-take-001",
      name: "walk_cycle_01",
      notes: "Calibration reference walk",
      startedAt: "2026-01-15T12:00:00.000Z",
      durationMs: 18_420,
      frames: 1_105,
      status: "complete",
      localPath: "/visual-fixture/walk_cycle_01",
    },
    {
      id: "visual-take-002",
      name: "turn_and_reach_02",
      notes: "Sensor warning reference",
      startedAt: "2026-01-15T12:10:00.000Z",
      durationMs: 9_600,
      frames: 576,
      status: "interrupted",
      localPath: "/visual-fixture/turn_and_reach_02",
    },
  ];
  state.events = [
    {
      id: "visual-event-connected",
      timestamp: "2026-01-15T12:00:00.000Z",
      level: "success",
      source: "Connection",
      message: "DEMO source connected",
    },
    {
      id: "visual-event-warning",
      timestamp: "2026-01-15T12:00:06.000Z",
      level: "warning",
      source: "Sensors",
      message: "Right upper arm magnetic environment is unstable",
    },
  ];
  state.diagnostics = {
    receivedFrames: 4_260,
    droppedFrames: 3,
    jitterMs: 0.7,
    latencyMs: 8.4,
    packetsPerSecond: 60,
    bytesPerSecond: 46_080,
    lastFrameAt: "2026-01-15T12:00:06.000Z",
  };
  return state;
};

export const disconnectedFixture = (): StudioState => {
  const state = fixedBase();
  state.connection = {
    status: "disconnected",
    mode: "demo",
    transport: "udp",
    host: "127.0.0.1",
    port: 7012,
    message: "Ready",
  };
  state.session = {
    capturing: false,
    recording: false,
    recordingTarget: null,
    takeName: "take003",
    elapsedMs: 0,
  };
  state.avatars = [];
  state.sensors = [];
  state.diagnostics = {
    receivedFrames: 0,
    droppedFrames: 0,
    jitterMs: 0,
    latencyMs: null,
    packetsPerSecond: 0,
    bytesPerSecond: 0,
    lastFrameAt: null,
  };
  state.capabilities = {
    receiveMotion: false,
    receiveSensors: false,
    serverCommands: false,
    calibrationCommands: false,
    axisRecording: false,
    localRecording: false,
    reason: "Connect a provider to discover its capabilities.",
  };
  state.events = [{
    id: "visual-event-ready",
    timestamp: "2026-01-15T11:59:59.000Z",
    level: "info",
    source: "Studio",
    message: "Operator console ready. Connect a BVH stream or start Demo mode.",
  }];
  return state;
};

export const connectedCaptureFixture = (): StudioState => {
  const state = fixedBase();
  state.connection = {
    status: "connected",
    mode: "demo",
    transport: "udp",
    host: "127.0.0.1",
    port: 7012,
    message: "Demo source online",
  };
  state.session = {
    capturing: true,
    recording: false,
    recordingTarget: null,
    takeName: "take003",
    elapsedMs: 6_250,
  };
  state.capabilities = {
    receiveMotion: true,
    receiveSensors: true,
    serverCommands: true,
    calibrationCommands: true,
    axisRecording: true,
    localRecording: true,
    reason: "Deterministic visual fixture; no hardware is contacted.",
  };
  return state;
};
