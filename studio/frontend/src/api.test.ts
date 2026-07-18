import { afterEach, describe, expect, it, vi } from "vitest";
import { sendCommand } from "./api";

describe("API errors", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("unwraps the bridge JSON error field", async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response(
      JSON.stringify({ error: "Provider rejected calibration" }),
      { status: 409, headers: { "Content-Type": "application/json" } },
    ));
    vi.stubGlobal("fetch", fetchMock);

    await expect(sendCommand("start_calibration")).rejects.toThrow("Provider rejected calibration");
  });

  it("preserves a plain-text bridge error", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response("Transport unavailable", { status: 503 })));

    await expect(sendCommand("start_capture")).rejects.toThrow("Transport unavailable");
  });
});
