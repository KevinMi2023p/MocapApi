import { expect, test, type Page } from "@playwright/test";
import type { StudioState } from "../../src/types";
import { connectedCaptureFixture, disconnectedFixture } from "./fixtures";

const disableLiveEventStream = async (page: Page) => {
  await page.addInitScript(() => {
    class FrozenEventSource extends EventTarget {
      static readonly CONNECTING = 0;
      static readonly OPEN = 1;
      static readonly CLOSED = 2;

      readonly CONNECTING = 0;
      readonly OPEN = 1;
      readonly CLOSED = 2;
      readonly url: string;
      readonly withCredentials = false;
      readyState = 1;
      onopen: ((event: Event) => void) | null = null;
      onmessage: ((event: MessageEvent) => void) | null = null;
      onerror: ((event: Event) => void) | null = null;

      constructor(url: string | URL) {
        super();
        this.url = String(url);
      }

      close() {
        this.readyState = 2;
      }
    }

    Object.defineProperty(window, "EventSource", {
      configurable: true,
      value: FrozenEventSource,
      writable: true,
    });
  });
};

const openFixture = async (page: Page, state: StudioState) => {
  await disableLiveEventStream(page);
  await page.route("**/api/**", async (route) => {
    if (new URL(route.request().url()).pathname === "/api/state") {
      await route.fulfill({
        body: JSON.stringify(state),
        contentType: "application/json",
        status: 200,
      });
      return;
    }
    if (route.request().method() === "POST") {
      await route.fulfill({
        body: JSON.stringify({ ok: true, message: "Visual fixture accepted" }),
        contentType: "application/json",
        status: 200,
      });
      return;
    }
    await route.continue();
  });

  await page.goto("/");
  await expect(page.getByLabel("Mocap Studio")).toBeVisible();
  await page.addStyleTag({
    content: `
      *, *::before, *::after {
        animation-delay: 0s !important;
        animation-duration: 0s !important;
        caret-color: transparent !important;
        transition-delay: 0s !important;
        transition-duration: 0s !important;
      }
    `,
  });
  await page.evaluate(() => new Promise<void>((resolve) => {
    requestAnimationFrame(() => requestAnimationFrame(() => resolve()));
  }));
};

test.describe("deterministic open-source studio baselines", () => {
  test("disconnected capture workspace", async ({ page }) => {
    await openFixture(page, disconnectedFixture());
    await expect(page.getByRole("button", { name: "Connect", exact: true })).toBeEnabled();
    await expect(page).toHaveScreenshot("capture-disconnected.png", { fullPage: true });
  });

  test("connected capture workspace", async ({ page }) => {
    await openFixture(page, connectedCaptureFixture());
    await expect(page.getByRole("button", { name: "Disconnect", exact: true })).toBeEnabled();
    await expect(page.getByRole("button", { name: "Stop", exact: true })).toBeEnabled();
    await expect(page).toHaveScreenshot("capture-connected.png", { fullPage: true });
  });

  test("four-panel capture workspace", async ({ page }) => {
    await openFixture(page, connectedCaptureFixture());
    await page.getByRole("button", { name: "4-panel layout" }).click();
    await expect(page.getByRole("group", { name: "4-panel viewport layout" })).toBeVisible();
    await expect(page).toHaveScreenshot("capture-four-panel.png", { fullPage: true });
  });

  test("calibration ready dialog", async ({ page }) => {
    await openFixture(page, connectedCaptureFixture());
    await page.getByRole("button", { name: "Calibrate", exact: true }).click();
    await expect(page.getByRole("dialog", { name: "Provider-guided calibration" })).toBeVisible();
    await expect(page).toHaveScreenshot("calibration-ready.png", { fullPage: true });
  });

  test("edit timeline workspace", async ({ page }) => {
    await openFixture(page, connectedCaptureFixture());
    await page.getByRole("button", { name: "Edit" }).click();
    await expect(page.getByRole("tab", { name: "Timeline" })).toHaveAttribute("aria-selected", "true");
    await expect(page).toHaveScreenshot("edit-timeline.png", { fullPage: true });
  });

  test("local project workspace", async ({ page }) => {
    await openFixture(page, connectedCaptureFixture());
    await page.getByRole("button", { name: "Project", exact: true }).click();
    await expect(page.getByRole("region", { name: "Project workspace" })).toBeVisible();
    await expect(page.getByRole("table", { name: "Local takes" })).toBeVisible();
    await expect(page).toHaveScreenshot("project-local-library.png", { fullPage: true });
  });
});
