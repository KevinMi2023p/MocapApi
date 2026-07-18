export type ConnectionMode = "demo" | "bvh" | "mocap-api";
export type Transport = "udp" | "tcp";
export type WorkspaceTab = "capture" | "edit";
export type BottomTab = "takes" | "timeline" | "events" | "diagnostics";
export type ConnectionStatus = "disconnected" | "connecting" | "connected" | "error";

export interface Vec3 {
  x: number;
  y: number;
  z: number;
}

export interface Quat extends Vec3 {
  w: number;
}

export interface Joint {
  id: string;
  name: string;
  parent: string | null;
  position: Vec3;
  rotation: Quat;
  sensorId?: number;
  grounded?: boolean;
}

export interface Sensor {
  id: number;
  name: string;
  bodyPart: string;
  signal: number;
  battery: number;
  temperature: number;
  packetRate: number;
  magnetic: "steady" | "unstable" | "unknown";
  connected: boolean;
}

export interface Avatar {
  id: string;
  name: string;
  joints: Joint[];
  frame: number;
  fps: number;
  calibrated?: boolean | null;
}

export interface RigidBody {
  id: string;
  name: string;
  position: Vec3;
  rotation: Quat;
  tracked: boolean;
}

export type Tracker = RigidBody;

export interface Take {
  id: string;
  name: string;
  notes: string;
  startedAt: string;
  durationMs: number;
  frames: number;
  status: "recording" | "complete" | "interrupted";
  localPath?: string;
}

export interface EventLogItem {
  id: string;
  timestamp: string;
  level: "info" | "warning" | "error" | "success";
  source: string;
  message: string;
}

export interface Diagnostics {
  receivedFrames: number;
  droppedFrames: number;
  jitterMs: number;
  latencyMs: number | null;
  packetsPerSecond: number;
  bytesPerSecond: number;
  lastFrameAt: string | null;
}

export interface Capabilities {
  receiveMotion: boolean;
  receiveSensors: boolean;
  serverCommands: boolean;
  calibrationCommands: boolean;
  axisRecording: boolean;
  localRecording: boolean;
  reason?: string;
}

export interface StudioState {
  connection: {
    status: ConnectionStatus;
    mode: ConnectionMode;
    transport: Transport;
    host: string;
    port: number;
    message: string;
  };
  session: {
    capturing: boolean;
    recording: boolean;
    takeName: string;
    elapsedMs: number;
  };
  avatars: Avatar[];
  sensors: Sensor[];
  rigidBodies: RigidBody[];
  trackers: Tracker[];
  takes: Take[];
  events: EventLogItem[];
  diagnostics: Diagnostics;
  capabilities: Capabilities;
}

export interface ConnectionSettings {
  mode: ConnectionMode;
  transport: Transport;
  host: string;
  port: number;
  rotationOrder: "XYZ" | "XZY" | "YXZ" | "YZX" | "ZXY" | "ZYX";
  upAxis: "+X" | "+Y" | "+Z";
  handedness: "right" | "left";
  unit: "meters" | "centimeters";
}
