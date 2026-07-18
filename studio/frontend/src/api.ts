import type { ConnectionSettings, StudioState } from "./types";

export async function fetchState(): Promise<StudioState> {
  const response = await fetch("/api/state", { cache: "no-store" });
  if (!response.ok) throw new Error(`State request failed (${response.status})`);
  return response.json() as Promise<StudioState>;
}

export async function connect(settings: ConnectionSettings): Promise<void> {
  await post("/api/connect", settings);
}

export async function disconnect(): Promise<void> {
  await post("/api/disconnect", {});
}

export async function sendCommand(
  command: string,
  options: Record<string, unknown> = {},
): Promise<void> {
  await post("/api/commands", { command, ...options });
}

export function subscribeToState(
  onState: (state: StudioState) => void,
  onError: (error: Error) => void,
): () => void {
  const events = new EventSource("/api/events");
  events.addEventListener("state", (event) => {
    try {
      onState(JSON.parse((event as MessageEvent<string>).data) as StudioState);
    } catch (error) {
      onError(error instanceof Error ? error : new Error("Invalid state event"));
    }
  });
  events.onerror = () => onError(new Error("Live state stream disconnected"));
  return () => events.close();
}

async function post(path: string, body: unknown): Promise<void> {
  const response = await fetch(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!response.ok) {
    const body = await response.text();
    let message = body;
    if (body) {
      try {
        const payload = JSON.parse(body) as { error?: unknown };
        if (typeof payload.error === "string" && payload.error.trim()) {
          message = payload.error;
        }
      } catch {
        // Plain-text errors remain valid bridge responses.
      }
    }
    throw new Error(message || `Request failed (${response.status})`);
  }
}
