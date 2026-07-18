import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import App from "./App";
import { initialState } from "./demoState";
import type { StudioState } from "./types";

const apiMocks = vi.hoisted(() => ({
  fetchState: vi.fn(),
  connect: vi.fn(),
  disconnect: vi.fn(),
  sendCommand: vi.fn(),
  subscribeToState: vi.fn(),
}));

vi.mock("./api", () => apiMocks);

vi.mock("./components/SkeletonViewport", () => ({
  SkeletonViewport: ({ avatar, onJointSelect, preview }: {
    avatar?: { joints: Array<{ id: string; name: string }> };
    preview: boolean;
    onJointSelect: (joint: { id: string; name: string }) => void;
  }) => (
    <section aria-label="3D motion preview">
      <span>{preview ? "Viewport preview offline" : "Interactive skeleton viewport"}</span>
      {avatar?.joints[1] ? (
        <button type="button" onClick={() => onJointSelect(avatar.joints[1])}>
          Select {avatar.joints[1].name}
        </button>
      ) : null}
    </section>
  ),
}));

const studioState = (
  status: StudioState["connection"]["status"],
  session: Partial<StudioState["session"]> = {},
): StudioState => ({
  ...initialState,
  connection: {
    ...initialState.connection,
    status,
    message: status === "connected" ? "Provider ready" : "Ready",
  },
  session: { ...initialState.session, ...session },
});

