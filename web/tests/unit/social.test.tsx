import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { describe, expect, it, vi } from "vitest";
import { SocialPage, isTrustedFacebookUrl } from "../../src/features/social";
import { BASE, renderWithApp } from "./helpers";
import { server } from "./server";

const ACCOUNT = {
  id: "a1", provider: "facebook", page_id: "p1", page_name: "Acme Page", status: "connected",
  connected_at: "2026-11-26T08:00:00Z", last_error: "",
};

function post(over: Record<string, unknown> = {}) {
  return {
    id: "post-1", page_id: "p1", page_name: "Acme Page", text: "We open on Monday", link: "",
    content_sha256: "a".repeat(64), state: "draft", source: "owner", tainted: false, approved_at: null,
    approval_expires_at: null, approved_via: "", queued_at: null, publish_attempts: 0, last_attempt_at: null,
    external_post_id: "", published_at: null, error_code: "", created_at: "2026-11-26T08:30:00Z",
    updated_at: "2026-11-26T08:30:00Z", ...over,
  };
}

const ANALYTICS = {
  available: true, reason: null, window_days: 30, published_count: 2, measured_count: 2,
  totals: { reactions: 12, comments: 3, shares: 1 }, data_as_of: "2026-11-26T07:00:00Z", not_collected: ["reach", "impressions"],
};

function mock(opts: { account?: unknown; posts?: unknown[]; analytics?: unknown } = {}) {
  server.use(
    http.get(`${BASE}/social/account`, () =>
      opts.account === null ? HttpResponse.json({ error: { code: "not_found", message: "Not found.", details: null, request_id: null } }, { status: 404 }) : HttpResponse.json(opts.account ?? ACCOUNT),
    ),
    http.get(`${BASE}/social/posts`, () => HttpResponse.json({ results: opts.posts ?? [], next_cursor: null })),
    http.get(`${BASE}/social/analytics`, () => HttpResponse.json(opts.analytics ?? ANALYTICS)),
  );
}

describe("isTrustedFacebookUrl", () => {
  it("accepts only https www.facebook.com", () => {
    expect(isTrustedFacebookUrl("https://www.facebook.com/v26.0/dialog/oauth?x=1")).toBe(true);
    expect(isTrustedFacebookUrl("http://www.facebook.com/")).toBe(false);
    expect(isTrustedFacebookUrl("https://www.facebook.com.evil.test/")).toBe(false);
    expect(isTrustedFacebookUrl("https://evil.test/?u=www.facebook.com")).toBe(false);
    expect(isTrustedFacebookUrl("javascript:alert(1)")).toBe(false);
  });
});

