import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import App from "./App";

const apiMocks = vi.hoisted(() => ({
  fetchState: vi.fn(),
  connect: vi.fn(),
  disconnect: vi.fn(),
  sendCommand: vi.fn(),
  subscribeToState: vi.fn(),
}));

vi.mock("./api", () => apiMocks);

vi.mock("./components/SkeletonViewport", () => ({
  SkeletonViewport: ({ avatar, onJointSelect }: {
    avatar?: { joints: Array<{ id: string; name: string }> };
    onJointSelect: (joint: { id: string; name: string }) => void;
  }) => (
    <section aria-label="3D motion preview">
      <span>Interactive skeleton viewport</span>
      {avatar?.joints[1] ? (
        <button type="button" onClick={() => onJointSelect(avatar.joints[1])}>
          Select {avatar.joints[1].name}
        </button>
      ) : null}
    </section>
  ),
}));

describe("Mocap Studio operator console", () => {
  beforeEach(() => {
    apiMocks.fetchState.mockRejectedValue(new Error("No backend in component test"));
    apiMocks.connect.mockResolvedValue(undefined);
    apiMocks.disconnect.mockResolvedValue(undefined);
    apiMocks.sendCommand.mockResolvedValue(undefined);
    apiMocks.subscribeToState.mockReturnValue(() => {});
  });

  it("renders the workstation layout and independent product disclosure", async () => {
    render(<App />);

    expect(screen.getByLabelText("Mocap Studio")).toBeInTheDocument();
    expect(screen.getByRole("navigation", { name: "Workspace" })).toBeInTheDocument();
    expect(screen.getByRole("region", { name: "3D motion preview" })).toBeInTheDocument();
    expect(screen.getByRole("complementary", { name: "Suits and scene" })).toBeInTheDocument();
    expect(screen.getByRole("complementary", { name: "Joint and sensor inspector" })).toBeInTheDocument();
    expect(screen.getByText(/Independent open-source MocapApi companion/i)).toBeInTheDocument();

    await waitFor(() => expect(apiMocks.fetchState).toHaveBeenCalledOnce());
  });

  it("gates live commands until a provider connects and runs demo capture", async () => {
    render(<App />);
    const controls = within(screen.getByRole("region", { name: "Capture controls" }));

    expect(controls.getByRole("button", { name: "Capture" })).toBeDisabled();
    fireEvent.click(screen.getByRole("button", { name: "Connect" }));

    const dialog = screen.getByRole("dialog", { name: "Connection & streaming" });
    expect(within(dialog).getByText("Current provider capabilities")).toBeInTheDocument();
    fireEvent.click(within(dialog).getByRole("button", { name: "Connect" }));

    await waitFor(() => expect(screen.queryByRole("dialog", { name: "Connection & streaming" })).not.toBeInTheDocument());
    expect(screen.getByRole("button", { name: "Disconnect" })).toBeInTheDocument();
    expect(controls.getByRole("button", { name: "Capture" })).toBeEnabled();

    fireEvent.click(controls.getByRole("button", { name: "Capture" }));
    expect(await controls.findByRole("button", { name: "Stop" })).toBeInTheDocument();
  });

  it("uses provider-owned progress in the calibration workflow", async () => {
    render(<App />);
    fireEvent.click(screen.getByRole("button", { name: "Connect" }));
    fireEvent.click(within(screen.getByRole("dialog", { name: "Connection & streaming" })).getByRole("button", { name: "Connect" }));
    await waitFor(() => expect(screen.getByRole("button", { name: "Calibrate" })).toBeEnabled());

    fireEvent.click(screen.getByRole("button", { name: "Calibrate" }));
    const calibration = screen.getByRole("dialog", { name: "Provider-guided calibration" });
    expect(within(calibration).getByText(/does not reproduce the proprietary calibration solver/i)).toBeInTheDocument();

    fireEvent.click(within(calibration).getByRole("button", { name: /Start calibration/i }));
    expect(await within(calibration).findByText(/Waiting for provider progress/i)).toBeInTheDocument();
    expect(within(calibration).getByRole("button", { name: /Provider Next/i })).toBeEnabled();
  });

  it("switches between scene selection, sensor detail, and edit timeline", () => {
    render(<App />);

    fireEvent.click(screen.getByRole("tab", { name: /Scene/i }));
    expect(screen.getByRole("tree", { name: "Motion scene" })).toBeInTheDocument();
    fireEvent.click(screen.getByRole("treeitem", { name: /^Spine 2$/i }));
    expect(screen.getByRole("heading", { name: "Spine" })).toBeInTheDocument();

    fireEvent.click(screen.getByRole("tab", { name: /^Map$/i }));
    fireEvent.click(screen.getByRole("button", { name: /Right Upper Arm: 72% signal/i }));
    expect(screen.getByRole("heading", { name: "PNS-09" })).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: /Edit/i }));
    expect(screen.getByRole("tab", { name: /Timeline/i })).toHaveAttribute("aria-selected", "true");
    expect(screen.getByRole("button", { name: "Play take" })).toBeInTheDocument();
  });
});
