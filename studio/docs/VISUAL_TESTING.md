# Deterministic visual testing

The Playwright suite captures only this project's independently authored UI. It
does not read, embed, download, or commit Axis Studio screenshots or assets.
Reference-application captures used for manual parity review must remain in an
access-controlled directory outside this repository.

## What is fixed

- Chromium is pinned through `@playwright/test` in `package-lock.json`.
- Tests run at CSS-pixel viewports of 1366×768 and 1920×1080 with device scale
  factor 1.
- Locale is `en-US`, timezone is UTC, color scheme is dark, reduced motion is
  enabled, and CSS transitions/animations/carets are suppressed.
- `/api/state` is fulfilled from authored TypeScript fixtures with fixed IDs,
  timestamps, telemetry, takes, diagnostics, and skeleton transforms.
- `EventSource` is replaced before application code loads, preventing a live
  backend or reconnect timing from mutating a screenshot.
- API POSTs are acknowledged locally. Visual tests must separately assert that
  production capability gates prevent unsupported actions.
- A single worker prevents GPU and Vite scheduling from changing capture order.

The scenarios are disconnected Capture, connected Capture, four-panel Capture,
calibration ready, Edit timeline, and the local Project library. Each runs at
both fixed viewports. Add a focused scenario when a row in
[PARITY_LEDGER.md](PARITY_LEDGER.md) gains a new visible state or changes
placement.

The checked-in baselines are Linux Chromium images, matching the Ubuntu CI
environment. Playwright deliberately uses a platform suffix, so a macOS run does
not overwrite Linux expectations; macOS may gain separately reviewed baselines
when a stable runner is assigned to visual comparison.

## Install and run

From `studio/frontend`:

```sh
npm ci
npx playwright install chromium
npm run test:visual
```

The Vite server is started automatically on loopback port 4173. No Python
backend, network motion source, Windows executable, or hardware is used.

To create or deliberately refresh this project's baselines:

```sh
npm run test:visual:update
npm run test:visual
```

Review every changed image at both sizes before committing it. A baseline update
must accompany the relevant parity-ledger and behavior-test changes; never use
`--update-snapshots` merely to silence an unexplained difference.

## Private reference comparison

Use a fixed target profile before comparing with Axis Studio:

- Axis Studio `3.0.14004.2620`, build `20251222173616363`
- recorded Windows version and display scale
- recorded application window size and locale
- explicit state such as disconnected, calibration countdown, recording, or
  Edit playback

Keep the Windows capture outside Git. Compare it side-by-side with the matching
open-source Playwright baseline and record measurements or conclusions in the
ledger. Do not use the Windows image as a Playwright golden and do not crop or
copy its icons into this project.

## Failure triage

1. Inspect `test-results` and the Playwright diff before updating anything.
2. If text alone moved, confirm locale, fonts, and the pinned browser revision.
3. If only the canvas differs, reproduce with the same Chromium revision and
   software-rendering environment. Keep tolerance narrow; do not mask structural
   movement with a broad pixel threshold.
4. If the fixture changed, verify that it still represents a possible provider
   state and that capability labels remain truthful.
5. Run `npm test`, `npm run lint`, and `npm run build` after visual tests pass.

Generated `test-results`, HTML reports, and blob reports are ignored. Approved
PNG snapshots under `tests/visual/*-snapshots` are source-controlled.
