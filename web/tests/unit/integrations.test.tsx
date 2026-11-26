import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { describe, expect, it, vi } from "vitest";
import { IntegrationsPage, isTrustedAuthUrl } from "../../src/features/integrations";
import { BASE, renderWithApp } from "./helpers";
import { server } from "./server";

describe("isTrustedAuthUrl", () => {
  it("accepts only https Google", () => {
    expect(isTrustedAuthUrl("https://accounts.google.com/o/oauth2/v2/auth?x=1")).toBe(true);
    expect(isTrustedAuthUrl("http://accounts.google.com/")).toBe(false);
    expect(isTrustedAuthUrl("https://accounts.google.com.evil.test/")).toBe(false);
    expect(isTrustedAuthUrl("https://evil.test/?u=accounts.google.com")).toBe(false);
    expect(isTrustedAuthUrl("javascript:alert(1)")).toBe(false);
    expect(isTrustedAuthUrl("not a url")).toBe(false);
  });
});

function listIntegrations(results: unknown[]) {
  server.use(http.get(`${BASE}/integrations`, () => HttpResponse.json({ results, next_cursor: null })));
}

describe("IntegrationsPage", () => {
  it("redirects to Google for a trusted authorization url", async () => {
    listIntegrations([]);
    server.use(http.post(`${BASE}/integrations/connect`, () => HttpResponse.json({ authorization_url: "https://accounts.google.com/o/oauth2/v2/auth?c=1" })));
    const redirect = vi.fn();
    renderWithApp(<IntegrationsPage redirect={redirect} />);
    const buttons = await screen.findAllByRole("button", { name: "Connect" });
    await userEvent.click(buttons[0]!);
    await waitFor(() => { expect(redirect).toHaveBeenCalledWith("https://accounts.google.com/o/oauth2/v2/auth?c=1"); });
  });

  it("refuses to follow an untrusted address", async () => {
    listIntegrations([]);
    server.use(http.post(`${BASE}/integrations/connect`, () => HttpResponse.json({ authorization_url: "https://evil.test/login" })));
    const redirect = vi.fn();
    renderWithApp(<IntegrationsPage redirect={redirect} />);
    await userEvent.click((await screen.findAllByRole("button", { name: "Connect" }))[0]!);
    expect(await screen.findByRole("alert")).toHaveTextContent("unexpected sign-in address");
    expect(redirect).not.toHaveBeenCalled();
  });

  it("shows status, syncs and disconnects", async () => {
    const calls: string[] = [];
    listIntegrations([
      {
        id: "i1", provider: "google", kind: "mail", status: "reauth_required", external_account_id: "me@example.com",
        granted_scopes: [], last_synced_at: null, next_sync_after: null, consecutive_failures: 2, last_error: "invalid_grant", created_at: "2026-11-01T00:00:00Z",
      },
    ]);
    server.use(
      http.post(`${BASE}/integrations/i1/sync`, () => { calls.push("sync"); return HttpResponse.json({}, { status: 202 }); }),
      http.delete(`${BASE}/integrations/i1`, () => { calls.push("delete"); return new HttpResponse(null, { status: 204 }); }),
    );
    renderWithApp(<IntegrationsPage redirect={vi.fn()} />);
    expect(await screen.findByText(/Needs to be re-authorised as me@example.com/)).toBeInTheDocument();
    expect(screen.getByText("invalid_grant")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Reconnect" })).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Sync now" }));
    await screen.findByText("Sync started.");
    await userEvent.click(screen.getByRole("button", { name: "Disconnect" }));
    await waitFor(() => { expect(calls).toEqual(["sync", "delete"]); });
  });

  it("reports the result of the OAuth return", async () => {
    listIntegrations([]);
    renderWithApp(<IntegrationsPage redirect={vi.fn()} />, undefined, "/integrations?status=access_denied");
    expect(await screen.findByRole("alert")).toHaveTextContent("access denied");
  });
});
