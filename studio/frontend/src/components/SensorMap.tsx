import { Battery, Radio, ShieldAlert } from "lucide-react";
import type { Sensor } from "../types";

interface SensorMapProps {
  sensors: Sensor[];
  selectedSensorId: number | null;
  available: boolean;
  connected: boolean;
  onSelect: (sensor: Sensor) => void;
}

const SENSOR_POSITIONS = [
  [50, 55], [50, 45], [50, 36], [50, 29], [50, 12],
  [31, 29], [21, 42], [15, 54], [69, 29], [79, 42], [85, 54],
  [40, 60], [39, 75], [37, 91], [60, 60], [61, 75], [63, 91], [50, 22],
] as const;

function sensorTone(sensor: Sensor) {
  if (!sensor.connected) return "offline";
  if (sensor.signal < 55) return "danger";
  if (sensor.signal < 75 || sensor.magnetic === "unstable") return "warning";
  return "good";
}

export function SensorMap({ sensors, selectedSensorId, available, connected, onSelect }: SensorMapProps) {
  const connectedCount = sensors.filter((sensor) => sensor.connected).length;
  const unstable = sensors.filter((sensor) => sensor.magnetic === "unstable").length;
  const averageBattery = sensors.length
    ? Math.round(sensors.reduce((sum, sensor) => sum + sensor.battery, 0) / sensors.length)
    : 0;

  return (
    <div className="sensor-map-view">
      <div className="sensor-summary-row">
        <span><Radio size={12} /> {available ? `${connectedCount}/${sensors.length}` : "— / —"}</span>
        <span><Battery size={12} /> {available ? `${averageBattery}%` : "—"}</span>
        <span className={available && unstable ? "warning-text" : ""}>
          <ShieldAlert size={12} /> {available ? `${unstable} mag` : "— mag"}
        </span>
      </div>

      {!available ? (
        <div className="telemetry-unavailable-banner" role="status">
          <Radio size={14} />
          <div><strong>Sensor telemetry {connected ? "unavailable" : "offline"}</strong><span>{connected ? "Current provider does not expose sensors" : "Connect a telemetry-capable provider"}</span></div>
        </div>
      ) : null}

      <div className="sensor-figure" aria-label="Body sensor map">
        <svg viewBox="0 0 100 110" aria-hidden="true" className="sensor-silhouette">
          <defs>
            <linearGradient id="body-fill" x1="0" x2="1" y1="0" y2="1">
              <stop offset="0" stopColor="#565d67" />
              <stop offset="1" stopColor="#343a42" />
            </linearGradient>
          </defs>
          <circle cx="50" cy="11" r="8" />
          <path d="M41 22 C44 20 56 20 59 22 L65 51 C62 57 57 59 50 59 C43 59 38 57 35 51 Z" />
          <path d="M38 25 L29 28 L16 52 L22 56 L38 36 Z" />
          <path d="M62 25 L71 28 L84 52 L78 56 L62 36 Z" />
          <path d="M41 55 L49 57 L46 94 L36 94 Z" />
          <path d="M59 55 L51 57 L54 94 L64 94 Z" />
          <path d="M35 93 L47 93 L46 103 L30 103 Z" />
          <path d="M65 93 L53 93 L54 103 L70 103 Z" />
          <circle cx="18" cy="56" r="5" />
          <circle cx="82" cy="56" r="5" />
        </svg>

        <span className="side-mark left">L</span>
        <span className="side-mark right">R</span>
        {sensors.map((sensor, index) => {
          const [left, top] = SENSOR_POSITIONS[index] ?? [50, 50];
          const tone = available ? sensorTone(sensor) : "offline";
          return (
            <button
              type="button"
              key={sensor.id}
              className={`sensor-node ${tone}${selectedSensorId === sensor.id ? " selected" : ""}`}
              style={{ left: `${left}%`, top: `${top}%` }}
              onClick={() => onSelect(sensor)}
              aria-label={available ? `${sensor.bodyPart}: ${sensor.signal}% signal, ${sensor.magnetic} magnetic field` : `${sensor.bodyPart}: telemetry unavailable`}
              title={available ? `${sensor.bodyPart} · ${sensor.signal}% · ${sensor.magnetic}` : `${sensor.bodyPart} · telemetry unavailable`}
            >
              <span className="signal-dot" />
              <span className="magnetic-marker" />
            </button>
          );
        })}
      </div>

      <div className="sensor-map-legend" aria-label="Sensor map legend">
        <span><i className="legend-dot good" />Strong</span>
        <span><i className="legend-dot warning" />Check</span>
        <span><i className="legend-square" />Magnetic</span>
      </div>
    </div>
  );
}
