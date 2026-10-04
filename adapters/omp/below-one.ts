import type { ExtensionAPI, ExtensionContext } from "@oh-my-pi/pi-coding-agent";
import { lstat, open, readFile, realpath } from "node:fs/promises";
import { constants } from "node:fs";
import { setTimeout as delay } from "node:timers/promises";
import { resolve, relative, isAbsolute, dirname } from "node:path";

export const UNREACHABLE = "Below One engine unreachable";
export type Action = {tool: string; operation: string; paths: string[]; input: Record<string, unknown>; action_id?: string};
type Decision = {allow: boolean; reason: string; state: string; decision_id: string; elapsed: number; proposal_seq: number; proposal_elapsed: number; label: string; confidence: number; messages?: unknown[]; steer?: string};

const OPERATIONS: Record<string, string> = {read: "read", write: "write", edit: "write", below_one_send: "send", below_one_inbox: "receive", below_one_finish: "finish"};
function object(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

export function normalizeTool(name: string, input: Record<string, unknown>): Action {
  const operation = OPERATIONS[name];
  if (operation) {
    const path = typeof input.path === "string" ? input.path : undefined;
    return {tool: name, operation, paths: path ? [path] : [], input: {...input}};
  }
  if (name === "bash") {
    const command = String(input.command ?? "");
    const match = /^(?:cat|head|tail|wc|sort)\s+(?:--\s+)?(?:"([^"\n]+)"|'([^'\n]+)'|([^\s;&|<>]+))\s*$/.exec(command);
    if (match) return {tool: name, operation: "read", paths: [match[1] ?? match[2] ?? match[3]], input: {...input}};
    if (/^(?:python3?|uv run python)\s+-I\s+-m\s+unittest\s+discover(?:\s+-s\s+tests)?\s*$/.test(command)) {
      return {tool: name, operation: "run", paths: ["tests"], input: {...input}};
    }
  }
  return {tool: name, operation: "unknown", paths: [`__unknown__/${name}`], input: {...input, unknown_access: true}};
}

