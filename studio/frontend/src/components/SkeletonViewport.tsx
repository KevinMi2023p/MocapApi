import { useEffect, useMemo, useRef } from "react";
import { Canvas } from "@react-three/fiber";
import {
  GizmoHelper,
  GizmoViewport,
  Grid,
  Line,
  OrbitControls,
  PerspectiveCamera,
} from "@react-three/drei";
import {
  Box,
  Crosshair,
  Eye,
  EyeOff,
  Focus,
  Move3d,
  Rotate3d,
  ScanLine,
} from "lucide-react";
import type { PerspectiveCamera as PerspectiveCameraType } from "three";
import type { Avatar, Joint, Sensor } from "../types";

export type CameraView = "perspective" | "front" | "right";

interface SkeletonViewportProps {
  avatar?: Avatar;
  sensors: Sensor[];
  selectedJointId: string | null;
  selectedSensorId: number | null;
  capturing: boolean;
  preview: boolean;
  cameraView: CameraView;
  cameraRevision: number;
  followActor: boolean;
  showLabels: boolean;
  showSensors: boolean;
  onCameraViewChange: (view: CameraView) => void;
  onResetCamera: () => void;
  onFollowActorChange: (value: boolean) => void;
  onShowLabelsChange: (value: boolean) => void;
  onShowSensorsChange: (value: boolean) => void;
  onJointSelect: (joint: Joint) => void;
}

const CAMERA_POSITIONS: Record<CameraView, [number, number, number]> = {
  perspective: [2.7, 1.75, 3.4],
  front: [0, 1.25, 4.2],
  right: [4.2, 1.25, 0],
};

function CameraController({
  view,
  revision,
}: {
  view: CameraView;
  revision: number;
}) {
  const camera = useRef<PerspectiveCameraType>(null);

  useEffect(() => {
    if (!camera.current) return;
    camera.current.position.set(...CAMERA_POSITIONS[view]);
    camera.current.lookAt(0, 1, 0);
    camera.current.updateProjectionMatrix();
  }, [revision, view]);

  return (
    <PerspectiveCamera
      ref={camera}
      makeDefault
      fov={38}
      near={0.05}
      far={80}
      position={CAMERA_POSITIONS[view]}
    />
  );
}

function signalColor(sensor?: Sensor) {
  if (!sensor?.connected) return "#6e747c";
  if (sensor.signal < 55) return "#f85c1d";
  if (sensor.signal < 75 || sensor.magnetic === "unstable") return "#f2c14e";
  return "#47de86";
}

function SkeletonModel({
  avatar,
  sensors,
  selectedJointId,
  selectedSensorId,
  showSensors,
  onJointSelect,
}: Pick<
  SkeletonViewportProps,
  | "avatar"
  | "sensors"
  | "selectedJointId"
  | "selectedSensorId"
  | "showSensors"
  | "onJointSelect"
>) {
  const jointsByName = useMemo(
    () => new Map(avatar?.joints.map((joint) => [joint.name, joint]) ?? []),
    [avatar],
  );
  const sensorsById = useMemo(
    () => new Map(sensors.map((sensor) => [sensor.id, sensor])),
    [sensors],
  );

  if (!avatar) return null;

  return (
    <group>
      {avatar.joints.map((joint) => {
        if (!joint.parent) return null;
        const parent = jointsByName.get(joint.parent);
        if (!parent) return null;
        return (
          <Line
            key={`bone-${joint.id}`}
            points={[
              [parent.position.x, parent.position.y, parent.position.z],
              [joint.position.x, joint.position.y, joint.position.z],
            ]}
            color={joint.id === selectedJointId ? "#69b8f4" : "#9aa4b1"}
            lineWidth={joint.id === selectedJointId ? 3 : 2}
            transparent
            opacity={0.96}
          />
        );
      })}

      {avatar.joints.map((joint) => {
        const sensor = joint.sensorId ? sensorsById.get(joint.sensorId) : undefined;
        const selected = joint.id === selectedJointId;
        const sensorSelected = sensor?.id === selectedSensorId;
        return (
          <group
            key={joint.id}
            position={[joint.position.x, joint.position.y, joint.position.z]}
          >
            <mesh
              onClick={(event) => {
                event.stopPropagation();
                onJointSelect(joint);
              }}
              scale={selected ? 1.25 : 1}
            >
              <sphereGeometry args={[joint.name === "Head" ? 0.09 : 0.035, 18, 18]} />
              <meshStandardMaterial
                color={selected ? "#8dd6ff" : "#c4ccd6"}
                emissive={selected ? "#23658a" : "#20252b"}
                emissiveIntensity={selected ? 0.9 : 0.22}
                roughness={0.32}
                metalness={0.18}
              />
            </mesh>
            {showSensors && sensor ? (
              <mesh rotation={[Math.PI / 2, 0, 0]} scale={sensorSelected ? 1.18 : 1}>
                <torusGeometry args={[0.058, 0.012, 10, 24]} />
                <meshBasicMaterial color={signalColor(sensor)} />
              </mesh>
            ) : null}
          </group>
        );
      })}

      <mesh position={[0, 1.38, -0.018]} scale={[0.25, 0.38, 0.07]}>
        <capsuleGeometry args={[0.3, 0.7, 8, 16]} />
        <meshStandardMaterial
          color="#59616c"
          transparent
          opacity={0.24}
          roughness={0.8}
          depthWrite={false}
        />
      </mesh>
      <mesh position={[-0.1, 0.74, -0.018]} rotation={[0, 0, 0.03]} scale={[0.13, 0.44, 0.08]}>
        <capsuleGeometry args={[0.28, 0.8, 8, 16]} />
        <meshStandardMaterial color="#59616c" transparent opacity={0.2} depthWrite={false} />
      </mesh>
      <mesh position={[0.1, 0.74, -0.018]} rotation={[0, 0, -0.03]} scale={[0.13, 0.44, 0.08]}>
        <capsuleGeometry args={[0.28, 0.8, 8, 16]} />
        <meshStandardMaterial color="#59616c" transparent opacity={0.2} depthWrite={false} />
      </mesh>
    </group>
  );
}

