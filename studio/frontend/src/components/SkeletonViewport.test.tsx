import type { ReactNode } from "react";
import { fireEvent, render, screen, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { SkeletonViewport } from "./SkeletonViewport";

vi.mock("@react-three/fiber", () => ({
  Canvas: ({ children: _children }: { children: ReactNode }) => (
    <div data-testid="three-canvas" />
  ),
}));

const createProps = () => ({
  avatar: undefined,
  sensors: [],
  selectedJointId: null,
  selectedSensorId: null,
  capturing: false,
  preview: true,
  cameraView: "perspective" as const,
  cameraRevision: 0,
  followActor: true,
  showLabels: true,
  showSensors: true,
  onCameraViewChange: vi.fn(),
  onResetCamera: vi.fn(),
  onFollowActorChange: vi.fn(),
  onShowLabelsChange: vi.fn(),
  onShowSensorsChange: vi.fn(),
  onJointSelect: vi.fn(),
});

describe("SkeletonViewport panel layouts", () => {
  it("starts with the existing single interactive viewport", () => {
    render(<SkeletonViewport {...createProps()} />);

    expect(screen.getByRole("button", { name: "1-panel layout" })).toHaveAttribute("aria-pressed", "true");
    expect(screen.getByRole("button", { name: "2-panel layout" })).toHaveAttribute("aria-pressed", "false");
    const layout = screen.getByRole("group", { name: "1-panel viewport layout" });
    expect(within(layout).getAllByRole("img")).toHaveLength(1);
    expect(within(layout).getByRole("img", { name: "Interactive 3D skeleton viewport" })).toHaveAttribute(
      "data-camera-view",
      "perspective",
    );
  });

  it.each([2, 3, 4] as const)("renders a real camera canvas for the %i-panel layout", (panelCount) => {
    render(<SkeletonViewport {...createProps()} />);

    fireEvent.click(screen.getByRole("button", { name: `${panelCount}-panel layout` }));

    expect(screen.getByRole("button", { name: `${panelCount}-panel layout` })).toHaveAttribute("aria-pressed", "true");
    const layout = screen.getByRole("group", { name: `${panelCount}-panel viewport layout` });
    expect(within(layout).getAllByRole("img")).toHaveLength(panelCount);
    expect(screen.getAllByTestId("three-canvas")).toHaveLength(panelCount);
  });

  it("uses distinct camera angles and keeps the selected primary camera first", () => {
    const props = createProps();
    const { rerender } = render(<SkeletonViewport {...props} />);
    fireEvent.click(screen.getByRole("button", { name: "4-panel layout" }));

    const cameraViews = () => within(
      screen.getByRole("group", { name: "4-panel viewport layout" }),
    ).getAllByRole("img").map((panel) => panel.getAttribute("data-camera-view"));

    expect(cameraViews()).toEqual(["perspective", "front", "right", "top"]);

    rerender(<SkeletonViewport {...props} cameraView="front" />);
    expect(cameraViews()).toEqual(["front", "right", "top", "perspective"]);
  });

  it("keeps the existing camera action connected to the app shell", () => {
    const props = createProps();
    render(<SkeletonViewport {...props} />);

    fireEvent.click(screen.getByRole("button", { name: "Right view" }));

    expect(props.onCameraViewChange).toHaveBeenCalledWith("right");
  });

  it("places reference layout, follow, and avatar-label actions before companion camera tools", () => {
    render(<SkeletonViewport {...createProps()} />);

    const labels = within(screen.getByLabelText("Camera views"))
      .getAllByRole("button")
      .map((button) => button.getAttribute("aria-label"));

    expect(labels).toEqual([
      "1-panel layout",
      "2-panel layout",
      "3-panel layout",
      "4-panel layout",
      "Follow performer",
      "Hide labels",
      "Perspective view",
      "Front view",
      "Right view",
      "Reset camera",
      "Hide sensor markers",
    ]);
  });
});
