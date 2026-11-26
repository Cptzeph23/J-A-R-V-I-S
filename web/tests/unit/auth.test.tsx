import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it } from "vitest";
import { App } from "../../src/App";
import { ApiClient } from "../../src/api/client";
import { TokenStore } from "../../src/auth/tokenStore";
import { BASE } from "./helpers";
import { SUMMARY } from "./fixtures";
import { server } from "./server";

const TOKENS = {
  token_type: "Bearer",
  access_token: "access-1",
  access_expires_at: new Date(Date.now() + 900_000).toISOString(),
  refresh_token: "refresh-1",
  refresh_expires_at: new Date(Date.now() + 86_400_000).toISOString(),
  device: {},
};

function mount(client: ApiClient) {
  return render(
    <MemoryRouter>
      <App client={client} />
    </MemoryRouter>,
  );
}

function backend() {
  const logins: unknown[] = [];
  server.use(
    http.post(`${BASE}/auth/login`, async ({ request }) => {
      logins.push(await request.json());
      return HttpResponse.json(TOKENS);
    }),
    http.post(`${BASE}/auth/refresh`, () => HttpResponse.json({ ...TOKENS, refresh_token: "refresh-2" })),
    http.post(`${BASE}/auth/logout`, () => new HttpResponse(null, { status: 204 })),
    http.get(`${BASE}/dashboard/summary`, () => HttpResponse.json(SUMMARY)),
    http.get(`${BASE}/agent/approvals`, () => HttpResponse.json({ results: [], next_cursor: null })),
  );
  return logins;
}

describe("authentication", () => {
  it("shows the login page, signs in and lands on the dashboard", async () => {
    const logins = backend();
    const client = new ApiClient({ store: new TokenStore(), baseUrl: BASE });
    mount(client);
    await userEvent.type(await screen.findByLabelText("Email"), "me@example.com");
    await userEvent.type(screen.getByLabelText("Password"), "correct horse");
    await userEvent.click(screen.getByRole("button", { name: "Sign in" }));
    expect(await screen.findByRole("heading", { name: "Dashboard" })).toBeInTheDocument();
    expect(logins[0]).toMatchObject({ email: "me@example.com", password: "correct horse", device: { platform: "web", name: "Web browser" } });
    expect(sessionStorage.getItem("jarvis.refresh")).toBe("refresh-1");
    expect(localStorage.getItem("jarvis.refresh")).toBeNull();
  });

  it("shows the server's message for a wrong password", async () => {
    server.use(
      http.post(`${BASE}/auth/login`, () =>
        HttpResponse.json({ error: { code: "invalid_credentials", message: "Wrong email or password.", details: null, request_id: null } }, { status: 401 }),
      ),
    );
    mount(new ApiClient({ store: new TokenStore(), baseUrl: BASE }));
    await userEvent.type(await screen.findByLabelText("Email"), "me@example.com");
    await userEvent.type(screen.getByLabelText("Password"), "nope");
    await userEvent.click(screen.getByRole("button", { name: "Sign in" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Wrong email or password.");
    expect(screen.getByRole("button", { name: "Sign in" })).toBeEnabled();
  });

  it("restores the session after a reload through the refresh token", async () => {
    backend();
    sessionStorage.setItem("jarvis.refresh", "refresh-1");
    mount(new ApiClient({ store: new TokenStore(), baseUrl: BASE }));
    expect(await screen.findByRole("heading", { name: "Dashboard" })).toBeInTheDocument();
    expect(sessionStorage.getItem("jarvis.refresh")).toBe("refresh-2");
  });

  it("falls back to the login page when the stored session is dead", async () => {
    server.use(
      http.post(`${BASE}/auth/refresh`, () =>
        HttpResponse.json({ error: { code: "invalid_token", message: "x", details: null, request_id: null } }, { status: 401 }),
      ),
    );
    sessionStorage.setItem("jarvis.refresh", "stale");
    mount(new ApiClient({ store: new TokenStore(), baseUrl: BASE }));
    expect(await screen.findByRole("form", { name: "Sign in" })).toBeInTheDocument();
    expect(sessionStorage.getItem("jarvis.refresh")).toBeNull();
  });

  it("signs out and forgets the tokens", async () => {
    backend();
    sessionStorage.setItem("jarvis.refresh", "refresh-1");
    mount(new ApiClient({ store: new TokenStore(), baseUrl: BASE }));
    await screen.findByRole("heading", { name: "Dashboard" });
    await userEvent.click(screen.getByRole("button", { name: "Sign out" }));
    await waitFor(() => { expect(screen.getByRole("form", { name: "Sign in" })).toBeInTheDocument(); });
    expect(sessionStorage.getItem("jarvis.refresh")).toBeNull();
  });

  it("shows a pending-approvals badge in the navigation", async () => {
    backend();
    server.use(
      http.get(`${BASE}/agent/approvals`, () =>
        HttpResponse.json({ results: [{ id: "a1", workflow: "w", state: "pending" }, { id: "a2", workflow: "w", state: "pending" }], next_cursor: null }),
      ),
    );
    sessionStorage.setItem("jarvis.refresh", "refresh-1");
    mount(new ApiClient({ store: new TokenStore(), baseUrl: BASE }));
    expect(await screen.findByLabelText("2 pending")).toBeInTheDocument();
  });
});
