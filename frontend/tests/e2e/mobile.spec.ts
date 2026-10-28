/**
 * ⚠️ QUARANTINED — THIS FILE HAS NEVER RUN AND DOES NOT COMPILE. ⚠️
 *
 * playwright.config.ts sets `testDir: "./e2e"`. This file lives in
 * "./tests/e2e", so Playwright has never collected it — every assertion
 * below is unverified, and it was written against an app version that no
 * longer exists:
 *   • line ~165 calls `vi.stubGlobal` — that is Vitest's API, undefined in
 *     a Playwright worker; the test would throw ReferenceError on the
 *     first line of its body.
 *   • `window._confirmCallCount` is assigned without a declaration merge,
 *     so it does not type-check either.
 *   • It asserts path routes like /strategies/1, but /strategies/[id] was
 *     converted to the query-param route /strategies/detail?id= (required
 *     by output:'export' — a static export cannot serve arbitrary runtime
 *     path segments).
 *   • It clicks `button:has-text("Log in")`; the real button reads
 *     "Sign in".
 *   • It uses numeric strategy ids; the API uses UUID strings.
 *
 * Because tsconfig.json's `include` covered this path while Playwright's
 * `testDir` did not, it broke `tsc --noEmit` (and therefore `next build`,
 * which runs with typescript.ignoreBuildErrors:false) while providing
 * zero coverage. "tests" is now listed in tsconfig.json's `exclude`,
 * alongside the already-excluded "e2e".
 *
 * It is kept rather than deleted because the SCENARIOS are still worth
 * covering (deep-link routing, offline banner, push-permission prompt,
 * Android back button) — none of which the live e2e/happy-paths.spec.ts
 * exercises. Rewriting them against the current routes and moving them
 * into ./e2e is tracked as follow-up work; do not treat this file as
 * passing coverage in the meantime.
 *
 * ─────────────────────────────────────────────────────────────────────
 *
 * mobile.spec.ts — Vectorae (M7 Chunk 4)
 *
 * Playwright E2E smoke tests for the mobile/web app.
 * Backend and Firebase are mocked via Playwright's route interception.
 *
 * These tests run against the Next.js static export served locally,
 * simulating the Capacitor WebView environment as closely as possible
 * without a real Android device.
 *
 * Run: npx playwright test frontend/tests/e2e/mobile.spec.ts
 *
 * TODO: for real device testing, configure Playwright to connect to a
 * device via Appium or use Firebase Test Lab for cloud device testing.
 */

import { test, expect, Page } from '@playwright/test';

// ── API mock helpers ───────────────────────────────────────────────────────────

const API_URL = process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000';

async function mockBackendRoutes(page: Page) {
  // Health check (used by useNetwork)
  await page.route(`${API_URL}/health`, route =>
    route.fulfill({ status: 200, body: JSON.stringify({ status: 'ok' }) }),
  );

  // Auth endpoints
  await page.route(`${API_URL}/auth/login`, route =>
    route.fulfill({
      status: 200,
      body: JSON.stringify({
        access_token: 'mock_access_token',
        refresh_token: 'mock_refresh_token',
        token_type: 'bearer',
      }),
    }),
  );

  await page.route(`${API_URL}/auth/me`, route =>
    route.fulfill({
      status: 200,
      body: JSON.stringify({ id: 1, email: 'test@example.com', plan: 'pro' }),
    }),
  );

  // Strategies list
  await page.route(`${API_URL}/strategies*`, route =>
    route.fulfill({
      status: 200,
      body: JSON.stringify([
        { id: 1, status: 'complete', flow_type: 'has_clients', created_at: '2026-01-01T00:00:00Z' },
        { id: 2, status: 'running',  flow_type: 'no_clients',  created_at: '2026-01-02T00:00:00Z' },
      ]),
    }),
  );

  // Device registration (push notifications)
  await page.route(`${API_URL}/devices/register`, route =>
    route.fulfill({ status: 200, body: JSON.stringify({ token: 'fcm', registered: true }) }),
  );
}

// ── Tests ─────────────────────────────────────────────────────────────────────

