import type { Page, Route } from "@playwright/test";

/** Minimal in-memory stand-in for the backend. Every request is answered here; nothing leaves the browser. */

export interface Calls {
  logins: unknown[];
  messages: unknown[];
  decisions: unknown[];
  connects: number;
}

const TOKENS = () => ({
  token_type: "Bearer",
  access_token: "access-e2e",
  access_expires_at: new Date(Date.now() + 15 * 60_000).toISOString(),
  refresh_token: "refresh-e2e",
  refresh_expires_at: new Date(Date.now() + 86_400_000).toISOString(),
  device: {},
});

export const SUMMARY = {
  range: { preset: "week", from: "", to: "", from_date: "2026-11-23", to_date: "2026-11-29", timezone: "Africa/Nairobi" },
  spending: {
    available: true, reason: null, source: "finance", data_as_of: "2026-11-25T07:00:00Z", currency: "KES",
    total: { amount_minor: 123456, currency: "KES" }, included_count: 7,
    by_category: [{ category_id: "c1", category: "Groceries", total: { amount_minor: 80000, currency: "KES" } }],
    by_day: [],
  },
  calls: { available: true, reason: null, source: "phone", data_as_of: "2026-11-25T07:00:00Z", received: 12, missed: 3, outgoing: 9, rejected: 1, total_duration_seconds: 3725, by_day: [] },
  messages: { available: true, reason: null, source: "phone", data_as_of: "2026-11-25T07:00:00Z", sent: 40, received: 55, by_day: [] },
  email: { available: false, reason: "mail_not_connected", source: "none", data_as_of: null },
  activity: {
    available: true, reason: null, source: "agent", data_as_of: "2026-11-25T07:00:00Z", pending_approvals: 1,
    unread_notifications: { total: 2, by_priority: { low: 0, normal: 1, high: 1, critical: 0 } }, active_workflows: 0,
  },
};

const CONVERSATION = { id: "c1111111-1111-4111-8111-111111111111", title: "Planning", archived_at: null, created_at: "2026-11-25T06:00:00Z", updated_at: "2026-11-25T06:00:00Z" };
const WORKFLOW = {
  id: "w1111111-1111-4111-8111-111111111111", conversation: CONVERSATION.id, state: "awaiting_approval", origin: "text", tainted: false,
  goal: "Book", llm_turns: 1, tokens: 10, error_code: "", error_message: "", answer_message_id: "", cancel_requested_at: null,
  created_at: "2026-11-25T07:00:00Z", started_at: "2026-11-25T07:00:01Z", finished_at: null, steps: [],
};
const APPROVAL = {
  id: "a1111111-1111-4111-8111-111111111111", workflow: WORKFLOW.id, tool_name: "calendar_create_event",
  action_payload: { title: "Dentist" }, payload_sha256: "cd".repeat(32), destination_summary: "Google Calendar: primary",
  tainted: false, state: "pending", expires_at: new Date(Date.now() + 600_000).toISOString(), decided_at: null, created_at: "2026-11-25T07:00:00Z",
};

const json = (route: Route, body: unknown, status = 200) =>
  route.fulfill({ status, contentType: "application/json", body: JSON.stringify(body) });

export async function mockApi(page: Page): Promise<Calls> {
  const calls: Calls = { logins: [], messages: [], decisions: [], connects: 0 };
  let decided = false;
  let sent = false;

  await page.route("**/api/v1/**", async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    const path = url.pathname.replace("/api/v1", "");
    const method = request.method();
    const body = request.postData() ? (JSON.parse(request.postData() ?? "null") as unknown) : null;

    if (path === "/auth/login") {
      calls.logins.push(body);
      return json(route, TOKENS());
    }
    if (path === "/auth/refresh") return json(route, TOKENS());
    if (path === "/auth/logout") return route.fulfill({ status: 204 });
    if (path === "/dashboard/summary") return json(route, SUMMARY);
    if (path === "/agent/approvals" && method === "GET") {
      const pending = sent && !decided && url.searchParams.get("state") === "pending";
      return json(route, { results: pending ? [APPROVAL] : [], next_cursor: null });
    }
    if (path === "/agent/conversations" && method === "GET") return json(route, { results: [CONVERSATION], next_cursor: null });
    if (path === `/agent/conversations/${CONVERSATION.id}/messages`) {
      if (method === "POST") {
        calls.messages.push(body);
        sent = true;
        return json(route, { message: { id: "m1", conversation: CONVERSATION.id, role: "user", content: "Book the dentist", source: "text", status: "complete", created_at: "2026-11-25T07:00:00Z" }, workflow: WORKFLOW }, 202);
      }
      const history = [];
      if (decided) history.push({ id: "m2", conversation: CONVERSATION.id, role: "assistant", content: "Booked for Friday.", source: "text", status: "complete", created_at: "2026-11-25T07:01:00Z" });
      if (sent) history.push({ id: "m1", conversation: CONVERSATION.id, role: "user", content: "Book the dentist", source: "text", status: "complete", created_at: "2026-11-25T07:00:00Z" });
      return json(route, { results: history, next_cursor: null });
    }
    if (path === `/agent/workflows/${WORKFLOW.id}/stream`) {
      const snapshot = decided
        ? { workflow: { ...WORKFLOW, state: "succeeded" }, approvals: [], answer: null }
        : { workflow: WORKFLOW, approvals: [APPROVAL], answer: null };
      const frames = `event: snapshot\ndata: ${JSON.stringify(snapshot)}\n\n` + (decided ? "event: done\ndata: {}\n\n" : "event: timeout\ndata: {}\n\n");
      return route.fulfill({ status: 200, contentType: "text/event-stream", body: frames });
    }
    if (path === `/agent/approvals/${APPROVAL.id}/decision`) {
      calls.decisions.push(body);
      decided = true;
      return json(route, { ...APPROVAL, state: "approved" });
    }
    if (path === "/integrations" && method === "GET") return json(route, { results: [], next_cursor: null });
    if (path === "/integrations/connect") {
      calls.connects += 1;
      return json(route, { authorization_url: "https://evil.example/steal" });
    }
    return json(route, { error: { code: "not_mocked", message: `Unmocked ${method} ${path}`, details: null, request_id: null } }, 500);
  });
  return calls;
}
