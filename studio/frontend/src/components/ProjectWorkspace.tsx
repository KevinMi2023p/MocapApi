import { useEffect, useMemo, useRef, useState } from "react";
import {
  Archive,
  ChevronDown,
  Clock3,
  Database,
  ExternalLink,
  FileText,
  FolderOpen,
  FolderPlus,
  HardDrive,
  Info,
  PencilLine,
  Ruler,
  Search,
  Trash2,
  Upload,
} from "lucide-react";
import type { Take } from "../types";
import "./ProjectWorkspace.css";

type ProjectLibrarySection = "motion" | "body-size";

interface ContextMenuState {
  take: Take;
  x: number;
  y: number;
}

export interface ProjectWorkspaceProps {
  takes: Take[];
  selectedTakeId: string | null;
  onSelectTake: (take: Take) => void;
  onOpenTake: (take: Take) => void;
  onRevealTake?: (take: Take) => void;
}

const formatDuration = (milliseconds: number) => {
  const safe = Math.max(0, milliseconds);
  const hours = Math.floor(safe / 3_600_000);
  const minutes = Math.floor((safe % 3_600_000) / 60_000);
  const seconds = Math.floor((safe % 60_000) / 1000);
  const frames = Math.floor((safe % 1000) / (1000 / 60));
  return [hours, minutes, seconds, frames]
    .map((value) => String(value).padStart(2, "0"))
    .join(":");
};

const formatStartedAt = (value: string) => {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "Unknown date";
  return date.toLocaleString([], {
    year: "numeric",
    month: "short",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  });
};

