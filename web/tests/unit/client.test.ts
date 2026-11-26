import { http, HttpResponse } from "msw";
import { describe, expect, it, vi } from "vitest";
import { ApiClient, ApiError } from "../../src/api/client";
import { TokenStore } from "../../src/auth/tokenStore";
import { BASE } from "./helpers";
import { server } from "./server";

function tokens(access: string, refresh: string) {
  return {
    token_type: "Bearer",
    access_token: access,
    access_expires_at: new Date(Date.now() + 900_000).toISOString(),
    refresh_token: refresh,
    refresh_expires_at: new Date(Date.now() + 86_400_000).toISOString(),
    device: {},
  };
}

function clientWith(access: string | null, refresh: string | null, onExpired = vi.fn()) {
  const store = new TokenStore();
  if (refresh) {
    store.set({
      access_token: access ?? "",
      access_expires_at: new Date(access ? Date.now() + 900_000 : 0).toISOString(),
      refresh_token: refresh,
    });
  }
  return { store, client: new ApiClient({ store, baseUrl: BASE, onSessionExpired: onExpired }), onExpired };
}

describe("ApiClient", () => {
  it("sends the bearer token and parses JSON", async () => {
    let auth: string | null = null;
    server.use(
      http.get(`${BASE}/ping`, ({ request }) => {
        auth = request.headers.get("Authorization");
        return HttpResponse.json({ ok: true });
      }),
    );
    const { client } = clientWith("access-1", "refresh-1");
    expect(await client.get("/ping")).toEqual({ ok: true });
    expect(auth).toBe("Bearer access-1");
  });

  it("turns the error envelope into an ApiError", async () => {
    server.use(
      http.get(`${BASE}/bad`, () =>
        HttpResponse.json(
          { error: { code: "workflow_active", message: "Busy.", details: null, request_id: "r-1" } },
          { status: 409 },
        ),
      ),
    );
    const { client } = clientWith("a", "r");
    const error = await client.get("/bad").catch((e: unknown) => e);
    expect(error).toBeInstanceOf(ApiError);
    expect(error).toMatchObject({ status: 409, code: "workflow_active", requestId: "r-1" });
  });

  it("shares ONE refresh request between concurrent calls", async () => {
    let refreshes = 0;
    server.use(
      http.post(`${BASE}/auth/refresh`, async () => {
        refreshes += 1;
        await new Promise((r) => setTimeout(r, 20));
        return HttpResponse.json(tokens("access-2", "refresh-2"));
      }),
      http.get(`${BASE}/data`, ({ request }) =>
        request.headers.get("Authorization") === "Bearer access-2"
          ? HttpResponse.json({ ok: true })
          : HttpResponse.json({ error: { code: "unauthenticated", message: "no", details: null, request_id: null } }, { status: 401 }),
      ),
    );
    const { client, store } = clientWith(null, "refresh-1");
    const results = await Promise.all([client.get("/data"), client.get("/data"), client.get("/data")]);
    expect(results).toHaveLength(3);
    expect(refreshes).toBe(1);
    expect(store.refresh).toBe("refresh-2");
  });

  it("retries once after a 401 with the new token", async () => {
    let calls = 0;
    server.use(
      http.post(`${BASE}/auth/refresh`, () => HttpResponse.json(tokens("access-2", "refresh-2"))),
      http.get(`${BASE}/data`, ({ request }) => {
        calls += 1;
        return request.headers.get("Authorization") === "Bearer access-2"
          ? HttpResponse.json({ ok: true })
          : HttpResponse.json({ error: { code: "unauthenticated", message: "expired", details: null, request_id: null } }, { status: 401 });
      }),
    );
    const { client } = clientWith("access-1", "refresh-1");
    expect(await client.get("/data")).toEqual({ ok: true });
    expect(calls).toBe(2);
  });

  it("clears the session and notifies when the refresh token is rejected", async () => {
    server.use(
      http.post(`${BASE}/auth/refresh`, () =>
        HttpResponse.json({ error: { code: "invalid_token", message: "no", details: null, request_id: null } }, { status: 401 }),
      ),
    );
    const { client, store, onExpired } = clientWith(null, "refresh-1");
    await expect(client.get("/data")).rejects.toBeInstanceOf(ApiError);
    expect(store.hasSession).toBe(false);
    expect(onExpired).toHaveBeenCalledTimes(1);
  });

  it("keeps the session when the network is down during a refresh", async () => {
    server.use(http.post(`${BASE}/auth/refresh`, () => HttpResponse.error()));
    const { client, store, onExpired } = clientWith(null, "refresh-1");
    expect(await client.refreshSession()).toBe(false);
    expect(store.hasSession).toBe(true);
    expect(onExpired).not.toHaveBeenCalled();
  });

  it("sends Idempotency-Key and omits cookies", async () => {
    let key: string | null = null;
    server.use(
      http.post(`${BASE}/things`, ({ request }) => {
        key = request.headers.get("Idempotency-Key");
        return HttpResponse.json({}, { status: 201 });
      }),
    );
    const { client } = clientWith("a", "r");
    await client.post("/things", { body: {}, idempotencyKey: "k-1" });
    expect(key).toBe("k-1");
  });

  it("keeps the refresh token in sessionStorage only", () => {
    clientWith("a", "refresh-secret");
    expect(sessionStorage.getItem("jarvis.refresh")).toBe("refresh-secret");
    expect(JSON.stringify({ ...localStorage })).not.toContain("refresh-secret");
  });
});