test.describe('Mobile app smoke tests', () => {
  test.beforeEach(async ({ page }) => {
    await mockBackendRoutes(page);
  });

  test('cold start — login screen renders without crash', async ({ page }) => {
    await page.goto('/');

    // The app should show a login form, not a crash/error screen
    // Looking for either an email input or a login button
    await expect(
      page.locator('[type="email"], input[placeholder*="email" i], button:has-text("Log in")')
        .first(),
    ).toBeVisible({ timeout: 10_000 });

    // No crash indicators
    await expect(page.locator('text=500')).not.toBeVisible();
    await expect(page.locator('text=Something went wrong')).not.toBeVisible();
  });

  test('login → strategy list renders', async ({ page }) => {
    await page.goto('/');

    // Fill login form
    await page.locator('[type="email"]').first().fill('test@example.com');
    await page.locator('[type="password"]').first().fill('password123');
    await page.locator('button:has-text("Log in"), button[type="submit"]').first().click();

    // Should navigate to the main dashboard / strategy list
    await expect(page).toHaveURL(/\/(dashboard|strategies|$)/, { timeout: 8_000 });

    // Strategy list or pipeline kanban should be visible
    await expect(
      page.locator('[data-testid="strategy-list"], [data-testid="pipeline-board"], h1, h2')
        .first(),
    ).toBeVisible({ timeout: 8_000 });
  });

  test('deep link navigation — ch:deeplink event routes to strategy detail', async ({ page }) => {
    // Mock strategy detail endpoint
    await page.route(`${API_URL}/strategies/1*`, route =>
      route.fulfill({
        status: 200,
        body: JSON.stringify({
          id: 1,
          status: 'complete',
          flow_type: 'has_clients',
          research_steps: [],
          verified_passes: [],
        }),
      }),
    );

    await page.goto('/');

    // Dispatch the custom deep link event that NativeProvider's push handler fires
    await page.evaluate(() => {
      window.dispatchEvent(
        new CustomEvent('ch:deeplink', {
          detail: { url: 'clienthunter:///strategies/1' },
        }),
      );
    });

    // The app should navigate to the strategy detail page
    await expect(page).toHaveURL(/strategies\/1/, { timeout: 8_000 });
  });

  test('push notification permission prompt NOT shown on cold start', async ({ page }) => {
    await page.goto('/');

    // Wait for any initial renders to complete
    await page.waitForTimeout(1000);

    // The re-prompt modal should NOT be visible on cold start
    // (it only appears after the first strategy completes)
    const promptModal = page.locator(
      '[data-testid="push-reprompt"], text=Enable notifications',
    );
    await expect(promptModal).not.toBeVisible();
  });

  test('offline banner appears when backend is unreachable', async ({ page }) => {
    await page.goto('/');

    // Override the health check to simulate backend down
    await page.route(`${API_URL}/health`, route => route.abort('failed'));

    // Wait for the polling interval to detect the outage
    // (BACKEND_PING_INTERVAL_MS = 30s, but useNetwork does an immediate check)
    await page.waitForTimeout(2000);

    // The offline/unreachable banner should appear
    const banner = page.getByRole('status');
    await expect(banner).toBeVisible({ timeout: 6_000 });
  });

  test('back button at root shows exit dialog (web fallback via window.confirm)', async ({ page }) => {
    vi.stubGlobal?.('confirm', () => false); // suppress actual dialogs in CI

    await page.goto('/');

    // Spy on window.confirm
    await page.evaluate(() => {
      window._confirmCallCount = 0;
      const orig = window.confirm.bind(window);
      window.confirm = (...args) => {
        window._confirmCallCount++;
        return false;
      };
    });

    // Simulate Android back button press via the custom event NativeProvider would receive
    // (On web, the browser back button is handled differently — this tests the logic path)
    await page.evaluate(() => {
      // Trigger the back button handler as if Capacitor fired it
      window.dispatchEvent(new CustomEvent('capacitor-back-button', { detail: {} }));
    });

    // Note: confirm may not be called here because the web platform handler runs,
    // not the Capacitor handler (isNative() returns false in web E2E).
    // This test verifies the page does not crash on the event.
    await expect(page).toHaveURL('/', { timeout: 2_000 });
  });
});
