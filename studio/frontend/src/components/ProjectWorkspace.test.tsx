import { fireEvent, render, screen, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import type { Take } from "../types";
import { ProjectWorkspace } from "./ProjectWorkspace";

const takes: Take[] = [
  {
    id: "walk",
    name: "walk_cycle_01",
    notes: "Neutral walk for blocking",
    startedAt: "2026-07-19T12:00:00.000Z",
    durationMs: 18_420,
    frames: 1105,
    status: "complete",
    localPath: "/tmp/mocap-studio/takes/walk",
  },
  {
    id: "jump",
    name: "jump_02",
    notes: "High energy action",
    startedAt: "2026-07-19T13:00:00.000Z",
    durationMs: 2_000,
    frames: 120,
    status: "interrupted",
  },
];

const setup = (overrides: Partial<React.ComponentProps<typeof ProjectWorkspace>> = {}) => {
  const onSelectTake = vi.fn();
  const onOpenTake = vi.fn();
  const onRevealTake = vi.fn();
  render(
    <ProjectWorkspace
      takes={takes}
      selectedTakeId="walk"
      onSelectTake={onSelectTake}
      onOpenTake={onOpenTake}
      onRevealTake={onRevealTake}
      {...overrides}
    />,
  );
  return { onSelectTake, onOpenTake, onRevealTake };
};

describe("ProjectWorkspace", () => {
  it("labels the catalog as local and keeps Axis project formats outside its ownership", () => {
    setup();

    expect(screen.getByRole("region", { name: "Project workspace" })).toBeInTheDocument();
    expect(screen.getByText(/Mocap Studio local takes only/i)).toBeInTheDocument();
    expect(screen.getByText(/Axis projects and/i)).toHaveTextContent(".mbx");
    expect(screen.getAllByText("Local Takes")).toHaveLength(2);
    expect(screen.getByRole("button", { name: /New Folder/i })).toBeDisabled();
    expect(screen.getByRole("button", { name: /Import/i })).toBeDisabled();
    expect(screen.getByRole("button", { name: /Export/i })).toBeDisabled();
    expect(screen.getByRole("button", { name: /Delete/i })).toBeDisabled();
  });

  it("searches local take names and notes without mutating the catalog", () => {
    setup();
    const table = screen.getByRole("table", { name: "Local takes" });

    fireEvent.change(screen.getByRole("textbox", { name: "Search local takes" }), {
      target: { value: "energy" },
    });

    expect(within(table).getByText("jump_02")).toBeInTheDocument();
    expect(within(table).queryByText("walk_cycle_01")).not.toBeInTheDocument();
    expect(screen.getByText("1 of 2 local takes")).toBeInTheDocument();
  });

  it("reports selection, edit, and file reveal through explicit callbacks", () => {
    const { onSelectTake, onOpenTake, onRevealTake } = setup();
    const walkRow = screen.getByRole("row", { name: /walk_cycle_01/i });
    const jumpRow = screen.getByRole("row", { name: /jump_02/i });

    fireEvent.click(jumpRow);
    expect(onSelectTake).toHaveBeenCalledWith(takes[1]);

    fireEvent.doubleClick(jumpRow);
    expect(onOpenTake).toHaveBeenCalledWith(takes[1]);

    fireEvent.keyDown(walkRow, { key: "Enter" });
    expect(onOpenTake).toHaveBeenLastCalledWith(takes[0]);

    fireEvent.contextMenu(walkRow, { clientX: 100, clientY: 120 });
    fireEvent.click(screen.getByRole("menuitem", { name: "Open in Edit" }));
    expect(onOpenTake).toHaveBeenLastCalledWith(takes[0]);

    fireEvent.contextMenu(walkRow, { clientX: 100, clientY: 120 });
    fireEvent.click(screen.getByRole("menuitem", { name: "Reveal in file manager" }));
    expect(onRevealTake).toHaveBeenCalledWith(takes[0]);
    expect(walkRow).toHaveAttribute("aria-selected", "true");
  });

  it("shows the honest BodySize capability boundary instead of an editable template", () => {
    setup();

    fireEvent.click(screen.getByRole("button", { name: /BodySize/i }));

    expect(screen.getByRole("note")).toHaveTextContent("BodySize belongs to the upstream solver");
    expect(screen.getByText(/MocapApi does not expose Axis body templates/i)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Editing unavailable" })).toBeDisabled();
    expect(screen.getByRole("textbox", { name: "Search local takes" })).toBeDisabled();
  });

  it("keeps reveal disabled when the desktop bridge does not provide the action", () => {
    setup({ onRevealTake: undefined });

    fireEvent.contextMenu(screen.getByRole("row", { name: /walk_cycle_01/i }), { clientX: 100, clientY: 120 });
    expect(screen.getByRole("menuitem", { name: "Reveal in file manager" })).toBeDisabled();
    expect(screen.getByRole("menuitem", { name: "Reveal in file manager" })).toHaveAttribute(
      "title",
      "Desktop file reveal is not available in this build",
    );
  });
});