function ViewButton({
  label,
  active = false,
  children,
  onClick,
}: {
  label: string;
  active?: boolean;
  children: React.ReactNode;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      className={`viewport-icon-button${active ? " active" : ""}`}
      aria-label={label}
      title={label}
      onClick={onClick}
    >
      {children}
    </button>
  );
}

export function SkeletonViewport(props: SkeletonViewportProps) {
  const selectedJoint = props.avatar?.joints.find((joint) => joint.id === props.selectedJointId);

  return (
    <section className="viewport-panel" aria-label="3D motion preview">
      <div className="viewport-toolbar">
        <div className="viewport-toolbar-group" aria-label="Camera views">
          <ViewButton
            label="Perspective view"
            active={props.cameraView === "perspective"}
            onClick={() => props.onCameraViewChange("perspective")}
          >
            <Box size={14} />
          </ViewButton>
          <ViewButton
            label="Front view"
            active={props.cameraView === "front"}
            onClick={() => props.onCameraViewChange("front")}
          >
            <ScanLine size={14} />
          </ViewButton>
          <ViewButton
            label="Right view"
            active={props.cameraView === "right"}
            onClick={() => props.onCameraViewChange("right")}
          >
            <Move3d size={14} />
          </ViewButton>
          <span className="toolbar-divider" />
          <ViewButton label="Reset camera" onClick={props.onResetCamera}>
            <Focus size={14} />
          </ViewButton>
          <ViewButton
            label="Follow performer"
            active={props.followActor}
            onClick={() => props.onFollowActorChange(!props.followActor)}
          >
            <Crosshair size={14} />
          </ViewButton>
          <ViewButton
            label={props.showLabels ? "Hide labels" : "Show labels"}
            active={props.showLabels}
            onClick={() => props.onShowLabelsChange(!props.showLabels)}
          >
            {props.showLabels ? <Eye size={14} /> : <EyeOff size={14} />}
          </ViewButton>
          <ViewButton
            label={props.showSensors ? "Hide sensor markers" : "Show sensor markers"}
            active={props.showSensors}
            onClick={() => props.onShowSensorsChange(!props.showSensors)}
          >
            <Rotate3d size={14} />
          </ViewButton>
        </div>
        <div className="viewport-readout" aria-live="polite">
          <span>{props.avatar?.name ?? "No avatar"}</span>
          <span className={props.capturing ? "live" : props.preview ? "preview" : ""}>
            {props.preview ? "PREVIEW / OFFLINE" : props.capturing ? "LIVE" : "IDLE"}
          </span>
          <span>{props.avatar?.fps ?? 0} FPS</span>
        </div>
      </div>

      <div className="viewport-canvas" role="img" aria-label="Interactive 3D skeleton viewport">
        <Canvas dpr={[1, 1.75]} gl={{ antialias: true }}>
          <color attach="background" args={["#252a31"]} />
          <fog attach="fog" args={["#252a31", 4.5, 9]} />
          <CameraController view={props.cameraView} revision={props.cameraRevision} />
          <ambientLight intensity={1.35} />
          <directionalLight position={[3, 5, 4]} intensity={2.2} color="#d9efff" />
          <directionalLight position={[-4, 2, -2]} intensity={0.7} color="#69b8f4" />
          <Grid
            args={[14, 14]}
            position={[0, 0, 0]}
            cellSize={0.25}
            cellThickness={0.35}
            cellColor="#4a5059"
            sectionSize={1}
            sectionThickness={0.7}
            sectionColor="#68717d"
            fadeDistance={8}
            fadeStrength={1.6}
            infiniteGrid
          />
          <SkeletonModel
            avatar={props.avatar}
            sensors={props.sensors}
            selectedJointId={props.selectedJointId}
            selectedSensorId={props.selectedSensorId}
            showSensors={props.showSensors}
            onJointSelect={props.onJointSelect}
          />
          <OrbitControls
            makeDefault
            target={[0, props.followActor ? 1.05 : 0.95, 0]}
            minDistance={1.1}
            maxDistance={10}
            minPolarAngle={0.1}
            maxPolarAngle={Math.PI / 2.01}
            enableDamping
            dampingFactor={0.08}
          />
          <GizmoHelper alignment="bottom-left" margin={[58, 48]}>
            <GizmoViewport
              axisColors={["#d65555", "#55bb76", "#4e83d1"]}
              labelColor="#dce2e8"
            />
          </GizmoHelper>
        </Canvas>

        {props.showLabels && props.avatar ? (
          <div className="avatar-label" aria-hidden="true">
            <span className={`avatar-label-dot${props.preview ? " preview" : ""}`} />
            {props.avatar.name}
          </div>
        ) : null}
        {selectedJoint ? (
          <div className="selection-chip" aria-live="polite">
            <Crosshair size={12} />
            {selectedJoint.name}
          </div>
        ) : null}
        <div className="viewport-hint">Drag to orbit · Shift-drag to pan · Scroll to zoom</div>
      </div>
    </section>
  );
}
