import { useEffect, useState } from "react";
import type { FormEvent } from "react";
import { useSearchParams } from "react-router-dom";
import type { ApiError } from "../../api/client";
import { newIdempotencyKey } from "../../api/client";
import type { Page, SocialAccount, SocialAnalytics, SocialPost } from "../../api/types";
import { Card, ErrorNotice, Unavailable } from "../../components/Card";
import { formatDateTime } from "../../lib/time";
import { asApiError, useApi, useResource } from "../../hooks/useApi";

const IN_FLIGHT = new Set(["queued", "publishing"]);
const POLL_MS = 3000;

/** Only follow a consent URL that really points at Facebook over https. */
export function isTrustedFacebookUrl(value: string): boolean {
  try {
    const url = new URL(value);
    return url.protocol === "https:" && url.hostname === "www.facebook.com";
  } catch {
    return false;
  }
}

const STATE_TEXT: Record<string, string> = {
  draft: "Draft - not approved",
  approved: "Approved - not yet published",
  queued: "Publishing...",
  publishing: "Publishing...",
  published: "Published",
  failed: "Not published",
  uncertain: "Unknown - check your Page",
  cancelled: "Cancelled",
};

const ERROR_TEXT: Record<string, string> = {
  publish_rejected: "Facebook refused the post.",
  permission_denied: "Facebook says this login may not post to the Page.",
  blocked_by_platform: "Facebook blocked the post.",
  provider_rate_limited: "Facebook is rate limiting posts. Try again later.",
  token_rejected: "The Page connection was rejected. Reconnect the Page.",
  approval_expired: "The approval expired.",
  approval_mismatch: "The stored post no longer matches what was approved.",
  account_unavailable: "The Page was disconnected or changed.",
  not_found_on_page: "The post was not found on the Page.",
  owner_confirmed: "You confirmed the outcome.",
  worker_lost: "The connection dropped while sending.",
  outcome_unknown: "The reply from Facebook was lost.",
  unexpected_error: "Something unexpected happened while sending.",
};

export function SocialPage({ redirect = (url: string) => { window.location.assign(url); } }: { redirect?: (url: string) => void }) {
  const api = useApi();
  const [params] = useSearchParams();
  const account = useResource<SocialAccount | null>("social-account", (signal) =>
    api.get<SocialAccount>("/social/account", { signal }).catch((e: unknown) => {
      if (asApiError(e).status === 404) return null;
      throw e;
    }),
  );
  const posts = useResource<Page<SocialPost>>("social-posts", (signal) =>
    api.get("/social/posts", { query: { limit: 30 }, signal }),
  );
  const analytics = useResource<SocialAnalytics>("social-analytics", (signal) =>
    api.get("/social/analytics", { signal }),
  );
  const candidates = useResource<{ page_id: string; name: string }[]>(
    `social-candidates-${params.get("status") ?? ""}`,
    (signal) => (params.get("status") === "select_page" ? api.get("/social/connect/candidates", { signal }) : Promise.resolve([])),
  );
  const [error, setError] = useState<ApiError | null>(null);
  const [note, setNote] = useState<string | null>(null);
  const [text, setText] = useState("");
  const [link, setLink] = useState("");

  const rows = posts.data?.results ?? [];
  const reloadPosts = posts.reload;
  const anyInFlight = rows.some((p) => IN_FLIGHT.has(p.state));
  useEffect(() => {
    if (!anyInFlight) return undefined;
    const timer = setInterval(reloadPosts, POLL_MS);
    return () => {
      clearInterval(timer);
    };
  }, [anyInFlight, reloadPosts]);

  const run = (action: Promise<unknown>, message?: string): void => {
    setError(null);
    setNote(null);
    action
      .then(() => {
        if (message) setNote(message);
        posts.reload();
        account.reload();
        analytics.reload();
      })
      .catch((e: unknown) => {
        setError(asApiError(e));
        posts.reload();
      });
  };

  const connect = (): void => {
    setError(null);
    api
      .post<{ authorization_url: string }>("/social/connect")
      .then((r) => {
        if (!isTrustedFacebookUrl(r.authorization_url)) {
          setError(asApiError(new Error("The server returned an unexpected sign-in address, so it was not opened.")));
          return;
        }
        redirect(r.authorization_url);
      })
      .catch((e: unknown) => {
        setError(asApiError(e));
      });
  };

  const saveDraft = (event: FormEvent): void => {
    event.preventDefault();
    run(
      api
        .post("/social/posts", { body: { text, link }, idempotencyKey: newIdempotencyKey() })
        .then(() => {
          setText("");
          setLink("");
        }),
      "Draft saved. Nothing is published until you approve and publish it.",
    );
  };

  const connected = account.data?.status === "connected";
  const returned = params.get("status");
  return (
    <main>
      <h1>Social</h1>
      {returned === "connected" ? <p className="notice ok" role="status">Page connected.</p> : null}
      {returned === "error" ? <p className="notice error" role="alert">The connection was not completed ({(params.get("reason") ?? "error").replaceAll("_", " ")}).</p> : null}
      {note ? <p className="notice ok" role="status">{note}</p> : null}
      {error ? <ErrorNotice error={error} /> : null}
      {account.error ? <ErrorNotice error={account.error} onRetry={account.reload} /> : null}

      <Card title="Facebook Page">
        {candidates.data && candidates.data.length > 0 ? (
          <div>
            <p>Choose the Page to publish to:</p>
            <ul className="plain">
              {candidates.data.map((c) => (
                <li key={c.page_id}>
                  <button type="button" onClick={() => { run(api.post("/social/connect/select", { body: { page_id: c.page_id } }), "Page connected."); }}>
                    {c.name}
                  </button>
                </li>
              ))}
            </ul>
          </div>
        ) : connected && account.data ? (
          <>
            <p>
              Publishing to <strong>{account.data.page_name}</strong>
            </p>
            <button type="button" className="danger" onClick={() => { run(api.delete("/social/account"), "Disconnected. Remove the app in your Facebook settings (Business Integrations) as well."); }}>
              Disconnect
            </button>
          </>
        ) : (
          <>
            {account.data?.status === "reauth_required" ? <p className="notice warn">The Page connection was lost. Reconnect it to publish.</p> : null}
            <button type="button" onClick={connect}>
              {account.data ? "Reconnect Page" : "Connect Facebook Page"}
            </button>
          </>
        )}
      </Card>

      {connected ? (
        <form className="card" onSubmit={saveDraft} aria-label="New post">
          <h2>New post</h2>
          <label>
            Text
            <textarea value={text} maxLength={2000} rows={4} required onChange={(e) => { setText(e.target.value); }} />
          </label>
          <label>
            Link (optional, https)
            <input type="url" value={link} maxLength={500} onChange={(e) => { setLink(e.target.value); }} />
          </label>
          <button type="submit" disabled={text.trim() === ""}>Save draft</button>
        </form>
      ) : null}

      <h2>Posts</h2>
      {posts.error ? <ErrorNotice error={posts.error} onRetry={posts.reload} /> : null}
      {posts.data && rows.length === 0 ? <p className="muted">No posts yet.</p> : null}
      <ul className="plain">
        {rows.map((p) => (
          <PostRow key={p.id} post={p} run={run} />
        ))}
      </ul>

      <Card title="Engagement" asOf={analytics.data?.data_as_of}>
        {analytics.error ? <ErrorNotice error={analytics.error} onRetry={analytics.reload} /> : null}
        {analytics.data && !analytics.data.available ? <Unavailable reason={analytics.data.reason} /> : null}
        {analytics.data?.available ? (
          analytics.data.totals ? (
            <p>
              {analytics.data.totals.reactions} reactions, {analytics.data.totals.comments} comments, {analytics.data.totals.shares} shares
              across {analytics.data.measured_count} of {analytics.data.published_count} posts in the last {analytics.data.window_days} days.
            </p>
          ) : (
            <p className="muted">No engagement figures have been fetched yet.</p>
          )
        ) : null}
        <p className="muted small">Reach and impressions are not collected.</p>
        <button type="button" className="secondary" onClick={() => { run(api.post("/social/analytics/refresh"), "Figures refreshed."); }}>
          Refresh now
        </button>
      </Card>
    </main>
  );
}

