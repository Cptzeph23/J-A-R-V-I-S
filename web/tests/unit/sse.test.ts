import { describe, expect, it, vi } from "vitest";
import { ApiError } from "../../src/api/client";
import type { ApiClient } from "../../src/api/client";
import { createSseParser, followWorkflow } from "../../src/api/sse";
import type { WorkflowSnapshot } from "../../src/api/types";
import { WORKFLOW } from "./fixtures";

function parse(chunks: string[]) {
  const events: { event: string; data: string }[] = [];
  const parser = createSseParser((e) => events.push(e));
  chunks.forEach((c) => { parser.push(c); });
  return events;
}

describe("createSseParser", () => {
  it("parses events, ignores comments and joins data lines", () => {
    expect(parse([": ping\n\nevent: snapshot\ndata: {\"a\":1}\n\nevent: done\ndata: {}\n\n"])).toEqual([
      { event: "snapshot", data: '{"a":1}' },
      { event: "done", data: "{}" },
    ]);
    expect(parse(["data: one\ndata: two\n\n"])).toEqual([{ event: "message", data: "one\ntwo" }]);
  });

  it("handles chunks split anywhere, including inside CRLF", () => {
    const text = "event: snapshot\r\ndata: x\r\n\r\n";
    for (let i = 1; i < text.length; i++) {
      expect(parse([text.slice(0, i), text.slice(i)])).toEqual([{ event: "snapshot", data: "x" }]);
    }
  });

  it("does not dispatch an event that has no data", () => {
    expect(parse(["event: ping\n\n"])).toEqual([]);
  });
});

function streamOf(text: string): Response {
  const body = new ReadableStream<Uint8Array>({
    start(controller) {
      controller.enqueue(new TextEncoder().encode(text));
      controller.close();
    },
  });
  return new Response(body, { headers: { "Content-Type": "text/event-stream" } });
}

const snap = (state: string): WorkflowSnapshot => ({
  workflow: { ...WORKFLOW, state } as WorkflowSnapshot["workflow"],
  approvals: [],
  answer: null,
});

const frame = (event: string, data: unknown) => `event: ${event}\ndata: ${JSON.stringify(data)}\n\n`;
const noSleep = () => Promise.resolve();

describe("followWorkflow", () => {
  it("delivers snapshots and stops on done", async () => {
    const open = vi.fn().mockResolvedValue(streamOf(frame("snapshot", snap("running")) + frame("snapshot", snap("succeeded")) + frame("done", {})));
    const seen: string[] = [];
    await followWorkflow({ open } as unknown as ApiClient, "w1", {
      signal: new AbortController().signal,
      onSnapshot: (s) => seen.push(s.workflow.state),
      sleep: noSleep,
    });
    expect(seen).toEqual(["running", "succeeded"]);
    expect(open).toHaveBeenCalledTimes(1);
  });

  it("reconnects after a timeout event", async () => {
    const open = vi
      .fn()
      .mockResolvedValueOnce(streamOf(frame("snapshot", snap("running")) + frame("timeout", {})))
      .mockResolvedValueOnce(streamOf(frame("snapshot", snap("succeeded")) + frame("done", {})));
    const seen: string[] = [];
    await followWorkflow({ open } as unknown as ApiClient, "w1", {
      signal: new AbortController().signal,
      onSnapshot: (s) => seen.push(s.workflow.state),
      sleep: noSleep,
    });
    expect(open).toHaveBeenCalledTimes(2);
    expect(seen).toEqual(["running", "succeeded"]);
  });

  it("stops quietly when the workflow is gone or not found", async () => {
    const gone = vi.fn().mockResolvedValue(streamOf(frame("gone", {})));
    await followWorkflow({ open: gone } as unknown as ApiClient, "w1", { signal: new AbortController().signal, onSnapshot: vi.fn(), sleep: noSleep });
    const notFound = vi.fn().mockRejectedValue(new ApiError(404, "not_found", "x"));
    await followWorkflow({ open: notFound } as unknown as ApiClient, "w1", { signal: new AbortController().signal, onSnapshot: vi.fn(), sleep: noSleep });
    expect(notFound).toHaveBeenCalledTimes(1);
  });

  it("falls back to polling after repeated stream failures, then finishes", async () => {
    const open = vi.fn().mockRejectedValue(new ApiError(0, "network_error", "down"));
    const get = vi
      .fn()
      .mockImplementation((path: string) =>
        Promise.resolve(path.startsWith("/agent/approvals") ? { results: [], next_cursor: null } : { ...WORKFLOW, state: "succeeded" }),
      );
    const modes: string[] = [];
    const seen: string[] = [];
    await followWorkflow({ open, get } as unknown as ApiClient, "w1", {
      signal: new AbortController().signal,
      onSnapshot: (s) => seen.push(s.workflow.state),
      onMode: (m) => modes.push(m),
      sleep: noSleep,
    });
    expect(open).toHaveBeenCalledTimes(3);
    expect(modes).toEqual(["polling"]);
    expect(seen).toEqual(["succeeded"]);
  });

  it("returns when aborted", async () => {
    const controller = new AbortController();
    const open = vi.fn().mockImplementation(() => {
      controller.abort();
      return Promise.reject(new DOMException("aborted", "AbortError"));
    });
    await followWorkflow({ open } as unknown as ApiClient, "w1", { signal: controller.signal, onSnapshot: vi.fn(), sleep: noSleep });
    expect(open).toHaveBeenCalledTimes(1);
  });
});
