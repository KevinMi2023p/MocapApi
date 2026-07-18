import { useCallback, useEffect, useId, useRef, useState } from "react";
import type { FormEvent, RefObject } from "react";
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

const FOCUSABLE = [
  "button:not([disabled])",
  "input:not([disabled])",
  "select:not([disabled])",
  "textarea:not([disabled])",
  "[href]",
  "[tabindex]:not([tabindex='-1'])",
].join(",");

function useModalFocus(
  open: boolean,
  dialogRef: RefObject<HTMLElement>,
  initialFocusRef: RefObject<HTMLElement>,
  onEscape: () => void,
) {
  const restoreFocus = useRef<HTMLElement | null>(null);
  const escapeHandler = useRef(onEscape);

  useEffect(() => {
    escapeHandler.current = onEscape;
  }, [onEscape]);

  useEffect(() => {
    if (!open) return;
    restoreFocus.current = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    const focusInitial = window.requestAnimationFrame(() => {
      const preferred = initialFocusRef.current;
      if (preferred && !preferred.hasAttribute("disabled")) {
        preferred.focus();
        return;
      }
      dialogRef.current?.querySelector<HTMLElement>(FOCUSABLE)?.focus();
    });

    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        event.preventDefault();
        escapeHandler.current();
        return;
      }
      if (event.key !== "Tab" || !dialogRef.current) return;
      const focusable = Array.from(dialogRef.current.querySelectorAll<HTMLElement>(FOCUSABLE));
      if (!focusable.length) return;
      const first = focusable[0];
      const last = focusable[focusable.length - 1];
      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault();
        first.focus();
      }
    };

    document.addEventListener("keydown", handleKeyDown);
    return () => {
      window.cancelAnimationFrame(focusInitial);
      document.removeEventListener("keydown", handleKeyDown);
      restoreFocus.current?.focus();
      restoreFocus.current = null;
    };
  }, [dialogRef, initialFocusRef, open]);
}

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
  const dialogRef = useRef<HTMLElement>(null);
  const initialFocusRef = useRef<HTMLSelectElement>(null);
  const [draft, setDraft] = useState(settings);
  const [submitting, setSubmitting] = useState(false);
  const [connectionError, setConnectionError] = useState<string | null>(null);

  useEffect(() => {
    if (open) {
      setDraft(settings);
      setConnectionError(null);
    }
  }, [open, settings]);

  useModalFocus(open, dialogRef, initialFocusRef, onClose);

  if (!open) return null;

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    if (!backendOnline) {
      setConnectionError("Local bridge is offline. Start it before connecting a provider.");
      return;
    }
    setConnectionError(null);
    setSubmitting(true);
    try {
      await onConnect(draft);
      onClose();
    } catch (cause) {
      setConnectionError(cause instanceof Error ? cause.message : "Connection failed");
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div className="dialog-backdrop" role="presentation" onMouseDown={onClose}>
      <section
        ref={dialogRef}
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
              <p>Choose a solved-motion provider and supported output profile.</p>
            </div>
          </div>
          <button type="button" className="icon-button" aria-label="Close connection dialog" onClick={onClose}>
            <X size={15} />
          </button>
        </header>

        <form onSubmit={(event) => void submit(event)}>
          <div className="dialog-body two-column-form">
            <fieldset>
              <legend>Provider</legend>
              <label className="field span-two">
                <span>Connection mode</span>
                <select
                  ref={initialFocusRef}
                  value={draft.mode}
                  onChange={(event) => setDraft({ ...draft, mode: event.target.value as ConnectionSettings["mode"] })}
                >
                  <option value="demo">Bridge-hosted demo</option>
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
              <label className="field unavailable-field" title="Up-axis conversion is not implemented by the current bridge">
                <span>Up axis <em>Unavailable</em></span>
                <select value={draft.upAxis} disabled aria-label="Up axis unavailable">
                  <option value={draft.upAxis}>{draft.upAxis}</option>
                </select>
              </label>
              <label className="field unavailable-field" title="Handedness conversion is not implemented by the current bridge">
                <span>Handedness <em>Unavailable</em></span>
                <select value={draft.handedness} disabled aria-label="Handedness unavailable">
                  <option value={draft.handedness}>{draft.handedness === "right" ? "Right-handed" : "Left-handed"}</option>
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
                <span>Rotation order and units are supported. Up axis and handedness are read-only until bridge conversion is implemented.</span>
              </div>
            </fieldset>

            <div className="capability-preview span-all">
              <div className="capability-preview-title">
                <ShieldCheck size={14} /> Last reported provider capabilities
              </div>
              <div className="capability-chips">
                <span className={capabilities.receiveMotion ? "available" : "unavailable"}>Motion</span>
                <span className={capabilities.receiveSensors ? "available" : "unavailable"}>Telemetry</span>
                <span className={capabilities.serverCommands ? "available" : "unavailable"}>Commands</span>
                <span className={capabilities.calibrationCommands ? "available" : "unavailable"}>Calibration</span>
                <span className={capabilities.axisRecording ? "available" : "unavailable"}>Provider record</span>
              </div>
              <p>{backendOnline ? capabilities.reason ?? "Capabilities are negotiated after connection." : "Static preview values; the bridge is offline."}</p>
            </div>

            {connectionError ? (
              <div className="dialog-error span-all" role="alert"><AlertTriangle size={14} /> {connectionError}</div>
            ) : null}
          </div>

          <footer className="dialog-footer">
            <div className="backend-indicator">
              <span className={`status-dot ${backendOnline ? "good" : "error"}`} />
              {backendOnline ? "Local bridge ready" : "Bridge offline · preview only"}
            </div>
            <button type="button" className="secondary-button" onClick={onClose}>Cancel</button>
            <button type="submit" className="primary-button" disabled={!backendOnline || submitting || !draft.host || !draft.port}>
              {submitting ? <Activity size={14} className="spin" /> : <Radio size={14} />}
              {submitting ? "Connecting…" : backendOnline ? "Connect" : "Bridge offline"}
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
  const dialogRef = useRef<HTMLElement>(null);
  const initialFocusRef = useRef<HTMLButtonElement>(null);
  const [started, setStarted] = useState(false);
  const [busy, setBusy] = useState(false);
  const [commandError, setCommandError] = useState<string | null>(null);

  useEffect(() => {
    if (!open) {
      setStarted(false);
      setBusy(false);
      setCommandError(null);
    }
  }, [open]);

  const start = useCallback(async () => {
    setBusy(true);
    setCommandError(null);
    try {
      await onStart();
      setStarted(true);
    } catch (cause) {
      setCommandError(cause instanceof Error ? cause.message : "Calibration could not start");
    } finally {
      setBusy(false);
    }
  }, [onStart]);

  const next = useCallback(async () => {
    setBusy(true);
    setCommandError(null);
    try {
      await onNext();
    } catch (cause) {
      setCommandError(cause instanceof Error ? cause.message : "Provider rejected the next step");
    } finally {
      setBusy(false);
    }
  }, [onNext]);

  const cancel = useCallback(async () => {
    if (busy) return;
    setCommandError(null);
    if (started) {
      setBusy(true);
      try {
        await onCancel();
      } catch (cause) {
        setCommandError(cause instanceof Error ? cause.message : "Calibration cancellation failed");
        setBusy(false);
        return;
      }
      setBusy(false);
    }
    onClose();
  }, [busy, onCancel, onClose, started]);

  useModalFocus(open, dialogRef, initialFocusRef, () => void cancel());

  if (!open) return null;

  return (
    <div className="dialog-backdrop" role="presentation" onMouseDown={() => void cancel()}>
      <section
        ref={dialogRef}
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
          <button type="button" className="icon-button" aria-label="Close calibration dialog" onClick={() => void cancel()} disabled={busy}>
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
            {commandError ? (
              <div className="dialog-error" role="alert"><AlertTriangle size={14} /> {commandError}</div>
            ) : null}
            <div className="calibration-note">
              <CircleHelp size={14} />
              This console relays MocapApi controls; it does not reproduce the proprietary calibration solver.
            </div>
          </div>
        </div>

        <footer className="dialog-footer">
          <button type="button" className="secondary-button" onClick={() => void cancel()} disabled={busy}>
            {started ? "Cancel calibration" : "Close"}
          </button>
          {started ? (
            <button ref={initialFocusRef} type="button" className="primary-button" onClick={() => void next()} disabled={busy}>
              {busy ? <Activity size={14} className="spin" /> : null} Provider Next <ChevronRight size={14} />
            </button>
          ) : (
            <button ref={initialFocusRef} type="button" className="primary-button" onClick={() => void start()} disabled={!allowed || busy}>
              {busy ? <Activity size={14} className="spin" /> : null} Start calibration <ChevronRight size={14} />
            </button>
          )}
        </footer>
      </section>
    </div>
  );
}