function PostRow({ post, run }: { post: SocialPost; run: (action: Promise<unknown>, message?: string) => void }) {
  const api = useApi();
  const act = (path: string, body?: object, message?: string): void => {
    run(api.post(`/social/posts/${post.id}/${path}`, body ? { body } : undefined), message);
  };
  const reason = post.error_code ? (ERROR_TEXT[post.error_code] ?? post.error_code.replaceAll("_", " ")) : null;
  return (
    <li className="card" aria-label={`Post ${post.state}`}>
      <div className="row between">
        <strong>{STATE_TEXT[post.state] ?? post.state}</strong>
        <span className="muted small">{formatDateTime(post.published_at ?? post.created_at)}</span>
      </div>
      {/* Post text is rendered as text, never as markup. */}
      <p className="pre">{post.text}</p>
      {post.link ? <p className="muted small">Link: {post.link}</p> : null}
      {post.tainted ? <p className="notice warn small">Drafted by the assistant from content that came from outside; read it carefully.</p> : null}
      {reason ? <p className="notice warn small">{reason}</p> : null}
      {post.state === "uncertain" ? (
        <p className="notice warn">
          It is not known whether this was published. Look at your Page before doing anything; publishing again could create a duplicate.
        </p>
      ) : null}
      <div className="row">
        {post.state === "draft" || post.state === "failed" ? (
          <button
            type="button"
            onClick={() => { act("approve", { content_sha256: post.content_sha256, confirmation: "tap" }, "Approved for 24 hours. Nothing is published until you press Publish."); }}
          >
            Approve this exact text
          </button>
        ) : null}
        {post.state === "approved" ? (
          <button type="button" onClick={() => { act("publish", undefined, "Publishing started."); }}>
            Publish to {post.page_name}
          </button>
        ) : null}
        {post.state === "uncertain" ? (
          <>
            <button type="button" onClick={() => { act("reconcile", undefined, "Checked your Page."); }}>Check my Page</button>
            <button type="button" className="secondary" onClick={() => { act("resolve", { outcome: "published" }, "Marked as published."); }}>It is on my Page</button>
            <button type="button" className="secondary" onClick={() => { act("resolve", { outcome: "not_published" }, "Marked as not published."); }}>It is not on my Page</button>
          </>
        ) : null}
        {["draft", "approved", "queued", "failed"].includes(post.state) ? (
          <button type="button" className="secondary" onClick={() => { act("cancel", undefined, "Cancelled."); }}>Cancel</button>
        ) : null}
      </div>
    </li>
  );
}
