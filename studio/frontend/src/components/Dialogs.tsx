import { useEffect, useId, useState } from "react";
import {
  Activity,
  AlertTriangle,
  Check,
  ChevronRight,
  CircleHelp,
  Radio,
  Settings2,
  ShieldCheck,
  X,
} from "lucide-react";
import type { Capabilities, ConnectionSettings } from "../types";

interface ConnectionDialogProps {
  open: boolean;
  settings: ConnectionSettings;
  capabilities: Capabilities;
  backendOnline: boolean;
  onClose: () => void;
  onConnect: (settings: ConnectionSettings) => Promise<void> | void;
}

export function ConnectionDialog({
  open,
  settings,
  capabilities,
  backendOnline,
  onClose,
  onConnect,
}: ConnectionDialogProps) {
  const titleId = useId();
  const [draft, setDraft] = useState(settings);
  const [submitting, setSubmitting] = useState(false);

  useEffect(() => {
    if (open) setDraft(settings);
  }, [open, settings]);

  if (!open) return null;

  const submit = async (event: React.FormEvent) => {
    event.preventDefault();
    setSubmitting(true);
    try {
      await onConnect(draft);
      onClose();
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div className="dialog-backdrop" role="presentation" onMouseDown={onClose}>
      <section
        className="studio-dialog connection-dialog"
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        onMouseDown={(event) => event.stopPropagation()}
      >
        <header className="dialog-header">
          <div className="dialog-title-group">
            <span className="dialog-icon"><Radio size={16} /></span>
            <div>
              <h2 id={titleId}>Connection & streaming</h2>
              <p>Choose a solved-motion provider and coordinate profile.</p>
            </div>
          </div>
          <button type="button" className="icon-button" aria-label="Close connection dialog" onClick={onClose}>
            <X size={15} />
          </button>
        </header>

        <form onSubmit={submit}>
          <div className="dialog-body two-column-form">
            <fieldset>
              <legend>Provider</legend>
              <label className="field span-two">
                <span>Connection mode</span>
                <select
                  value={draft.mode}
                  onChange={(event) => setDraft({ ...draft, mode: event.target.value as ConnectionSettings["mode"] })}
                >
                  <option value="demo">Standalone demo</option>
                  <option value="mocap-api">MocapApi command server</option>
                  <option value="bvh">BVH motion stream</option>
                </select>
              </label>
              <label className="field">
                <span>Transport</span>
                <select
                  value={draft.transport}
                  onChange={(event) => setDraft({ ...draft, transport: event.target.value as ConnectionSettings["transport"] })}
                >
                  <option value="udp">UDP</option>
                  <option value="tcp">TCP</option>
                </select>
              </label>
              <label className="field">
                <span>Port</span>
                <input
                  type="number"
                  min={1}
                  max={65535}
                  value={draft.port}
                  onChange={(event) => setDraft({ ...draft, port: Number(event.target.value) })}
                />
              </label>
              <label className="field span-two">
                <span>Host</span>
                <input
                  value={draft.host}
                  inputMode="url"
                  spellCheck={false}
                  onChange={(event) => setDraft({ ...draft, host: event.target.value })}
                />
              </label>
            </fieldset>

            <fieldset>
              <legend>Coordinate profile</legend>
              <label className="field">
                <span>Rotation order</span>
                <select
                  value={draft.rotationOrder}
                  onChange={(event) => setDraft({ ...draft, rotationOrder: event.target.value as ConnectionSettings["rotationOrder"] })}
                >
                  {(["XYZ", "XZY", "YXZ", "YZX", "ZXY", "ZYX"] as const).map((order) => (
                    <option value={order} key={order}>{order}</option>
                  ))}
                </select>
              </label>
              <label className="field">
                <span>Up axis</span>
                <select
                  value={draft.upAxis}
                  onChange={(event) => setDraft({ ...draft, upAxis: event.target.value as ConnectionSettings["upAxis"] })}
                >
                  <option value="+X">+X</option>
                  <option value="+Y">+Y</option>
                  <option value="+Z">+Z</option>
                </select>
              </label>
              <label className="field">
                <span>Handedness</span>
                <select
                  value={draft.handedness}
                  onChange={(event) => setDraft({ ...draft, handedness: event.target.value as ConnectionSettings["handedness"] })}
                >
                  <option value="right">Right-handed</option>
                  <option value="left">Left-handed</option>
                </select>
              </label>
              <label className="field">
                <span>Units</span>
                <select
                  value={draft.unit}
                  onChange={(event) => setDraft({ ...draft, unit: event.target.value as ConnectionSettings["unit"] })}
                >
                  <option value="meters">Meters</option>
                  <option value="centimeters">Centimeters</option>
                </select>
              </label>
              <div className="profile-callout span-two">
                <Settings2 size={14} />
                <span>These settings shape MocapApi output; they do not alter the provider’s solver.</span>
              </div>
            </fieldset>

            <div className="capability-preview span-all">
              <div className="capability-preview-title">
                <ShieldCheck size={14} /> Current provider capabilities
              </div>
              <div className="capability-chips">
                <span className={capabilities.receiveMotion ? "available" : "unavailable"}>Motion</span>
                <span className={capabilities.receiveSensors ? "available" : "unavailable"}>Telemetry</span>
                <span className={capabilities.serverCommands ? "available" : "unavailable"}>Commands</span>
                <span className={capabilities.calibrationCommands ? "available" : "unavailable"}>Calibration</span>
                <span className={capabilities.axisRecording ? "available" : "unavailable"}>Provider record</span>
              </div>
              <p>{capabilities.reason ?? "Capabilities are negotiated after connection."}</p>
            </div>
          </div>

          <footer className="dialog-footer">
            <div className="backend-indicator">
              <span className={`status-dot ${backendOnline ? "good" : "warning"}`} />
              {backendOnline ? "Local bridge ready" : "Demo fallback active"}
            </div>
            <button type="button" className="secondary-button" onClick={onClose}>Cancel</button>
            <button type="submit" className="primary-button" disabled={submitting || !draft.host || !draft.port}>
              {submitting ? <Activity size={14} className="spin" /> : <Radio size={14} />}
              {submitting ? "Connecting…" : "Connect"}
            </button>
          </footer>
        </form>
      </section>
    </div>
  );
}

interface CalibrationDialogProps {
  open: boolean;
  performerName: string;
  allowed: boolean;
  disabledReason?: string;
  onClose: () => void;
  onStart: () => Promise<void> | void;
  onNext: () => Promise<void> | void;
  onCancel: () => Promise<void> | void;
}

export function CalibrationDialog({
  open,
  performerName,
  allowed,
  disabledReason,
  onClose,
  onStart,
  onNext,
  onCancel,
}: CalibrationDialogProps) {
  const titleId = useId();
  const [started, setStarted] = useState(false);

  useEffect(() => {
    if (!open) setStarted(false);
  }, [open]);

  if (!open) return null;

  const start = async () => {
    await onStart();
    setStarted(true);
  };

  const cancel = async () => {
    if (started) await onCancel();
    onClose();
  };

  return (
    <div className="dialog-backdrop" role="presentation" onMouseDown={() => void cancel()}>
      <section
        className="studio-dialog calibration-dialog"
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        onMouseDown={(event) => event.stopPropagation()}
      >
        <header className="dialog-header">
          <div className="dialog-title-group">
            <span className="dialog-icon"><Activity size={16} /></span>
            <div>
              <h2 id={titleId}>Provider-guided calibration</h2>
              <p>{performerName}</p>
            </div>
          </div>
          <button type="button" className="icon-button" aria-label="Close calibration dialog" onClick={() => void cancel()}>
            <X size={15} />
          </button>
        </header>

        <div className="dialog-body calibration-body">
          <div className="calibration-visual" aria-hidden="true">
            <div className="pose-figure">
              <span className="pose-head" />
              <span className="pose-torso" />
              <span className="pose-arm left" />
              <span className="pose-arm right" />
              <span className="pose-leg left" />
              <span className="pose-leg right" />
              <i className="pose-sensor one" />
              <i className="pose-sensor two" />
              <i className="pose-sensor three" />
              <i className="pose-sensor four" />
            </div>
            <div className="pose-floor" />
          </div>

          <div className="calibration-copy">
            <span className="eyebrow">{started ? "Calibration in progress" : "Before you begin"}</span>
            <h3>{started ? "Follow the provider’s pose prompt" : "Check performer and sensor readiness"}</h3>
            {started ? (
              <>
                <div className="provider-progress" role="status" aria-live="polite">
                  <span className="provider-progress-bar" />
                  Waiting for provider progress…
                </div>
                <p>
                  Pose timing and calibration quality are determined by the connected motion provider.
                  Use Next only when its prompt asks you to continue.
                </p>
              </>
            ) : (
              <ul className="readiness-list">
                <li><Check size={13} /> Performer is facing the original calibration direction.</li>
                <li><Check size={13} /> Sensors are secure and have stable signal.</li>
                <li><Check size={13} /> Performer has room to move and can see provider prompts.</li>
              </ul>
            )}

            {!allowed ? (
              <div className="inline-warning">
                <AlertTriangle size={14} />
                <span>{disabledReason ?? "Calibration commands are unavailable for this provider."}</span>
              </div>
            ) : null}
            <div className="calibration-note">
              <CircleHelp size={14} />
              This console relays MocapApi controls; it does not reproduce the proprietary calibration solver.
            </div>
          </div>
        </div>

        <footer className="dialog-footer">
          <button type="button" className="secondary-button" onClick={() => void cancel()}>
            {started ? "Cancel calibration" : "Close"}
          </button>
          {started ? (
            <button type="button" className="primary-button" onClick={() => void onNext()}>
              Provider Next <ChevronRight size={14} />
            </button>
          ) : (
            <button type="button" className="primary-button" onClick={() => void start()} disabled={!allowed}>
              Start calibration <ChevronRight size={14} />
            </button>
          )}
        </footer>
      </section>
    </div>
  );
}
