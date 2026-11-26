import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { describe, expect, it } from "vitest";
import { SettingsPage } from "../../src/features/settings";
import { BASE, renderWithApp } from "./helpers";
import { server } from "./server";

const PREFS = {
  preferences: [
    { category: "mail", enabled: true, min_priority: "normal" },
    { category: "calendar", enabled: true, min_priority: "normal" },
    { category: "approvals", enabled: true, min_priority: "low" },
    { category: "system", enabled: false, min_priority: "high" },
  ],
  quiet_hours: { start: "22:00:00", end: "06:00:00", critical_overrides_quiet: true, timezone: "Africa/Nairobi" },
};

function serve() {
  const log: { agent?: Record<string, unknown>; prefs?: Record<string, unknown>; purge?: string; revoked?: string } = {};
  server.use(
    http.get(`${BASE}/agent/settings`, () => HttpResponse.json({ granted_permissions: ["dashboard.read"] })),
    http.put(`${BASE}/agent/settings`, async ({ request }) => {
      log.agent = (await request.json()) as Record<string, unknown>;
      return HttpResponse.json(log.agent);
    }),
    http.get(`${BASE}/notification-preferences`, () => HttpResponse.json(PREFS)),
    http.put(`${BASE}/notification-preferences`, async ({ request }) => {
      log.prefs = (await request.json()) as Record<string, unknown>;
      return HttpResponse.json(PREFS);
    }),
    http.get(`${BASE}/devices`, () =>
      HttpResponse.json({
        results: [
          { id: "d1", platform: "web", name: "Web browser", app_version: "x", last_seen_at: null, revoked_at: null, created_at: "2026-11-01T00:00:00Z", is_current: true, push_provider: "", push_enabled: false },
          { id: "d2", platform: "android", name: "Pixel", app_version: "x", last_seen_at: null, revoked_at: null, created_at: "2026-11-01T00:00:00Z", is_current: false, push_provider: "fcm", push_enabled: true },
        ],
        next_cursor: null,
      }),
    ),
    http.delete(`${BASE}/devices/d2`, () => { log.revoked = "d2"; return new HttpResponse(null, { status: 204 }); }),
    http.delete(`${BASE}/phone/records`, ({ request }) => {
      log.purge = new URL(request.url).search;
      return HttpResponse.json({ deleted: { calls: 3, messages: 4, sms: 1 } });
    }),
  );
  return log;
}

describe("SettingsPage", () => {
  it("saves exactly the permissions the person ticked", async () => {
    const log = serve();
    renderWithApp(<SettingsPage />);
    const mail = await screen.findByLabelText(/Read email metadata/);
    expect(screen.getByLabelText(/Read dashboard summaries/)).toBeChecked();
    expect(mail).not.toBeChecked();
    await userEvent.click(mail);
    await userEvent.click(screen.getByRole("button", { name: "Save permissions" }));
    await waitFor(() => { expect(log.agent).toEqual({ granted_permissions: ["dashboard.read", "mail.read"] }); });
  });

  it("saves notification preferences and quiet hours", async () => {
    const log = serve();
    renderWithApp(<SettingsPage />);
    await screen.findByRole("form", { name: "Notification preferences" });
    await userEvent.click(screen.getByLabelText("System"));
    await userEvent.click(screen.getByRole("button", { name: "Save preferences" }));
    await waitFor(() => { expect(log.prefs).toBeDefined(); });
    expect(log.prefs).toEqual({
      preferences: [
        { category: "mail", enabled: true, min_priority: "normal" },
        { category: "calendar", enabled: true, min_priority: "normal" },
        { category: "approvals", enabled: true, min_priority: "low" },
        { category: "system", enabled: true, min_priority: "high" },
      ],
      quiet_hours: { start: "22:00:00", end: "06:00:00", critical_overrides_quiet: true },
    });
  });

  it("revokes another device but offers no button for this one", async () => {
    const log = serve();
    renderWithApp(<SettingsPage />);
    await screen.findByText(/Pixel/);
    expect(screen.getAllByRole("button", { name: "Sign out device" })).toHaveLength(1);
    await userEvent.click(screen.getByRole("button", { name: "Sign out device" }));
    await waitFor(() => { expect(log.revoked).toBe("d2"); });
  });

  it("only deletes phone data after the confirmation phrase is typed", async () => {
    const log = serve();
    renderWithApp(<SettingsPage />);
    const button = await screen.findByRole("button", { name: "Delete all phone records" });
    expect(button).toBeDisabled();
    await userEvent.type(screen.getByLabelText(/Type DELETE to confirm/), "delete");
    expect(button).toBeDisabled();
    await userEvent.clear(screen.getByLabelText(/Type DELETE to confirm/));
    await userEvent.type(screen.getByLabelText(/Type DELETE to confirm/), "DELETE");
    expect(button).toBeEnabled();
    await userEvent.click(button);
    expect(await screen.findByText("Deleted 8 stored phone records.")).toBeInTheDocument();
    expect(log.purge).toBe("?all=true");
  });
});