export async function confinedPath(cwd: string, name: string): Promise<string> {
  const root = await realpath(cwd);
  const path = resolve(root, name);
  const rel = relative(root, path);
  if (isAbsolute(name) || /^[~@:\[]/.test(name) || name.includes("://") || /[\u00A0\u2000-\u200A\u202F\u205F\u3000]/.test(name) || rel === ".." || rel.startsWith("../") || name.includes("\0") || rel === "goal-spec.json") throw new Error("Tool path escapes workspace or targets locked spec");
  let parent = path;
  for (;;) {
    try {
      const canonical = await realpath(parent);
      if (canonical !== parent || (canonical !== root && !canonical.startsWith(root + "/"))) throw new Error("Symlink tool paths forbidden");
      break;
    } catch (error: unknown) {
      if (!(error instanceof Error) || !("code" in error) || error.code !== "ENOENT") throw error;
      if (parent === root) throw error;
      parent = dirname(parent);
    }
  }
  return path;
}

// Resource-only parser for omp v18.5.0's filesystem read selectors. Literal
// colon filenames win; unsupported URL/archive/sqlite selectors fail closed.
type ReadResource = {path: string; inputPath: string; selector: string};
export async function readResource(cwd: string, name: string): Promise<ReadResource> {
  let base = name, selector = "";
  try {
    await lstat(resolve(cwd, name));
  } catch (error: unknown) {
    if (!(error instanceof Error) || !("code" in error) || !["ENOENT", "ENOTDIR", "ENAMETOOLONG"].includes(String(error.code))) throw error;
    const colon = name.lastIndexOf(":");
    if (colon >= 0) {
      base = name.slice(0, colon);
      selector = name.slice(colon + 1);
      const range = (value: string) => {
        if (/^-\d+$/.test(value)) return Number(value.slice(1)) >= 1;
        return value.split(",").every(part => {
          const match = /^L?(\d+)(?:(\.\.|[-+])L?(\d+)?)?$/i.exec(part);
          if (!match || !Number.isSafeInteger(Number(match[1])) || Number(match[1]) < 1) return false;
          if (match[2] === "+") return match[3] !== undefined && Number(match[3]) >= 1;
          return match[3] === undefined || Number(match[3]) >= Number(match[1]);
        });
      };
      if (!["raw", "conflicts", "img"].includes(selector.toLowerCase()) && !range(selector)) throw new Error("Unsupported or invalid read selector");
      const inner = base.lastIndexOf(":");
      if (inner > 0) {
        const chunk = base.slice(inner + 1);
        if ((chunk.toLowerCase() === "raw" && range(selector)) || (selector.toLowerCase() === "raw" && range(chunk))) {
          selector = chunk + ":" + selector;
          base = base.slice(0, inner);
        }
      }
    }
  }
  const target = await confinedPath(cwd, base);
  // No runtime fuzzy/suffix lookup may redirect a checked missing path.
  await lstat(target);
  const path = relative(await realpath(cwd), target);
  return {path, inputPath: path + (selector ? ":" + selector : ""), selector};
}

// Native read decodes file URLs before archive dispatch, so only a real
// same-name override can preserve arbitrary literal-colon text files. This
// bounded text branch deliberately has no image/archive/database/AST fallback.
export async function readLiteralText(cwd: string, resource: ReadResource, signal?: AbortSignal) {
  const chunks = resource.selector.toLowerCase().split(":");
  if (chunks.includes("img") || chunks.includes("conflicts")) throw new Error("Literal-colon reads support UTF-8 text ranges and :raw only; use an ordinary text filename for image/conflict rendering");
  const absolute = await confinedPath(cwd, resource.path);
  signal?.throwIfAborted();
  const file = await open(absolute, constants.O_RDONLY | constants.O_NOFOLLOW);
  let bytes: Buffer;
  try {
    const stat = await file.stat();
    if (!stat.isFile() || stat.size > 4 * 1024 * 1024) throw new Error("Literal-colon reads require a regular UTF-8 text file of at most 4 MiB; use a smaller ordinary text resource");
    bytes = Buffer.alloc(stat.size + 1);
    let used = 0;
    while (used < bytes.length) {
      signal?.throwIfAborted();
      const result = await file.read(bytes, used, bytes.length - used, null);
      if (!result.bytesRead) break;
      used += result.bytesRead;
    }
    if (used > stat.size) throw new Error("Literal-colon file grew during read; retry after the writer completes");
    bytes = bytes.subarray(0, used);
  } finally { await file.close(); }
  signal?.throwIfAborted();
  let text: string;
  try { text = new TextDecoder("utf-8", {fatal: true, ignoreBOM: true}).decode(bytes); }
  catch { throw new Error("Literal-colon resource is not UTF-8 text; binary/archive/database rendering requires an ordinary filename"); }
  if (/[\u0000-\u0008\u000B\u000C\u000E-\u001F]/.test(text)) throw new Error("Literal-colon resource contains binary control bytes; only UTF-8 text is supported");
  const raw = chunks.includes("raw");
  if (!raw) text = text.replace(/^\uFEFF/, "").replace(/\r\n?/g, "\n");
  const lines = text.split("\n");
  if (text.endsWith("\n") || !text) lines.pop();
  const range = chunks.find(chunk => chunk && chunk !== "raw");
  let spans: {start: number; end?: number}[];
  if (!range) spans = [{start: 1}];
  else if (range.startsWith("-")) spans = [{start: Math.max(1, lines.length - Number(range.slice(1)) + 1), end: Math.max(1, lines.length)}];
  else {
    const parts = range.split(",");
    spans = parts.map(part => {
      const match = /^l?(\d+)(?:(\.\.|[-+])l?(\d+)?)?$/.exec(part)!;
      const start = Number(match[1]), rhs = match[3] === undefined ? undefined : Number(match[3]);
      return {start, end: match[2] === "+" ? start + rhs! - 1 : match[2] ? rhs : parts.length > 1 ? start : undefined};
    }).sort((a, b) => a.start - b.start);
  }
  const merged: typeof spans = [];
  for (const span of spans) {
    const last = merged.at(-1);
    if (last && (last.end === undefined || span.start <= last.end + 1)) {
      if (last.end !== undefined) last.end = span.end === undefined ? undefined : Math.max(last.end, span.end);
    } else merged.push({...span});
  }
  const selected = new Map<number, string>();
  let capped = false, collectedBytes = 0, partialLine: number | undefined;
  const maxLines = 3000;
  const requestedLines = merged.reduce((sum, span) => sum + Math.min(maxLines, (span.end === undefined ? 300 : span.end - span.start + 1) + (raw ? 0 : (span.start > 1 ? 1 : 0) + (span.end === undefined ? 0 : 3))), 0);
  const maxBytes = Math.max(50 * 1024, Math.min(maxLines, requestedLines) * 512);
  for (const span of merged) {
    const start = Math.max(1, span.start - (!raw && span.start > 1 ? 1 : 0));
    const end = Math.min(lines.length, span.end === undefined ? span.start + 299 : span.end + (raw ? 0 : 3));
    for (let number = start; number <= end; number++) {
      if (selected.has(number)) continue;
      let line = lines[number - 1];
      if (!raw && Buffer.byteLength(line) > 512) {
        // A UTF-8 column cap must never split a code point.
        line = new TextDecoder().decode(Buffer.from(line).subarray(0, 512)).replace(/\uFFFD$/, "") + "…";
      }
      const size = Buffer.byteLength(line) + 1;
      if (selected.size === maxLines || collectedBytes + size > maxBytes) {
        if (!selected.size && raw) {
          selected.set(number, new TextDecoder().decode(Buffer.from(line).subarray(0, maxBytes)).replace(/\uFFFD$/, ""));
          partialLine = number;
        }
        capped = true; break;
      }
      selected.set(number, line); collectedBytes += size;
    }
    if (capped) break;
    if (span.end === undefined && span.start + 299 < lines.length) capped = true;
  }
  let output = raw
    ? [...selected].map(([number, line]) => line + (number !== partialLine && (number < lines.length || text.endsWith("\n")) ? "\n" : "")).join("")
    : [...selected].map(([number, line]) => `${number}|${line}`).join("\n");
  if (!selected.size && lines.length && merged[0].start > lines.length) output = `Line ${merged[0].start} is beyond end of file (${lines.length} lines total).`;
  if (capped) output += `\n[Literal text output limited to ${maxLines} lines and ${maxBytes} bytes; page with :N-M.]`;
  return {content: [{type: "text" as const, text: output}], details: {resolvedPath: absolute}};
}

export class Transport {
  constructor(readonly url: string, readonly agent: string, readonly agentToken: string, readonly fetcher = fetch) {
    const parsed = new URL(url);
    if (parsed.protocol !== "http:" || !["localhost", "127.0.0.1", "[::1]"].includes(parsed.hostname)) throw new Error("Engine must be local HTTP");
    if (!agentToken) throw new Error("Launcher must assign agent capability");
  }
  async post(endpoint: string, body: Record<string, unknown>): Promise<Record<string, unknown>> {
    const response = await this.fetcher(this.url + endpoint, {method: "POST", headers: {"Content-Type": "application/json", "Authorization": `Bearer ${this.agentToken}`}, body: JSON.stringify({...body, agent_id: this.agent}), signal: AbortSignal.timeout(endpoint === "/adapter/finish" ? 59_000 : 30_000)});
    if (!response.ok) throw new Error(UNREACHABLE);
    const value: unknown = await response.json();
    if (!object(value)) throw new Error(UNREACHABLE);
    return value;
  }
  async ask(endpoint: string, body: Record<string, unknown> = {}): Promise<Decision> {
    try {
      const answer = await this.post(endpoint, body);
      const proposalValid = endpoint === "/ready"
        ? answer.proposal_seq === null && answer.proposal_elapsed === null
        : Number.isSafeInteger(answer.proposal_seq) && Number(answer.proposal_seq) >= 1 && typeof answer.proposal_elapsed === "number" && Number.isFinite(answer.proposal_elapsed) && answer.proposal_elapsed >= 0;
      if (typeof answer.allow !== "boolean" || typeof answer.reason !== "string" || typeof answer.decision_id !== "string" || typeof answer.label !== "string" || !["clean", "drift", "violation"].includes(answer.label) || typeof answer.state !== "string" || !["active", "steered", "escalated", "frozen", "killed", "ended"].includes(answer.state) || typeof answer.confidence !== "number" || !Number.isFinite(answer.confidence) || answer.confidence < 0 || answer.confidence > 1 || typeof answer.elapsed !== "number" || !Number.isFinite(answer.elapsed) || answer.elapsed < 0 || !proposalValid || (answer.allow && ["frozen", "killed", "ended"].includes(answer.state))) throw new Error(UNREACHABLE);
      return {allow: answer.allow, reason: answer.reason, decision_id: answer.decision_id, state: answer.state, label: answer.label, confidence: answer.confidence, elapsed: answer.elapsed, proposal_seq: Number(answer.proposal_seq), proposal_elapsed: Number(answer.proposal_elapsed), messages: Array.isArray(answer.messages) ? answer.messages : undefined, steer: typeof answer.steer === "string" ? answer.steer : undefined};
    } catch {
      return {allow: false, reason: UNREACHABLE, state: "unreachable", decision_id: "", elapsed: 0, proposal_seq: 0, proposal_elapsed: 0, label: "clean", confidence: 0};
    }
  }
}

export default function belowOne(pi: ExtensionAPI) {
  const agent = process.env.BELOW_ONE_AGENT_ID;
  const url = process.env.BELOW_ONE_ENGINE_URL;
  const agentToken = process.env.BELOW_ONE_AGENT_TOKEN;
  if (!agent || !url || !agentToken) throw new Error("Launcher must assign engine URL, agent ID, and agent capability");
  const client = new Transport(url, agent, agentToken);
  const pending = new Map<string, {action: Action; decision: Decision; before: Map<string, Buffer | null | undefined>; started: number; read?: ReadResource}>();
  let watcher: AbortController | undefined;
  let baselineElapsed = 0, baselineTime = performance.now();
  const elapsed = () => baselineElapsed + (performance.now() - baselineTime) / 1000;
  const halt = (ctx: ExtensionContext, reason: string) => { ctx.ui.notify(reason, "error"); ctx.abort(); };
  const ready = async (ctx: ExtensionContext) => {
    let answer = await client.ask("/ready");
    // Launch suspends the actual process tree during freezes. Awaited gates also
    // close the small interval before SIGSTOP, without aborting print mode.
    while (answer.state === "frozen" && !watcher?.signal.aborted) {
      await delay(20);
      answer = await client.ask("/ready");
    }
    baselineElapsed = answer.elapsed;
    baselineTime = performance.now();
    if (!answer.allow) halt(ctx, answer.reason);
    return answer;
  };

  // Both hooks are awaited by the real runtime, before a billed provider call.
  pi.on("turn_start", async (_event, ctx) => { await ready(ctx); });
  pi.on("before_provider_request", async (_event, ctx) => { await ready(ctx); });
  pi.on("session_start", async (_event, ctx) => {
    watcher = new AbortController();
    const signal = watcher.signal;
    void (async () => {
      try {
        const response = await fetch(url + "/events", {signal});
        if (!response.ok || !response.body) throw new Error(UNREACHABLE);
        const reader = response.body.getReader();
        const decoder = new TextDecoder();
        let buffer = "";
        while (!signal.aborted) {
          const chunk = await reader.read();
          if (chunk.done) throw new Error(UNREACHABLE);
          buffer += decoder.decode(chunk.value, {stream: true});
          let split;
          while ((split = buffer.indexOf("\n\n")) >= 0) {
            const frame = buffer.slice(0, split); buffer = buffer.slice(split + 2);
            const data = frame.split("\n").filter(line => line.startsWith("data: ")).map(line => line.slice(6)).join("\n");
            if (!data || data === "[done]") continue;
            const event: unknown = JSON.parse(data);
            if (!object(event)) throw new Error(UNREACHABLE);
            const state = object(event.states) ? event.states[agent] : undefined;
            if ((event.agent_id === agent && event.kind === "kill") || (typeof state === "string" && ["killed", "ended"].includes(state))) halt(ctx, `Agent ${state ?? event.kind}`);
          }
        }
      } catch {
        if (!signal.aborted) halt(ctx, UNREACHABLE);
      }
    })();
  });
  pi.on("session_shutdown", () => { watcher?.abort(); });

  pi.on("tool_call", async (event, ctx) => {
    const action = normalizeTool(event.toolName, event.input);
    action.action_id = `${agent}:${event.toolCallId}`;
    const before = new Map<string, Buffer | null | undefined>();
    let read: ReadResource | undefined;
    try {
      if (typeof event.input.cwd === "string" && resolve(ctx.cwd, event.input.cwd) !== resolve(ctx.cwd)) throw new Error("Shell cwd override forbidden");
      if (event.toolName === "read") {
        if (typeof event.input.path !== "string") throw new Error("Read path required");
        read = await readResource(ctx.cwd, event.input.path);
        action.paths = [read.path];
        action.input.path = read.inputPath;
      } else {
        action.paths = await Promise.all(action.paths.map(async path => path.startsWith("__unknown__/") ? path : relative(await realpath(ctx.cwd), await confinedPath(ctx.cwd, path))));
        if (typeof action.input.path === "string" && action.paths.length === 1) action.input.path = action.paths[0];
      }
    } catch (error: unknown) {
      const reason = error instanceof Error ? error.message : "Invalid tool path";
      const decision = await client.ask("/decide", {action});
      if (decision.decision_id) await client.post("/record", {action, result: {ok: false, effects: "none", cost_usd: 0, error: reason}, decision_id: decision.decision_id, elapsed: elapsed()});
      return {block: true, reason};
    }
    const decision = await client.ask("/decide", {action});
    if (decision.steer) pi.sendMessage({customType: "below-one-steer", content: decision.steer, display: true}, {deliverAs: "steer"});
    if (!decision.allow) {
      if (decision.decision_id) await client.post("/record", {action, result: {ok: false, effects: "none", cost_usd: 0, error: decision.reason}, decision_id: decision.decision_id, elapsed: elapsed()});
      if (["killed", "ended", "unreachable"].includes(decision.state)) halt(ctx, decision.reason);
      return {block: true, reason: decision.reason};
    }
    const started = performance.now() - (decision.elapsed - decision.proposal_elapsed) * 1000;
    const claim = await client.ask("/start", {decision_id: decision.decision_id});
    if (!claim.allow) {
      await client.post("/record", {action, result: {ok: false, effects: "none", cost_usd: 0, error: claim.reason}, decision_id: decision.decision_id, elapsed: elapsed()});
      if (claim.state !== "frozen") halt(ctx, claim.reason);
      return {block: true, reason: claim.reason};
    }
    pending.set(event.toolCallId, {action, decision, before, started, read});
    if (action.operation === "write") {
      for (const path of action.paths) before.set(path, await readFile(await confinedPath(ctx.cwd, path)).catch((error: unknown) => error instanceof Error && "code" in error && error.code === "ENOENT" ? null : undefined));
    }
    return {input: action.input};
  });
  pi.on("tool_result", async (event, ctx) => {
    const ticket = pending.get(event.toolCallId);
    if (!ticket) return;
    pending.delete(event.toolCallId);
    const result: Record<string, unknown> = {ok: !event.isError, effects: !event.isError ? "observed" : "none", cost_usd: 0, content: event.content.filter(part => part.type === "text").map(part => part.text).join("\n"), details: event.details};
    if (ticket.action.operation === "write") {
      result.changed = false;
      result.effects = event.isError ? "possible" : "none";
      try {
        for (const [path, before] of ticket.before) {
          if (before === undefined) throw new Error("Pre-write state unavailable");
          const after = await readFile(await confinedPath(ctx.cwd, path)).catch((error: unknown) => {
            if (error instanceof Error && "code" in error && error.code === "ENOENT") return null;
            throw error;
          });
          if (before === null ? after !== null : after === null || !before.equals(after)) result.changed = true;
        }
        if (result.changed) result.effects = "observed";
      } catch { result.effects = "possible"; }
    }
    if (ticket.action.operation === "receive") result.messages = ticket.decision.messages ?? [];
    if (ticket.action.operation === "finish" && event.details) Object.assign(result, event.details);
    try { await client.post("/record", {action: ticket.action, result, decision_id: ticket.decision.decision_id, elapsed: ticket.decision.proposal_elapsed + (performance.now() - ticket.started) / 1000}); }
    catch { halt(ctx, UNREACHABLE); }
  });

  pi.registerTool({name: "read", label: "Read gated resource", description: "Read a gated workspace resource with inline selectors. Literal-colon filenames support bounded UTF-8 text only (4 MiB); image/conflict and binary rendering require ordinary filenames.", loadMode: "essential", approval: "read", parameters: pi.zod.object({path: pi.zod.string()}),
    async execute(callId, params, signal, onUpdate, ctx) {
      const resource = pending.get(callId)?.read;
      if (!resource || params.path !== resource.inputPath) throw new Error("Read requires its canonical claimed engine ticket");
      if (resource.path.includes(":")) return readLiteralText(ctx.cwd, resource, signal);
      // v18.5.0's same-tool invokeTool resolves the original native registry,
      // not this registered override, preserving native snapshots/bookkeeping.
      if (!ctx.invokeTool) throw new Error("omp v18.5.0 native read delegation is required");
      return ctx.invokeTool(params, {signal, onUpdate});
    }});

  pi.registerTool({name: "below_one_send", label: "Send teammate message", description: "Send a gated message to one launcher-assigned teammate.", loadMode: "essential", approval: "read", parameters: pi.zod.object({recipient: pi.zod.string(), content: pi.zod.string()}),
    async execute() { return {content: [{type: "text", text: "Message accepted; Engine records and delivers after this result."}], details: {}}; }});
  pi.registerTool({name: "below_one_inbox", label: "Read teammate inbox", description: "Read only messages actually delivered by Engine.", loadMode: "essential", approval: "read", parameters: pi.zod.object({}),
    async execute(callId) { const messages = pending.get(callId)?.decision.messages ?? []; return {content: [{type: "text", text: JSON.stringify(messages)}], details: {messages}}; }});
  pi.registerTool({name: "below_one_finish", label: "Check completion", description: "Run the real isolated held-out grader; never treat PASS or local tests as completion.", loadMode: "essential", approval: "read", parameters: pi.zod.object({}),
    async execute() { const result = await client.post("/adapter/finish", {}); return {content: [{type: "text", text: JSON.stringify(result)}], details: result}; }});

  // Runtime itself makes OpenAI-compatible requests; server holds all real
  // provider keys, U4 clients/cassettes, quota pin, and shared spend meter.
  pi.registerProvider("below-one", {baseUrl: `${url}/model/${agent}`, apiKey: agentToken, api: "openai-completions", models: [{id: "coding", name: process.env.BELOW_ONE_SYNTHETIC === "1" ? "SYNTHETIC DEV omp coding" : "Kimi coding via Below One", api: "openai-completions", reasoning: false, input: ["text"], cost: {input: 0, output: 0, cacheRead: 0, cacheWrite: 0}, contextWindow: 32000, maxTokens: 4096}]});
}
