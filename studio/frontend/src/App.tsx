import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  Activity,
  AlertCircle,
  AlertTriangle,
  Battery,
  Bone,
  Box,
  Cable,
  CheckCircle2,
  ChevronDown,
  ChevronRight,
  CircleDot,
  Clock3,
  Cpu,
  Database,
  FileText,
  FolderOpen,
  Gauge,
  Info,
  Layers3,
  ListTree,
  Magnet,
  Play,
  Plug,
  Radio,
  RefreshCw,
  RotateCcw,
  Search,
  Settings,
  Signal,
  SkipBack,
  SkipForward,
  SlidersHorizontal,
  Sparkles,
  Square,
  UserRound,
  UsersRound,
  Video,
  Wifi,
  X,
  Zap,
} from "lucide-react";
import { connect, disconnect, fetchState, sendCommand, subscribeToState } from "./api";
import { CalibrationDialog, ConnectionDialog } from "./components/Dialogs";
import { SensorMap } from "./components/SensorMap";
import {
  SkeletonViewport,
  type CameraView,
} from "./components/SkeletonViewport";
import { initialState } from "./demoState";
import type {
  BottomTab,
  ConnectionSettings,
  EventLogItem,
  Joint,
  Sensor,
  StudioState,
  Take,
  WorkspaceTab,
} from "./types";

type LeftTab = "suits" | "scene" | "sensors";
type InspectorTab = "joint" | "sensor";

const defaultConnection: ConnectionSettings = {
  mode: "demo",
  transport: "udp",
  host: "127.0.0.1",
  port: 7012,
  rotationOrder: "YXZ",
  upAxis: "+Y",
  handedness: "right",
  unit: "meters",
};

const formatDuration = (milliseconds: number, showFrames = false) => {
  const safe = Math.max(0, milliseconds);
  const hours = Math.floor(safe / 3_600_000);
  const minutes = Math.floor((safe % 3_600_000) / 60_000);
  const seconds = Math.floor((safe % 60_000) / 1000);
  const frames = Math.floor((safe % 1000) / (1000 / 60));
  const base = [hours, minutes, seconds].map((value) => String(value).padStart(2, "0")).join(":");
  return showFrames ? `${base}:${String(frames).padStart(2, "0")}` : base;
};

const formatNumber = (value: number, fractionDigits = 0) =>
  new Intl.NumberFormat(undefined, { maximumFractionDigits: fractionDigits }).format(value);

const formatEventTime = (value: string) => {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "--:--:--";
  return date.toLocaleTimeString([], { hour12: false });
};

function ToolbarAction({
  label,
  tooltip,
  active = false,
  danger = false,
  disabledReason,
  onClick,
  children,
}: {
  label: string;
  tooltip: string;
  active?: boolean;
  danger?: boolean;
  disabledReason?: string;
  onClick: () => void;
  children: React.ReactNode;
}) {
  const disabled = Boolean(disabledReason);
  const help = disabledReason ?? tooltip;
  return (
    <span
      className="action-tooltip"
      data-tooltip={help}
      tabIndex={disabled ? 0 : -1}
      aria-label={disabled ? `${label} unavailable: ${disabledReason}` : undefined}
    >
      <button
        type="button"
        className={`tool-action${active ? " active" : ""}${danger ? " danger" : ""}`}
        onClick={onClick}
        disabled={disabled}
        aria-pressed={active}
        aria-label={label}
      >
        <span className="tool-action-icon">{children}</span>
        <span>{label}</span>
      </button>
    </span>
  );
}

function PaneTitle({
  title,
  subtitle,
  children,
}: {
  title: string;
  subtitle?: string;
  children?: React.ReactNode;
}) {
  return (
    <div className="pane-title">
      <div>
        <strong>{title}</strong>
        {subtitle ? <span>{subtitle}</span> : null}
      </div>
      {children}
    </div>
  );
}

function MetricBar({
  label,
  value,
  suffix,
  maximum = 100,
  tone = "blue",
}: {
  label: string;
  value: number;
  suffix: string;
  maximum?: number;
  tone?: "blue" | "green" | "yellow" | "red";
}) {
  const percentage = Math.min(100, Math.max(0, (value / maximum) * 100));
  return (
    <div className="metric-bar">
      <div className="metric-label"><span>{label}</span><strong>{formatNumber(value, 1)}{suffix}</strong></div>
      <div className="metric-track" role="meter" aria-label={label} aria-valuenow={value} aria-valuemin={0} aria-valuemax={maximum}>
        <span className={tone} style={{ width: `${percentage}%` }} />
      </div>
    </div>
  );
}

function EventIcon({ level }: { level: EventLogItem["level"] }) {
  if (level === "error") return <AlertCircle size={13} />;
  if (level === "warning") return <AlertTriangle size={13} />;
  if (level === "success") return <CheckCircle2 size={13} />;
  return <Info size={13} />;
}