export function ProjectWorkspace({
  takes,
  selectedTakeId,
  onSelectTake,
  onOpenTake,
  onRevealTake,
}: ProjectWorkspaceProps) {
  const [section, setSection] = useState<ProjectLibrarySection>("motion");
  const [query, setQuery] = useState("");
  const [contextMenu, setContextMenu] = useState<ContextMenuState | null>(null);
  const contextMenuRef = useRef<HTMLDivElement>(null);
  const normalizedQuery = query.trim().toLocaleLowerCase();
  const visibleTakes = useMemo(
    () => takes.filter((take) => (
      normalizedQuery.length === 0
      || take.name.toLocaleLowerCase().includes(normalizedQuery)
      || take.notes.toLocaleLowerCase().includes(normalizedQuery)
    )),
    [normalizedQuery, takes],
  );
  const selectedTake = takes.find((take) => take.id === selectedTakeId) ?? null;

  useEffect(() => {
    if (!contextMenu) return undefined;
    const dismiss = (event: MouseEvent) => {
      if (!contextMenuRef.current?.contains(event.target as Node)) setContextMenu(null);
    };
    const dismissWithEscape = (event: KeyboardEvent) => {
      if (event.key === "Escape") setContextMenu(null);
    };
    document.addEventListener("mousedown", dismiss);
    document.addEventListener("keydown", dismissWithEscape);
    return () => {
      document.removeEventListener("mousedown", dismiss);
      document.removeEventListener("keydown", dismissWithEscape);
    };
  }, [contextMenu]);

  return (
    <section className="project-workspace" aria-label="Project workspace">
      <header className="project-workspace-header">
        <div>
          <span className="project-workspace-kicker">PROJECT</span>
          <strong>Local Library</strong>
          <span className="project-local-badge"><HardDrive size={11} /> ON THIS COMPUTER</span>
        </div>
        <p><Info size={12} /> Mocap Studio local takes only. Axis projects and <code>.mbx</code> files remain external and unchanged.</p>
      </header>

      <div className="project-library-layout">
        <nav className="project-library-tabs" aria-label="Project file type">
          <button
            type="button"
            aria-current={section === "motion" ? "page" : undefined}
            className={section === "motion" ? "selected" : ""}
            onClick={() => setSection("motion")}
          >
            <Database size={13} /> <span>Local Takes</span>
          </button>
          <button
            type="button"
            aria-current={section === "body-size" ? "page" : undefined}
            className={section === "body-size" ? "selected" : ""}
            onClick={() => setSection("body-size")}
          >
            <Ruler size={13} /> <span>BodySize</span>
          </button>
        </nav>

        <div className="project-library-browser">
          <div className="project-browser-toolbar">
            <div className="project-file-actions" aria-label="Local library actions">
              <button type="button" disabled title="Local folders are planned but not implemented"><FolderPlus size={13} /> New Folder</button>
              <button type="button" disabled title="Local file import is planned but not implemented"><Upload size={13} /> Import</button>
              <button type="button" disabled title="Local take export is planned but not implemented"><ExternalLink size={13} /> Export</button>
              <button type="button" disabled title="Safe take deletion is planned but not implemented"><Trash2 size={13} /> Delete</button>
            </div>
            <span className="project-browser-count">
              {section === "motion" ? `${visibleTakes.length} of ${takes.length} local takes` : "Provider-owned data"}
            </span>
            <label className="project-search">
              <span>FILTER</span>
              <Search size={14} />
              <input
                aria-label="Search local takes"
                placeholder="Search local takes"
                value={query}
                disabled={section !== "motion"}
                onChange={(event) => setQuery(event.target.value)}
              />
            </label>
          </div>

          {section === "motion" ? (
            <div className="project-take-table" role="table" aria-label="Local takes">
              <div className="project-take-row project-table-header" role="row">
                <span role="columnheader">File</span>
                <span role="columnheader">Recorded</span>
                <span role="columnheader">Duration</span>
                <span role="columnheader">Frames</span>
                <span role="columnheader">Status</span>
              </div>
              <div className="project-table-body" role="rowgroup">
                <div className="project-folder-row" role="row">
                  <span role="cell"><ChevronDown size={13} /><FolderOpen size={14} /><strong>Local Takes</strong><small>MOCAP STUDIO</small></span>
                </div>
                {visibleTakes.map((take) => (
                  <button
                    type="button"
                    role="row"
                    aria-selected={selectedTake?.id === take.id}
                    className={`project-take-row project-file-row${selectedTake?.id === take.id ? " selected" : ""}`}
                    key={take.id}
                    onClick={() => {
                      setContextMenu(null);
                      onSelectTake(take);
                    }}
                    onDoubleClick={() => onOpenTake(take)}
                    onKeyDown={(event) => {
                      if (event.key !== "Enter") return;
                      event.preventDefault();
                      onOpenTake(take);
                    }}
                    onContextMenu={(event) => {
                      event.preventDefault();
                      onSelectTake(take);
                      setContextMenu({
                        take,
                        x: Math.max(4, Math.min(event.clientX, window.innerWidth - 180)),
                        y: Math.max(4, Math.min(event.clientY, window.innerHeight - 184)),
                      });
                    }}
                  >
                    <span role="cell" className="project-take-name"><i className="project-tree-guide" /><FileText size={14} /><strong>{take.name}</strong></span>
                    <span role="cell">{formatStartedAt(take.startedAt)}</span>
                    <span role="cell" className="project-monospace"><Clock3 size={11} /> {formatDuration(take.durationMs)}</span>
                    <span role="cell">{take.frames.toLocaleString()}</span>
                    <span role="cell" className={`project-take-status ${take.status}`}><i />{take.status}</span>
                  </button>
                ))}
                {visibleTakes.length === 0 ? (
                  <div className="project-empty-state">
                    <Search size={22} />
                    <strong>No matching local takes</strong>
                    <span>Try another name or note.</span>
                  </div>
                ) : null}
              </div>
            </div>
          ) : (
            <div className="project-capability-boundary" role="note">
              <span><Ruler size={28} /></span>
              <strong>BodySize belongs to the upstream solver</strong>
              <p>MocapApi does not expose Axis body templates or body-dimension editing. This independent console will not inspect or imitate undocumented project data.</p>
              <div><Archive size={13} /> Axis project files stay external and unchanged.</div>
              <button type="button" disabled>Editing unavailable</button>
            </div>
          )}
        </div>
      </div>

      {contextMenu ? (
        <div
          ref={contextMenuRef}
          className="project-context-menu"
          role="menu"
          aria-label={`${contextMenu.take.name} actions`}
          style={{ left: contextMenu.x, top: contextMenu.y }}
        >
          <button type="button" role="menuitem" onClick={() => { setContextMenu(null); onOpenTake(contextMenu.take); }}><PencilLine size={13} /> Open in Edit</button>
          <button type="button" role="menuitem" disabled title="Local take export is planned">Export</button>
          <button type="button" role="menuitem" disabled title="Atomic local rename is planned">Rename</button>
          <button type="button" role="menuitem" disabled title="Safe local deletion is planned">Delete</button>
          <span />
          <button
            type="button"
            role="menuitem"
            disabled={!contextMenu.take.localPath || !onRevealTake}
            title={!contextMenu.take.localPath
              ? "This preview take has no local folder"
              : !onRevealTake
                ? "Desktop file reveal is not available in this build"
                : "Reveal the local take folder"}
            onClick={() => {
              setContextMenu(null);
              onRevealTake?.(contextMenu.take);
            }}
          >
            <FolderOpen size={13} /> Reveal in file manager
          </button>
        </div>
      ) : null}
    </section>
  );
}