describe("Mocap Studio operator console", () => {
  let pushState: ((state: StudioState) => void) | undefined;
  let pushStreamError: ((error: Error) => void) | undefined;

  beforeEach(() => {
    pushState = undefined;
    pushStreamError = undefined;
    apiMocks.fetchState.mockRejectedValue(new Error("No backend in component test"));
    apiMocks.connect.mockResolvedValue(undefined);
    apiMocks.disconnect.mockResolvedValue(undefined);
    apiMocks.sendCommand.mockResolvedValue(undefined);
    apiMocks.subscribeToState.mockImplementation((
      onState: (state: StudioState) => void,
      onError: (error: Error) => void,
    ) => {
      pushState = onState;
      pushStreamError = onError;
      return () => {};
    });
  });

  it("renders a clearly offline, visual-only preview and disables connection submission", async () => {
    render(<App />);

    expect(screen.getByLabelText("Mocap Studio")).toBeInTheDocument();
    expect(screen.getByRole("navigation", { name: "Workspace" })).toBeInTheDocument();
    expect(screen.getByRole("region", { name: "3D motion preview" })).toHaveTextContent("preview offline");
    expect(screen.getByRole("complementary", { name: "Suits and scene" })).toBeInTheDocument();
    expect(screen.getByRole("complementary", { name: "Joint and sensor inspector" })).toBeInTheDocument();
    expect(screen.getByText(/Independent open-source MocapApi companion/i)).toBeInTheDocument();
    expect(screen.getAllByText(/PREVIEW \/ OFFLINE/i).length).toBeGreaterThan(0);
    expect(screen.queryByText("NOMINAL")).not.toBeInTheDocument();
    expect(screen.getAllByText("OFFLINE").length).toBeGreaterThan(0);

    const controls = within(screen.getByRole("region", { name: "Capture controls" }));
    expect(controls.getByRole("button", { name: "Capture" })).toBeDisabled();
    expect(controls.getByRole("button", { name: "Connect" })).toBeDisabled();

    const opener = screen.getByRole("button", { name: "Connection settings" });
    opener.focus();
    fireEvent.click(opener);
    const dialog = screen.getByRole("dialog", { name: "Connection & streaming" });
    expect(within(dialog).getByRole("button", { name: "Bridge offline" })).toBeDisabled();
    expect(within(dialog).getByRole("combobox", { name: "Up axis unavailable" })).toBeDisabled();
    expect(within(dialog).getByRole("combobox", { name: "Handedness unavailable" })).toBeDisabled();
    expect(within(dialog).getByRole("option", { name: "MocapApi runtime (planned)" })).toBeDisabled();
    await waitFor(() => expect(within(dialog).getByRole("combobox", { name: "Connection mode" })).toHaveFocus());

    fireEvent.keyDown(document, { key: "Escape" });
    expect(screen.queryByRole("dialog", { name: "Connection & streaming" })).not.toBeInTheDocument();
    expect(opener).toHaveFocus();
    expect(apiMocks.connect).not.toHaveBeenCalled();
  });

  it("preserves a take-name draft across SSE and syncs it at recording boundaries", async () => {
    apiMocks.fetchState.mockResolvedValue(studioState("connected"));
    render(<App />);

    const controls = within(screen.getByRole("region", { name: "Capture controls" }));
    await waitFor(() => expect(controls.getByRole("button", { name: "Record" })).toBeEnabled());
    const takeName = screen.getByRole("textbox", { name: "LOCAL TAKE NAME" });
    fireEvent.change(takeName, { target: { value: "hero_take" } });

    act(() => pushState?.({
      ...studioState("connected"),
      avatars: initialState.avatars.map((avatar) => ({ ...avatar, frame: 42 })),
    }));
    expect(takeName).toHaveValue("hero_take");

    fireEvent.click(controls.getByRole("button", { name: "Record" }));
    await waitFor(() => expect(apiMocks.sendCommand).toHaveBeenCalledWith("start_record", { target: "local", takeName: "hero_take" }));

    act(() => pushState?.(studioState("connected", {
      recording: true,
      recordingTarget: "local",
      capturing: true,
      takeName: "hero_take",
    })));
    expect(takeName).toBeDisabled();
    expect(takeName).toHaveValue("hero_take");

    fireEvent.click(controls.getByRole("button", { name: "Stop record" }));
    await waitFor(() => expect(apiMocks.sendCommand).toHaveBeenLastCalledWith("stop_record", { target: "local" }));
    act(() => pushState?.(studioState("connected", {
      recording: false,
      capturing: true,
      takeName: "take002",
    })));
    expect(takeName).toBeEnabled();
    expect(takeName).toHaveValue("take002");
  });

  it("can explicitly start and stop provider-side recording in Demo mode", async () => {
    apiMocks.fetchState.mockResolvedValue(studioState("connected"));
    render(<App />);

    const controls = within(screen.getByRole("region", { name: "Capture controls" }));
    const target = await controls.findByRole("combobox", { name: "Recording target" });
    expect(within(target).getByRole("option", { name: "Local take" })).toBeEnabled();
    expect(within(target).getByRole("option", { name: "Provider / Axis" })).toBeEnabled();
    fireEvent.change(target, { target: { value: "axis" } });
    expect(target).toHaveValue("axis");
    expect(screen.getByRole("textbox", { name: "PROVIDER RECORDING" })).toBeDisabled();
    expect(screen.getByRole("textbox", { name: "PROVIDER RECORDING" })).toHaveValue("Managed by provider");

    fireEvent.click(controls.getByRole("button", { name: "Record" }));
    await waitFor(() => expect(apiMocks.sendCommand).toHaveBeenLastCalledWith("start_record", { target: "axis" }));
    act(() => pushState?.(studioState("connected", {
      recording: true,
      recordingTarget: "axis",
      capturing: true,
    })));
    fireEvent.click(controls.getByRole("button", { name: "Stop record" }));
    await waitFor(() => expect(apiMocks.sendCommand).toHaveBeenLastCalledWith("stop_record", { target: "axis" }));
  });

  it("recovers an active provider recording target and can stop after capabilities drop", async () => {
    apiMocks.fetchState.mockResolvedValue({
      ...studioState("connected", {
        recording: true,
        recordingTarget: "axis",
        capturing: true,
      }),
      capabilities: {
        ...initialState.capabilities,
        axisRecording: false,
        localRecording: false,
        reason: "Provider capabilities are no longer available.",
      },
    });
    render(<App />);

    const controls = within(screen.getByRole("region", { name: "Capture controls" }));
    const stop = await controls.findByRole("button", { name: "Stop record" });
    await waitFor(() => expect(stop).toBeEnabled());
    const target = controls.getByRole("combobox", { name: "Recording target" });
    expect(target).toBeDisabled();
    expect(target).toHaveValue("axis");
    expect(screen.getByRole("textbox", { name: "PROVIDER RECORDING" })).toHaveValue("Managed by provider");

    fireEvent.click(stop);
    await waitFor(() => expect(apiMocks.sendCommand).toHaveBeenLastCalledWith("stop_record", { target: "axis" }));
  });

  it("connects a standard BVH source with centimeter output by default", async () => {
    apiMocks.fetchState.mockResolvedValue(studioState("disconnected"));
    render(<App />);

    const connectButton = await screen.findByRole("button", { name: "Connect" });
    await waitFor(() => expect(connectButton).toBeEnabled());
    fireEvent.click(connectButton);
    const dialog = screen.getByRole("dialog", { name: "Connection & streaming" });
    fireEvent.change(within(dialog).getByRole("combobox", { name: "Connection mode" }), {
      target: { value: "bvh" },
    });
    expect(within(dialog).getByRole("combobox", { name: "Units" })).toHaveValue("centimeters");
    fireEvent.click(within(dialog).getByRole("button", { name: "Connect" }));

    await waitFor(() => expect(apiMocks.connect).toHaveBeenCalledWith(expect.objectContaining({
      mode: "bvh",
      unit: "centimeters",
    })));
  });

  it("does not advance calibration after rejection and Escape sends cancellation", async () => {
    apiMocks.fetchState.mockResolvedValue(studioState("connected"));
    apiMocks.sendCommand.mockRejectedValueOnce(new Error("Provider rejected calibration"));
    render(<App />);

    await waitFor(() => expect(screen.getByRole("button", { name: "Calibrate" })).toBeEnabled());
    fireEvent.click(screen.getByRole("button", { name: "Calibrate" }));
    const calibration = screen.getByRole("dialog", { name: "Provider-guided calibration" });
    const start = within(calibration).getByRole("button", { name: /Start calibration/i });
    await waitFor(() => expect(start).toHaveFocus());
    fireEvent.click(start);

    expect(await within(calibration).findByRole("alert")).toHaveTextContent("Provider rejected calibration");
    expect(within(calibration).queryByText(/Waiting for provider progress/i)).not.toBeInTheDocument();
    expect(within(calibration).getByRole("button", { name: /Start calibration/i })).toBeEnabled();

    apiMocks.sendCommand.mockResolvedValue(undefined);
    fireEvent.click(within(calibration).getByRole("button", { name: /Start calibration/i }));
    expect(await within(calibration).findByText(/Waiting for provider progress/i)).toBeInTheDocument();
    fireEvent.keyDown(document, { key: "Escape" });

    await waitFor(() => expect(apiMocks.sendCommand).toHaveBeenLastCalledWith("calibration_cancel", {}));
    await waitFor(() => expect(screen.queryByRole("dialog", { name: "Provider-guided calibration" })).not.toBeInTheDocument());
  });

  it("disables live actions immediately when the SSE bridge fails", async () => {
    apiMocks.fetchState.mockResolvedValue(studioState("connected"));
    render(<App />);
    const controls = within(screen.getByRole("region", { name: "Capture controls" }));

    await waitFor(() => expect(controls.getByRole("button", { name: "Capture" })).toBeEnabled());
    act(() => pushStreamError?.(new Error("Live state stream disconnected")));
    expect(controls.getByRole("button", { name: "Capture" })).toBeDisabled();
    expect(screen.getAllByText(/PREVIEW \/ OFFLINE/i).length).toBeGreaterThan(0);
  });

  it("requires server commands for Capture and lets an errored provider disconnect", async () => {
    const bvhState: StudioState = {
      ...studioState("connected"),
      connection: { ...studioState("connected").connection, mode: "bvh" },
      capabilities: {
        ...initialState.capabilities,
        receiveSensors: false,
        serverCommands: false,
        calibrationCommands: false,
        axisRecording: false,
        localRecording: true,
        reason: "BVH is receive-only; provider commands are unavailable.",
      },
      avatars: initialState.avatars.map((avatar) => ({ ...avatar, calibrated: true })),
    };
    apiMocks.fetchState.mockResolvedValue(bvhState);
    const { unmount } = render(<App />);
    const controls = within(screen.getByRole("region", { name: "Capture controls" }));
    await waitFor(() => expect(screen.getByRole("button", { name: "Disconnect" })).toBeEnabled());
    expect(controls.getByRole("button", { name: "Capture" })).toBeDisabled();
    expect(screen.getByLabelText(/Capture unavailable: BVH is receive-only/i)).toBeInTheDocument();
    const recordingTarget = controls.getByRole("combobox", { name: "Recording target" });
    expect(within(recordingTarget).getByRole("option", { name: "Local take" })).toBeEnabled();
    expect(within(recordingTarget).getByRole("option", { name: "Provider / Axis" })).toBeDisabled();
    fireEvent.change(recordingTarget, { target: { value: "axis" } });
    expect(recordingTarget).toHaveValue("local");
    fireEvent.click(controls.getByRole("button", { name: "Record" }));
    await waitFor(() => expect(apiMocks.sendCommand).toHaveBeenLastCalledWith("start_record", {
      target: "local",
      takeName: "take001",
    }));
    expect(screen.getByLabelText("Performer 01: Calibration status unavailable")).toBeInTheDocument();
    expect(screen.queryByLabelText("Performer 01: Calibrated")).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("tab", { name: /^Map$/i }));
    expect(screen.getByText("Sensor telemetry unavailable")).toBeInTheDocument();
    expect(screen.queryByText("NOMINAL")).not.toBeInTheDocument();
    unmount();

    apiMocks.fetchState.mockResolvedValue({
      ...studioState("error"),
      connection: { ...studioState("error").connection, message: "Transport failed" },
    });
    render(<App />);
    const disconnectButton = await screen.findByRole("button", { name: "Disconnect" });
    expect(screen.queryByRole("button", { name: "Connect" })).not.toBeInTheDocument();
    fireEvent.click(disconnectButton);
    await waitFor(() => expect(apiMocks.disconnect).toHaveBeenCalledOnce());
  });

  it("marks stale sensor data offline after disconnect and uses attempted frames for loss", async () => {
    apiMocks.fetchState.mockResolvedValue({
      ...studioState("disconnected"),
      diagnostics: { ...initialState.diagnostics, receivedFrames: 90, droppedFrames: 10 },
    });
    render(<App />);

    await waitFor(() => expect(screen.getAllByText("OFFLINE").length).toBeGreaterThan(0));
    expect(screen.queryByText("NOMINAL")).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("tab", { name: /^Map$/i }));
    expect(screen.getByText("Sensor telemetry offline")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("tab", { name: /Diagnostics/i }));
    expect(screen.getByText("10.00% of stream")).toBeInTheDocument();
    const latencyCard = screen.getByText("Stream latency").closest(".diagnostic-card");
    expect(latencyCard).toHaveTextContent("— Unavailable");
    const latencyValue = within(latencyCard as HTMLElement).getByText("Unavailable").closest("strong");
    expect(latencyValue).not.toHaveTextContent("0.0 ms");
  });

  it("keeps scene inspection functional while marking playback controls as planned", () => {
    render(<App />);

    fireEvent.click(screen.getByRole("tab", { name: /Scene/i }));
    expect(screen.getByRole("tree", { name: "Motion scene" })).toBeInTheDocument();
    fireEvent.click(screen.getByRole("treeitem", { name: /^Spine 2$/i }));
    expect(screen.getByRole("heading", { name: "Spine" })).toBeInTheDocument();

    fireEvent.click(screen.getByRole("tab", { name: /^Map$/i }));
    fireEvent.click(screen.getByRole("button", { name: /Right Upper Arm: telemetry unavailable/i }));
    expect(screen.getByRole("heading", { name: "PNS-09" })).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: /Edit/i }));
    expect(screen.getByRole("tab", { name: /Timeline/i })).toHaveAttribute("aria-selected", "true");
    expect(screen.getByText("PLAYBACK PLANNED")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Play take (playback planned)" })).toBeDisabled();
  });
});