describe("SocialPage", () => {
  it("offers to connect when no Page is connected and follows only a trusted address", async () => {
    mock({ account: null });
    server.use(http.post(`${BASE}/social/connect`, () => HttpResponse.json({ authorization_url: "https://www.facebook.com/v26.0/dialog/oauth?c=1" })));
    const redirect = vi.fn();
    renderWithApp(<SocialPage redirect={redirect} />);
    await userEvent.click(await screen.findByRole("button", { name: "Connect Facebook Page" }));
    await waitFor(() => { expect(redirect).toHaveBeenCalledWith("https://www.facebook.com/v26.0/dialog/oauth?c=1"); });
    expect(screen.queryByRole("form", { name: "New post" })).not.toBeInTheDocument();
  });

  it("refuses an untrusted authorization address", async () => {
    mock({ account: null });
    server.use(http.post(`${BASE}/social/connect`, () => HttpResponse.json({ authorization_url: "https://evil.test/x" })));
    const redirect = vi.fn();
    renderWithApp(<SocialPage redirect={redirect} />);
    await userEvent.click(await screen.findByRole("button", { name: "Connect Facebook Page" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("unexpected sign-in address");
    expect(redirect).not.toHaveBeenCalled();
  });

  it("lets the owner pick a Page after a multi-Page login", async () => {
    mock({ account: null });
    const chosen: unknown[] = [];
    server.use(
      http.get(`${BASE}/social/connect/candidates`, () => HttpResponse.json([{ page_id: "p1", name: "One" }, { page_id: "p2", name: "Two" }])),
      http.post(`${BASE}/social/connect/select`, async ({ request }) => { chosen.push(await request.json()); return HttpResponse.json(ACCOUNT); }),
    );
    renderWithApp(<SocialPage redirect={vi.fn()} />, undefined, "/social?status=select_page");
    await userEvent.click(await screen.findByRole("button", { name: "Two" }));
    await waitFor(() => { expect(chosen).toEqual([{ page_id: "p2" }]); });
  });

  it("saves a draft with an idempotency key and says nothing is published yet", async () => {
    mock();
    const seen: { key: string | null; body: unknown }[] = [];
    server.use(http.post(`${BASE}/social/posts`, async ({ request }) => {
      seen.push({ key: request.headers.get("Idempotency-Key"), body: await request.json() });
      return HttpResponse.json(post(), { status: 201 });
    }));
    renderWithApp(<SocialPage redirect={vi.fn()} />);
    await userEvent.type(await screen.findByLabelText("Text"), "Hello <b>world</b>");
    await userEvent.click(screen.getByRole("button", { name: "Save draft" }));
    expect(await screen.findByRole("status")).toHaveTextContent("Nothing is published until you approve");
    expect(seen).toHaveLength(1);
    expect(seen[0]?.key).toMatch(/^[A-Za-z0-9._~-]{8,128}$/);
    expect(seen[0]?.body).toEqual({ text: "Hello <b>world</b>", link: "" });
  });

  it("renders post text as text, never as markup", async () => {
    mock({ posts: [post({ text: "<img src=x onerror=alert(1)> hi" })] });
    renderWithApp(<SocialPage redirect={vi.fn()} />);
    expect(await screen.findByText("<img src=x onerror=alert(1)> hi")).toBeInTheDocument();
    expect(document.querySelector("img")).toBeNull();
  });

  it("approves by echoing the shown hash, and publishing is a separate step", async () => {
    mock({ posts: [post()] });
    const calls: { path: string; body: unknown }[] = [];
    server.use(http.post(`${BASE}/social/posts/post-1/approve`, async ({ request }) => {
      calls.push({ path: "approve", body: await request.json() });
      return HttpResponse.json(post({ state: "approved" }));
    }));
    renderWithApp(<SocialPage redirect={vi.fn()} />);
    await userEvent.click(await screen.findByRole("button", { name: "Approve this exact text" }));
    await waitFor(() => { expect(calls).toEqual([{ path: "approve", body: { content_sha256: "a".repeat(64), confirmation: "tap" } }]); });
  });

  it("publishes an approved post", async () => {
    mock({ posts: [post({ state: "approved" })] });
    let published = 0;
    server.use(http.post(`${BASE}/social/posts/post-1/publish`, () => { published += 1; return HttpResponse.json(post({ state: "queued" }), { status: 202 }); }));
    renderWithApp(<SocialPage redirect={vi.fn()} />);
    await userEvent.click(await screen.findByRole("button", { name: "Publish to Acme Page" }));
    await waitFor(() => { expect(published).toBe(1); });
  });

  it("warns on an unknown outcome and offers no publish button", async () => {
    mock({ posts: [post({ state: "uncertain", error_code: "outcome_unknown" })] });
    renderWithApp(<SocialPage redirect={vi.fn()} />);
    expect(await screen.findByText(/could create a duplicate/)).toBeInTheDocument();
    expect(screen.getByText("The reply from Facebook was lost.")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /^Publish/ })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Check my Page" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "It is not on my Page" })).toBeInTheDocument();
  });

  it("flags an assistant draft built from outside content", async () => {
    mock({ posts: [post({ tainted: true, source: "assistant" })] });
    renderWithApp(<SocialPage redirect={vi.fn()} />);
    expect(await screen.findByText(/came from outside/)).toBeInTheDocument();
  });

  it("shows engagement exactly as the API reports it, with the fetch time and the gaps", async () => {
    mock({ posts: [] });
    renderWithApp(<SocialPage redirect={vi.fn()} />);
    const card = await screen.findByRole("region", { name: "Engagement" });
    expect(await within(card).findByText(/12 reactions, 3 comments, 1 shares across 2 of 2 posts in the last 30 days/)).toBeInTheDocument();
    expect(within(card).getByText(/as of/)).toBeInTheDocument();
    expect(within(card).getByText("Reach and impressions are not collected.")).toBeInTheDocument();
  });

  it("never shows zeros when nothing has been fetched", async () => {
    mock({ analytics: { ...ANALYTICS, totals: null, measured_count: 0, data_as_of: null } });
    renderWithApp(<SocialPage redirect={vi.fn()} />);
    expect(await screen.findByText("No engagement figures have been fetched yet.")).toBeInTheDocument();
    expect(screen.queryByText(/0 reactions/)).not.toBeInTheDocument();
  });

  it("asks to reconnect when the connection was lost", async () => {
    mock({ account: { ...ACCOUNT, status: "reauth_required" } });
    renderWithApp(<SocialPage redirect={vi.fn()} />);
    expect(await screen.findByText(/connection was lost/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Reconnect Page" })).toBeInTheDocument();
  });

  it("disconnects and reminds the owner to remove the app at Facebook", async () => {
    mock();
    server.use(http.delete(`${BASE}/social/account`, () => HttpResponse.json({ ...ACCOUNT, status: "revoked" })));
    renderWithApp(<SocialPage redirect={vi.fn()} />);
    await userEvent.click(await screen.findByRole("button", { name: "Disconnect" }));
    expect(await screen.findByText(/Business Integrations/)).toBeInTheDocument();
  });

  it("shows the server's error message", async () => {
    mock({ posts: [post({ state: "approved" })] });
    server.use(http.post(`${BASE}/social/posts/post-1/publish`, () =>
      HttpResponse.json({ error: { code: "daily_post_limit", message: "The daily post limit has been reached.", details: null, request_id: "r1" } }, { status: 429 }),
    ));
    renderWithApp(<SocialPage redirect={vi.fn()} />);
    await userEvent.click(await screen.findByRole("button", { name: "Publish to Acme Page" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("daily post limit");
  });
});