export default function App() {
  const [state, setState] = useState<StudioState>(initialState);
  const [workspace, setWorkspace] = useState<WorkspaceTab>("capture");
  const [bottomTab, setBottomTab] = useState<BottomTab>("takes");
  const [leftTab, setLeftTab] = useState<LeftTab>("suits");
  const [inspectorTab, setInspectorTab] = useState<InspectorTab>("joint");
  const [connectionSettings, setConnectionSettings] = useState(defaultConnection);
  const [backendOnline, setBackendOnline] = useState(false);
  const [lastError, setLastError] = useState<string | null>(null);
  const [connectionDialogOpen, setConnectionDialogOpen] = useState(false);
  const [calibrationDialogOpen, setCalibrationDialogOpen] = useState(false);
  const [selectedAvatarId, setSelectedAvatarId] = useState(initialState.avatars[0]?.id ?? null);
  const [selectedJointId, setSelectedJointId] = useState(initialState.avatars[0]?.joints[0]?.id ?? null);
  const [selectedSensorId, setSelectedSensorId] = useState<number | null>(initialState.sensors[0]?.id ?? null);
  const [selectedTakeId, setSelectedTakeId] = useState(initialState.takes[0]?.id ?? null);
  const [cameraView, setCameraView] = useState<CameraView>("perspective");
  const [cameraRevision, setCameraRevision] = useState(0);
  const [followActor, setFollowActor] = useState(true);
  const [showLabels, setShowLabels] = useState(true);
  const [showSensors, setShowSensors] = useState(true);
  const [bottomCollapsed, setBottomCollapsed] = useState(false);
  const [sceneExpanded, setSceneExpanded] = useState(true);
  const [jointFilter, setJointFilter] = useState("");
  const [takeNameDraft, setTakeNameDraft] = useState(initialState.session.takeName);
  const takeNameDirty = useRef(false);
  const previousRecording = useRef(initialState.session.recording);

  useEffect(() => {
    let unsubscribe = () => {};
    let mounted = true;
    fetchState()
      .then((nextState) => {
        if (!mounted) return;
        setState(nextState);
        setBackendOnline(true);
        unsubscribe = subscribeToState(
          (streamState) => {
            setState(streamState);
            setBackendOnline(true);
          },
          (error) => {
            setLastError(error.message);
            setBackendOnline(false);
          },
        );
      })
      .catch(() => {
        if (mounted) setBackendOnline(false);
      });
    return () => {
      mounted = false;
      unsubscribe();
    };
  }, []);

  useEffect(() => {
    const recordingChanged = previousRecording.current !== state.session.recording;
    if (recordingChanged || !takeNameDirty.current) {
      setTakeNameDraft(state.session.takeName);
      takeNameDirty.current = false;
    }
    previousRecording.current = state.session.recording;
  }, [state.session.recording, state.session.takeName]);

  const primaryAvatar = useMemo(
    () => state.avatars.find((avatar) => avatar.id === selectedAvatarId) ?? state.avatars[0],
    [selectedAvatarId, state.avatars],
  );
  const selectedJoint = useMemo(
    () => primaryAvatar?.joints.find((joint) => joint.id === selectedJointId) ?? primaryAvatar?.joints[0],
    [primaryAvatar, selectedJointId],
  );
  const selectedSensor = useMemo(
    () => state.sensors.find((sensor) => sensor.id === selectedSensorId) ?? state.sensors[0],
    [selectedSensorId, state.sensors],
  );
  const selectedTake = useMemo(
    () => state.takes.find((take) => take.id === selectedTakeId) ?? state.takes[0],
    [selectedTakeId, state.takes],
  );
  const warningCount = state.sensors.filter(
    (sensor) => !sensor.connected || sensor.signal < 75 || sensor.magnetic === "unstable",
  ).length;
  const connected = backendOnline && state.connection.status === "connected";
  const providerAttached = backendOnline && (state.connection.status === "connected" || state.connection.status === "error");
  const previewMode = !backendOnline;
  const sensorTelemetryAvailable = connected && state.capabilities.receiveSensors;
  const attemptedFrames = state.diagnostics.receivedFrames + state.diagnostics.droppedFrames;
  const captureWorkspace = workspace === "capture";

  const execute = useCallback(async (command: string, options: Record<string, unknown> = {}) => {
    setLastError(null);
    if (!backendOnline) {
      const error = new Error("Local bridge is offline. Preview data is visual-only.");
      setLastError(error.message);
      throw error;
    }
    try {
      await sendCommand(command, options);
    } catch (cause) {
      const error = cause instanceof Error ? cause : new Error("Command failed");
      setLastError(error.message);
      throw error;
    }
  }, [backendOnline]);

  const connectNow = useCallback(async (settings: ConnectionSettings = connectionSettings) => {
    setConnectionSettings(settings);
    setLastError(null);
    if (!backendOnline) {
      const error = new Error("Local bridge is offline. Start the bridge before connecting.");
      setLastError(error.message);
      throw error;
    }
    try {
      await connect(settings);
    } catch (cause) {
      const error = cause instanceof Error ? cause : new Error("Connection failed");
      setLastError(error.message);
      throw error;
    }
  }, [backendOnline, connectionSettings]);

  const disconnectNow = useCallback(async () => {
    setLastError(null);
    if (!backendOnline) {
      setLastError("Local bridge is offline. No live connection can be changed.");
      return;
    }
    try {
      await disconnect();
    } catch (cause) {
      setLastError(cause instanceof Error ? cause.message : "Disconnect failed");
    }
  }, [backendOnline]);

  const selectJoint = useCallback((joint: Joint) => {
    setSelectedJointId(joint.id);
    if (joint.sensorId) setSelectedSensorId(joint.sensorId);
    setInspectorTab("joint");
  }, []);

  const selectSensor = useCallback((sensor: Sensor) => {
    setSelectedSensorId(sensor.id);
    const joint = primaryAvatar?.joints.find((candidate) => candidate.sensorId === sensor.id);
    if (joint) setSelectedJointId(joint.id);
    setInspectorTab("sensor");
  }, [primaryAvatar]);

  const bridgeReason = backendOnline ? undefined : "Local bridge offline — preview data is visual-only";
  const connectionReason = connected ? undefined : "Connect a motion provider first";
  const workspaceReason = captureWorkspace ? undefined : "Switch to Capture workspace to use live controls";
  const captureDisabled = workspaceReason ?? bridgeReason ?? connectionReason ?? (!state.capabilities.serverCommands ? state.capabilities.reason ?? "Provider capture commands are unavailable" : undefined);
  const commandDisabled = workspaceReason ?? bridgeReason ?? connectionReason ?? (!state.capabilities.serverCommands ? state.capabilities.reason ?? "Provider commands are unavailable" : undefined);
  const calibrationDisabled = workspaceReason ?? bridgeReason ?? connectionReason ?? (!state.capabilities.calibrationCommands ? state.capabilities.reason ?? "Calibration commands are unavailable" : undefined);
  const recordingDisabled = workspaceReason ?? bridgeReason ?? connectionReason ?? (!(state.capabilities.axisRecording || state.capabilities.localRecording) ? state.capabilities.reason ?? "Recording is unavailable" : undefined);

  const chooseTake = (take: Take) => {
    setSelectedTakeId(take.id);
  };

  return (
    <main className={`studio-shell${bottomCollapsed ? " bottom-collapsed" : ""}`}>
      <header className="title-bar">
        <div className="brand-lockup" aria-label="Mocap Studio">
          <span className="brand-mark"><CircleDot size={17} /></span>
          <div className="brand-copy">
            <strong>MOCAP STUDIO</strong>
            <span>COMMUNITY CONSOLE</span>
          </div>
        </div>

        <div className="project-identity">
          <FolderOpen size={14} />
          <span className="project-label">PROJECT</span>
          <strong>Live Session</strong>
          <span className="project-separator">/</span>
          <span>{primaryAvatar?.name ?? "No performer"}</span>
        </div>

        <nav className="workspace-tabs" aria-label="Workspace">
          <button
            type="button"
            className={workspace === "capture" ? "active" : ""}
            aria-current={workspace === "capture" ? "page" : undefined}
            onClick={() => setWorkspace("capture")}
          >
            <Video size={13} /> Capture
          </button>
          <button
            type="button"
            className={workspace === "edit" ? "active" : ""}
            aria-current={workspace === "edit" ? "page" : undefined}
            onClick={() => {
              setWorkspace("edit");
              setBottomTab("timeline");
            }}
          >
            <SlidersHorizontal size={13} /> Edit
          </button>
        </nav>

        <div className="title-actions">
          <div className={`compact-connection ${backendOnline ? state.connection.status : "disconnected"}`}>
            <span className="status-dot" />
            <span>{previewMode ? "PREVIEW / BRIDGE OFFLINE" : providerAttached ? `${state.connection.host}:${state.connection.port}` : "Not connected"}</span>
          </div>
          <button type="button" className="title-icon-button" aria-label="Connection settings" title="Connection settings" onClick={() => setConnectionDialogOpen(true)}>
            <Settings size={15} />
          </button>
          <span className="title-icon-button passive" role="img" aria-label="Independent MocapApi companion, not affiliated with Noitom" title="Independent MocapApi companion">
            <Info size={15} />
          </span>
        </div>
      </header>

      <section className="command-bar" aria-label="Capture controls">
        <div className="command-group connection-command">
          <ToolbarAction
            label={providerAttached ? "Disconnect" : "Connect"}
            tooltip={providerAttached ? "Detach the current provider, including an errored transport" : "Configure and connect to a motion provider"}
            active={connected}
            disabledReason={previewMode ? "Local bridge offline — open Settings to inspect configuration" : undefined}
            onClick={() => providerAttached ? void disconnectNow() : setConnectionDialogOpen(true)}
          >
            {providerAttached ? <Wifi size={18} /> : <Plug size={18} />}
          </ToolbarAction>
          <div className="connection-copy">
            <strong>{previewMode ? "Preview only" : state.connection.status === "error" ? "Provider error" : connected ? "Provider online" : "Provider offline"}</strong>
            <span>{previewMode ? "Bridge offline · sample data" : state.connection.message}</span>
          </div>
        </div>
        <span className="command-divider" />
        <div className="command-group">
          <ToolbarAction
            label={state.session.capturing ? "Stop" : "Capture"}
            tooltip={state.session.capturing ? "Stop receiving the live capture" : "Start receiving live solved motion"}
            active={state.session.capturing}
            disabledReason={captureDisabled}
            onClick={() => void execute(state.session.capturing ? "stop_capture" : "start_capture").catch(() => {})}
          >
            {state.session.capturing ? <Square size={17} /> : <Activity size={18} />}
          </ToolbarAction>
          <ToolbarAction
            label="Zero"
            tooltip="Set the current heading as zero position"
            disabledReason={commandDisabled}
            onClick={() => void execute("zero_position").catch(() => {})}
          >
            <RotateCcw size={18} />
          </ToolbarAction>
          <ToolbarAction
            label="Calibrate"
            tooltip="Open provider-guided posture calibration"
            disabledReason={calibrationDisabled}
            onClick={() => setCalibrationDialogOpen(true)}
          >
            <Sparkles size={18} />
          </ToolbarAction>
          <ToolbarAction
            label="Resume"
            tooltip="Resume the provider's last original posture"
            disabledReason={commandDisabled}
            onClick={() => void execute("resume_original_posture").catch(() => {})}
          >
            <RefreshCw size={18} />
          </ToolbarAction>
        </div>
        <span className="command-divider flexible" />
        <div className="take-name-control">
          <label htmlFor="take-name">TAKE NAME</label>
          <input
            id="take-name"
            value={takeNameDraft}
            disabled={state.session.recording}
            onChange={(event) => {
              takeNameDirty.current = true;
              setTakeNameDraft(event.target.value);
            }}
          />
        </div>
        <div className={`capture-clock${state.session.recording ? " recording" : ""}`}>
          <Clock3 size={15} />
          <strong>{formatDuration(state.session.elapsedMs, true)}</strong>
          <span>{state.session.recording ? "REC" : "TIMECODE"}</span>
        </div>
        <ToolbarAction
          label={state.session.recording ? "Stop record" : "Record"}
          tooltip={state.session.recording ? "Finish and save the current take" : "Start provider or local recording"}
          danger
          active={state.session.recording}
          disabledReason={recordingDisabled}
          onClick={() => void execute(
            state.session.recording ? "stop_record" : "start_record",
            state.session.recording ? {} : { takeName: takeNameDraft },
          ).catch(() => {})}
        >
          {state.session.recording ? <Square size={17} /> : <CircleDot size={19} />}
        </ToolbarAction>
      </section>

      <section className="main-workspace">
        <aside className="left-pane workstation-pane" aria-label="Suits and scene">
          <PaneTitle title="Suits" subtitle={`${state.avatars.length} performer${state.avatars.length === 1 ? "" : "s"}`} />
          <div className="pane-tab-strip" role="tablist" aria-label="Suit views">
            <button type="button" role="tab" aria-selected={leftTab === "suits"} className={leftTab === "suits" ? "active" : ""} onClick={() => setLeftTab("suits")}><UsersRound size={13} /> Suits</button>
            <button type="button" role="tab" aria-selected={leftTab === "scene"} className={leftTab === "scene" ? "active" : ""} onClick={() => setLeftTab("scene")}><ListTree size={13} /> Scene</button>
            <button type="button" role="tab" aria-selected={leftTab === "sensors"} className={leftTab === "sensors" ? "active" : ""} onClick={() => setLeftTab("sensors")}><Radio size={13} /> Map</button>
          </div>

          <div className="pane-content left-pane-content">
            {leftTab === "suits" ? (
              <div className="suits-view">
                <div className="suit-toolbar">
                  <div className="capability-ribbon" title="Direct suit pairing is not exposed by MocapApi">
                    <Cable size={13} /> Pairing is provider-owned <span>READ-ONLY</span>
                  </div>
                </div>
                <div className="entity-list">
                  {state.avatars.map((avatar) => (
                    <button
                      type="button"
                      key={avatar.id}
                      className={`avatar-card${avatar.id === primaryAvatar?.id ? " selected" : ""}`}
                      onClick={() => {
                        setSelectedAvatarId(avatar.id);
                        setSelectedJointId(avatar.joints[0]?.id ?? null);
                      }}
                    >
                      <span className="avatar-thumbnail"><UserRound size={22} /></span>
                      <span className="avatar-card-copy">
                        <strong>{avatar.name}</strong>
                        <small>{avatar.joints.length} joints · {avatar.fps} fps</small>
                      </span>
                      <span className={`avatar-state${avatar.calibrated ? " calibrated" : ""}`} title={avatar.calibrated ? "Calibrated" : "Needs calibration"}>
                        {avatar.calibrated ? <CheckCircle2 size={14} /> : <AlertTriangle size={14} />}
                      </span>
                    </button>
                  ))}
                </div>
                <div className="suit-health-card">
                  <div className="health-card-heading">
                    <span><Gauge size={14} /> Suit health</span>
                    <strong className={!sensorTelemetryAvailable ? "unavailable-text" : warningCount ? "warning-text" : "good-text"}>
                      {!connected ? "OFFLINE" : !state.capabilities.receiveSensors ? "UNAVAILABLE" : warningCount ? "CHECK" : "READY"}
                    </strong>
                  </div>
                  {sensorTelemetryAvailable ? (
                    <MetricBar label="Sensor link" value={state.sensors.length - warningCount} suffix={` / ${state.sensors.length}`} maximum={state.sensors.length || 1} tone={warningCount ? "yellow" : "green"} />
                  ) : (
                    <div className="compact-unavailable"><Radio size={12} /> Sensor telemetry {!connected ? "offline" : "unavailable"}</div>
                  )}
                  <div className="mini-stat-grid">
                    <span><small>Frame</small><strong>{primaryAvatar?.frame ?? 0}</strong></span>
                    <span><small>Rate</small><strong>{primaryAvatar?.fps ?? 0} fps</strong></span>
                    <span><small>Warnings</small><strong>{sensorTelemetryAvailable ? warningCount : "—"}</strong></span>
                  </div>
                </div>
                <div className="working-mode-row">
                  <label title="Solver working mode is controlled by the connected provider"><span>Working mode</span><select defaultValue="auto" disabled aria-label="Provider working mode"><option value="auto">Auto detection</option><option value="full">Full body</option></select></label>
                  <label><span>Frame rate</span><input value={`${primaryAvatar?.fps ?? 0} fps`} readOnly /></label>
                </div>
              </div>
            ) : null}

            {leftTab === "scene" ? (
              <div className="scene-view">
                <label className="tree-search">
                  <Search size={13} />
                  <input placeholder="Filter joints" value={jointFilter} onChange={(event) => setJointFilter(event.target.value)} aria-label="Filter joints" />
                </label>
                <div className="scene-tree" role="tree" aria-label="Motion scene">
                  <button type="button" className="tree-row root" role="treeitem" aria-expanded={sceneExpanded} onClick={() => setSceneExpanded(!sceneExpanded)}>
                    {sceneExpanded ? <ChevronDown size={13} /> : <ChevronRight size={13} />}
                    <UserRound size={14} />
                    <span>{primaryAvatar?.name ?? "Avatar"}</span>
                    <i className="visibility-dot" />
                  </button>
                  {sceneExpanded ? (
                    <div role="group" className="tree-children">
                      {primaryAvatar?.joints
                        .filter((joint) => joint.name.toLowerCase().includes(jointFilter.toLowerCase()))
                        .map((joint) => (
                          <button
                            type="button"
                            role="treeitem"
                            key={joint.id}
                            className={`tree-row${selectedJoint?.id === joint.id ? " selected" : ""}`}
                            onClick={() => selectJoint(joint)}
                          >
                            <span className="tree-indent" />
                            <Bone size={12} />
                            <span>{joint.name}</span>
                            {joint.sensorId ? <i className="linked-sensor">{joint.sensorId}</i> : null}
                          </button>
                        ))}
                    </div>
                  ) : null}
                  <div className="tree-row muted"><ChevronRight size={13} /><Box size={13} /><span>Rigid Bodies</span><small>{state.rigidBodies.length}</small></div>
                  <div className="tree-row muted"><ChevronRight size={13} /><Layers3 size={13} /><span>Trackers</span><small>{state.trackers.length}</small></div>
                  <div className="tree-row"><span className="tree-spacer" /><span className="floor-icon" /><span>Ground Plane</span><i className="visibility-dot" /></div>
                </div>
              </div>
            ) : null}

            {leftTab === "sensors" ? (
              <SensorMap sensors={state.sensors} selectedSensorId={selectedSensor?.id ?? null} available={sensorTelemetryAvailable} connected={connected} onSelect={selectSensor} />
            ) : null}
          </div>
        </aside>

        <SkeletonViewport
          avatar={primaryAvatar}
          sensors={state.sensors}
          selectedJointId={selectedJoint?.id ?? null}
          selectedSensorId={selectedSensor?.id ?? null}
          capturing={state.session.capturing}
          preview={previewMode}
          cameraView={cameraView}
          cameraRevision={cameraRevision}
          followActor={followActor}
          showLabels={showLabels}
          showSensors={showSensors && sensorTelemetryAvailable}
          onCameraViewChange={(view) => {
            setCameraView(view);
            setCameraRevision((revision) => revision + 1);
          }}
          onResetCamera={() => setCameraRevision((revision) => revision + 1)}
          onFollowActorChange={setFollowActor}
          onShowLabelsChange={setShowLabels}
          onShowSensorsChange={setShowSensors}
          onJointSelect={selectJoint}
        />

        <aside className="right-pane workstation-pane" aria-label="Joint and sensor inspector">
          <PaneTitle title="Inspector" subtitle={inspectorTab === "joint" ? "Skeleton" : "Device detail"} />
          <div className="pane-tab-strip inspector-tabs" role="tablist" aria-label="Inspector views">
            <button type="button" role="tab" aria-selected={inspectorTab === "joint"} className={inspectorTab === "joint" ? "active" : ""} onClick={() => setInspectorTab("joint")}><Bone size={13} /> Joint</button>
            <button type="button" role="tab" aria-selected={inspectorTab === "sensor"} className={inspectorTab === "sensor" ? "active" : ""} onClick={() => setInspectorTab("sensor")}><Cpu size={13} /> Sensor</button>
          </div>
          <div className="pane-content inspector-content">
            {inspectorTab === "joint" && selectedJoint ? (
              <div className="joint-inspector">
                <div className="inspector-entity-header">
                  <span className="entity-icon"><Bone size={20} /></span>
                  <div><span>JOINT</span><h3>{selectedJoint.name}</h3><p>{primaryAvatar?.name} / {selectedJoint.parent ?? "Root"}</p></div>
                  <span className={`entity-live-badge${connected ? "" : " offline"}`}><i /> {connected ? "LIVE" : "OFFLINE"}</span>
                </div>
                <section className="property-section">
                  <h4>Transform <span>World</span></h4>
                  <div className="vector-row">
                    <span>Position</span>
                    {(["x", "y", "z"] as const).map((axis) => (
                      <label key={axis} className={`axis-field ${axis}`}><b>{axis.toUpperCase()}</b><input value={selectedJoint.position[axis].toFixed(3)} readOnly aria-label={`Position ${axis}`} /></label>
                    ))}
                  </div>
                  <div className="quaternion-grid">
                    <span>Rotation</span>
                    {(["x", "y", "z", "w"] as const).map((axis) => (
                      <label key={axis} className={`axis-field ${axis}`}><b>{axis.toUpperCase()}</b><input value={selectedJoint.rotation[axis].toFixed(3)} readOnly aria-label={`Rotation ${axis}`} /></label>
                    ))}
                  </div>
                </section>
                <section className="property-section">
                  <h4>Binding</h4>
                  {selectedJoint.sensorId && state.sensors.find((sensor) => sensor.id === selectedJoint.sensorId) ? (
                    <button type="button" className="bound-sensor-card" onClick={() => {
                      const sensor = state.sensors.find((item) => item.id === selectedJoint.sensorId);
                      if (sensor) selectSensor(sensor);
                    }}>
                      <span className="sensor-device-icon"><Cpu size={17} /></span>
                      <span><strong>{state.sensors.find((sensor) => sensor.id === selectedJoint.sensorId)?.name}</strong><small>Sensor {selectedJoint.sensorId} · linked</small></span>
                      <ChevronRight size={14} />
                    </button>
                  ) : <p className="empty-property">No sensor is bound to this joint.</p>}
                </section>
                <section className="property-section capability-section">
                  <h4>Data ownership</h4>
                  <p>Transforms shown here are solved upstream and exposed read-only by MocapApi.</p>
                </section>
              </div>
            ) : null}

            {inspectorTab === "sensor" && selectedSensor ? (
              <div className="sensor-inspector">
                <div className="inspector-entity-header">
                  <span className="entity-icon sensor"><Cpu size={20} /></span>
                  <div><span>SENSOR {String(selectedSensor.id).padStart(2, "0")}</span><h3>{selectedSensor.name}</h3><p>{selectedSensor.bodyPart}</p></div>
                  <span className={`entity-live-badge${sensorTelemetryAvailable && selectedSensor.connected ? "" : " offline"}`}><i />
                    {sensorTelemetryAvailable && selectedSensor.connected ? "LIVE" : !connected ? "OFFLINE" : "UNAVAILABLE"}
                  </span>
                </div>
                {!sensorTelemetryAvailable ? (
                  <div className="sensor-unavailable-card"><Radio size={15} /><div><strong>Telemetry {!connected ? "offline" : "unavailable"}</strong><p>{!connected ? "Connect a provider to receive current sensor values." : "This provider does not expose sensor telemetry. Values below are the last sample only."}</p></div></div>
                ) : null}
                <section className={`property-section telemetry-section${sensorTelemetryAvailable ? "" : " stale-telemetry"}`}>
                  <h4>Wireless telemetry <span>{selectedSensor.packetRate} pps</span></h4>
                  <MetricBar label="Signal" value={selectedSensor.signal} suffix="%" tone={selectedSensor.signal < 75 ? "yellow" : "green"} />
                  <MetricBar label="Battery" value={selectedSensor.battery} suffix="%" tone={selectedSensor.battery < 25 ? "red" : "blue"} />
                </section>
                <section className={`property-section sensor-facts${sensorTelemetryAvailable ? "" : " stale-telemetry"}`}>
                  <h4>Device detail</h4>
                  <dl>
                    <div><dt><Battery size={13} /> Battery</dt><dd>{selectedSensor.battery}%</dd></div>
                    <div><dt><Activity size={13} /> Packet rate</dt><dd>{selectedSensor.packetRate} Hz</dd></div>
                    <div><dt><Gauge size={13} /> Temperature</dt><dd>{selectedSensor.temperature.toFixed(1)} °C</dd></div>
                    <div><dt><Magnet size={13} /> Magnetic</dt><dd className={selectedSensor.magnetic === "unstable" ? "warning-text" : "good-text"}><i className={`mag-square ${selectedSensor.magnetic}`} /> {selectedSensor.magnetic}</dd></div>
                  </dl>
                </section>
                {!sensorTelemetryAvailable ? null : selectedSensor.magnetic === "unstable" || selectedSensor.signal < 75 ? (
                  <div className="sensor-warning-card"><AlertTriangle size={15} /><div><strong>Sensor needs attention</strong><p>Check fit, radio distance, and nearby magnetic interference before calibration.</p></div></div>
                ) : (
                  <div className="sensor-ready-card"><CheckCircle2 size={15} /><div><strong>Sensor nominal</strong><p>Signal and magnetic readings are stable.</p></div></div>
                )}
              </div>
            ) : null}
          </div>
        </aside>
      </section>

      <section className="bottom-dock" aria-label="Session dock">
        <div className="bottom-tab-bar">
          <div className="bottom-tabs" role="tablist" aria-label="Session views">
            <button type="button" role="tab" aria-selected={bottomTab === "takes"} className={bottomTab === "takes" ? "active" : ""} onClick={() => { setBottomTab("takes"); setBottomCollapsed(false); }}><Database size={13} /> Takes <span>{state.takes.length}</span></button>
            <button type="button" role="tab" aria-selected={bottomTab === "timeline"} className={bottomTab === "timeline" ? "active" : ""} onClick={() => { setBottomTab("timeline"); setBottomCollapsed(false); }}><SlidersHorizontal size={13} /> Timeline</button>
            <button type="button" role="tab" aria-selected={bottomTab === "events"} className={bottomTab === "events" ? "active" : ""} onClick={() => { setBottomTab("events"); setBottomCollapsed(false); }}><FileText size={13} /> Events <span>{state.events.length}</span></button>
            <button type="button" role="tab" aria-selected={bottomTab === "diagnostics"} className={bottomTab === "diagnostics" ? "active" : ""} onClick={() => { setBottomTab("diagnostics"); setBottomCollapsed(false); }}><Activity size={13} /> Diagnostics</button>
          </div>
          <div className="bottom-dock-actions">
            <span>{selectedTake ? `${selectedTake.name} · ${selectedTake.frames} frames` : "No take selected"}</span>
            <button type="button" className="icon-button" aria-label={bottomCollapsed ? "Expand session dock" : "Collapse session dock"} onClick={() => setBottomCollapsed(!bottomCollapsed)}>
              {bottomCollapsed ? <ChevronDown size={15} /> : <ChevronDown size={15} className="collapse-arrow" />}
            </button>
          </div>
        </div>

        {!bottomCollapsed ? (
          <div className="bottom-content">
            {bottomTab === "takes" ? (
              <div className="takes-layout">
                <div className="take-browser">
                  <div className="table-header take-row"><span>Name</span><span>Created</span><span>Duration</span><span>Frames</span><span>Status</span></div>
                  <div className="table-body">
                    {state.takes.map((take) => (
                      <button type="button" className={`take-row${selectedTake?.id === take.id ? " selected" : ""}`} key={take.id} onClick={() => chooseTake(take)} onDoubleClick={() => { chooseTake(take); setWorkspace("edit"); setBottomTab("timeline"); }}>
                        <span className="take-name"><FileText size={13} /><strong>{take.name}</strong></span>
                        <span>{new Date(take.startedAt).toLocaleDateString()}</span>
                        <span className="monospace">{formatDuration(take.durationMs, true)}</span>
                        <span>{take.frames}</span>
                        <span className={`take-status ${take.status}`}><i />{take.status}</span>
                      </button>
                    ))}
                  </div>
                </div>
                <aside className="take-information">
                  <div className="take-info-heading"><strong>Take information</strong><span>READ-ONLY</span></div>
                  <label><span>Name</span><input value={selectedTake?.name ?? takeNameDraft} readOnly /></label>
                  <label><span>Notes</span><input value={selectedTake?.notes ?? "Ready to record"} readOnly /></label>
                  <div className="take-info-footer"><span><Clock3 size={12} /> {selectedTake ? formatDuration(selectedTake.durationMs, true) : formatDuration(state.session.elapsedMs, true)}</span><small>Double-click a take to edit</small></div>
                </aside>
              </div>
            ) : null}

            {bottomTab === "timeline" ? (
              <div className="timeline-layout">
                <div className="transport-controls">
                  <button type="button" aria-label="Go to first frame (playback planned)" title="Take playback is planned" disabled><SkipBack size={15} /></button>
                  <button type="button" className="transport-play" aria-label="Play take (playback planned)" title="Take playback is planned" disabled><Play size={16} /></button>
                  <button type="button" aria-label="Go to last frame (playback planned)" title="Take playback is planned" disabled><SkipForward size={15} /></button>
                  <span className="timeline-timecode">00:00:00:00</span>
                  <span className="timeline-rate">60 fps</span>
                  <span className="planned-badge">PLAYBACK PLANNED</span>
                </div>
                <div className="timeline-editor">
                  <div className="track-labels"><div><UserRound size={12} /><span>{primaryAvatar?.name ?? "Avatar"}</span></div><div><Bone size={12} /><span>Motion</span></div><div><Radio size={12} /><span>Sensor quality</span></div></div>
                  <div className="timeline-canvas">
                    <div className="timeline-ruler">
                      {[0, 1, 2, 3, 4, 5, 6, 7, 8].map((tick) => <span key={tick} style={{ left: `${tick * 12.5}%` }}>{String(tick * 2).padStart(2, "0")}:00</span>)}
                    </div>
                    <div className="timeline-track motion"><span className="motion-clip" style={{ width: `${Math.max(8, Math.min(100, ((selectedTake?.durationMs ?? 18000) / 30000) * 100))}%` }}>{selectedTake?.name ?? "Live buffer"}</span></div>
                    <div className="timeline-track quality"><span /><span /><span className="warning-segment" /><span /><span /></div>
                    <i className="playhead" style={{ left: "0%" }} />
                  </div>
                </div>
              </div>
            ) : null}

            {bottomTab === "events" ? (
              <div className="events-table">
                <div className="event-row table-header"><span>Time</span><span>Level</span><span>Source</span><span>Message</span></div>
                <div className="table-body">
                  {state.events.map((event) => (
                    <div className={`event-row ${event.level}`} key={event.id}><span className="monospace">{formatEventTime(event.timestamp)}</span><span className="event-level"><EventIcon level={event.level} />{event.level}</span><span>{event.source}</span><span>{event.message}</span></div>
                  ))}
                </div>
              </div>
            ) : null}

            {bottomTab === "diagnostics" ? (
              <div className="diagnostics-grid">
                <div className="diagnostic-card"><span className="diagnostic-icon"><Activity size={17} /></span><div><span>Received frames</span><strong>{formatNumber(state.diagnostics.receivedFrames)}</strong><small>{state.diagnostics.packetsPerSecond} packets/sec</small></div></div>
                <div className="diagnostic-card"><span className={`diagnostic-icon${state.diagnostics.droppedFrames ? " warning" : ""}`}><AlertTriangle size={17} /></span><div><span>Dropped frames</span><strong>{formatNumber(state.diagnostics.droppedFrames)}</strong><small>{attemptedFrames ? ((state.diagnostics.droppedFrames / attemptedFrames) * 100).toFixed(2) : "0.00"}% of stream</small></div></div>
                <div className="diagnostic-card"><span className="diagnostic-icon"><Zap size={17} /></span><div><span>Stream latency</span><strong>{state.diagnostics.latencyMs.toFixed(1)} <em>ms</em></strong><small>{state.diagnostics.jitterMs.toFixed(1)} ms jitter</small></div></div>
                <div className="diagnostic-card"><span className="diagnostic-icon"><Signal size={17} /></span><div><span>Throughput</span><strong>{(state.diagnostics.bytesPerSecond / 1024).toFixed(1)} <em>KB/s</em></strong><small>{state.connection.transport.toUpperCase()} transport</small></div></div>
                <div className="diagnostics-status-list">
                  <span><i className={`status-dot ${backendOnline ? "good" : "error"}`} /> Local bridge <strong>{backendOnline ? "ONLINE" : "OFFLINE"}</strong></span>
                  <span><i className={`status-dot ${connected ? "good" : "offline"}`} /> Motion provider <strong>{connected ? "CONNECTED" : "OFFLINE"}</strong></span>
                  <span><i className={`status-dot ${!sensorTelemetryAvailable ? "offline" : warningCount ? "warning" : "good"}`} /> Sensor health <strong>{!connected ? "OFFLINE" : !state.capabilities.receiveSensors ? "UNAVAILABLE" : warningCount ? `${warningCount} CHECK` : "NOMINAL"}</strong></span>
                </div>
              </div>
            ) : null}
          </div>
        ) : null}
      </section>

      <footer className="status-bar">
        <div className="status-left">
          <span className={`status-dot ${previewMode ? "offline" : state.connection.status === "error" ? "error" : connected ? "good" : "offline"}`} />
          <strong>{previewMode ? "PREVIEW / OFFLINE" : state.connection.status.toUpperCase()}</strong>
          <span>{previewMode ? "STATIC SAMPLE · NO COMMANDS" : `${state.connection.mode.toUpperCase()} · ${state.connection.transport.toUpperCase()}`}</span>
          <span className="status-separator" />
          <span>{primaryAvatar?.fps ?? 0} FPS</span>
          <span>Frame {primaryAvatar?.frame ?? 0}</span>
          {sensorTelemetryAvailable && warningCount ? <span className="status-warning"><AlertTriangle size={11} /> {warningCount} sensor check{warningCount === 1 ? "" : "s"}</span> : null}
        </div>
        <div className="independence-note"><Info size={11} /> Independent open-source MocapApi companion · Not affiliated with or endorsed by Noitom</div>
        <div className="status-right"><span>{connectionSettings.rotationOrder} rotation</span><span>{connectionSettings.unit === "meters" ? "m" : "cm"}</span></div>
      </footer>

      {lastError ? (
        <div className="error-toast" role="alert"><AlertCircle size={15} /><div><strong>Operator action failed</strong><span>{lastError}</span></div><button type="button" aria-label="Dismiss error" onClick={() => setLastError(null)}><X size={14} /></button></div>
      ) : null}

      <ConnectionDialog
        open={connectionDialogOpen}
        settings={connectionSettings}
        capabilities={state.capabilities}
        backendOnline={backendOnline}
        onClose={() => setConnectionDialogOpen(false)}
        onConnect={connectNow}
      />
      <CalibrationDialog
        open={calibrationDialogOpen}
        performerName={primaryAvatar?.name ?? "All active performers"}
        allowed={!calibrationDisabled}
        disabledReason={calibrationDisabled}
        onClose={() => setCalibrationDialogOpen(false)}
        onStart={() => execute("start_calibration")}
        onNext={() => execute("calibration_next")}
        onCancel={() => execute("calibration_cancel")}
      />
    </main>
  );
}
