import type { ExtensionAPI, ExtensionContext } from "@oh-my-pi/pi-coding-agent";
import { readFile, realpath } from "node:fs/promises";
import { resolve, relative, isAbsolute, dirname } from "node:path";

export const UNREACHABLE = "Below One engine unreachable";
export type Action = {tool: string; operation: string; paths: string[]; input: Record<string, unknown>; action_id?: string};
type Decision = {allow: boolean; reason: string; state: string; decision_id: string; elapsed: number; label: string; confidence: number; messages?: unknown[]; steer?: string};

const OPERATIONS: Record<string, string> = {read: "read", write: "write", edit: "write", below_one_send: "send", below_one_inbox: "receive", below_one_finish: "finish"};
function object(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

export function normalizeTool(name: string, input: Record<string, unknown>): Action {
  const operation = OPERATIONS[name];
  if (operation) {
    const path = typeof input.path === "string" ? input.path.replace(/:(?:\d+(?:[-+,]\d+)*|raw)$/, "") : undefined;
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
  if (isAbsolute(name) || rel === ".." || rel.startsWith("../") || name.includes("\0") || rel === "goal-spec.json") throw new Error("Tool path escapes workspace or targets locked spec");
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

export class Transport {
  constructor(readonly url: string, readonly agent: string, readonly fetcher = fetch) {
    const parsed = new URL(url);
    if (parsed.protocol !== "http:" || !["localhost", "127.0.0.1", "[::1]"].includes(parsed.hostname)) throw new Error("Engine must be local HTTP");
  }
  async post(endpoint: string, body: Record<string, unknown>): Promise<Record<string, unknown>> {
    const response = await this.fetcher(this.url + endpoint, {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify({agent_id: this.agent, ...body}), signal: AbortSignal.timeout(30_000)});
    if (!response.ok) throw new Error(UNREACHABLE);
    const value: unknown = await response.json();
    if (!object(value)) throw new Error(UNREACHABLE);
    return value;
  }
  async ask(endpoint: string, body: Record<string, unknown> = {}): Promise<Decision> {
    try {
      const answer = await this.post(endpoint, body);
      if (typeof answer.allow !== "boolean" || typeof answer.reason !== "string" || typeof answer.decision_id !== "string" || typeof answer.label !== "string" || !["clean", "drift", "violation"].includes(answer.label) || typeof answer.state !== "string" || !["active", "steered", "escalated", "frozen", "killed", "ended"].includes(answer.state) || typeof answer.confidence !== "number" || !Number.isFinite(answer.confidence) || answer.confidence < 0 || answer.confidence > 1 || typeof answer.elapsed !== "number" || !Number.isFinite(answer.elapsed) || answer.elapsed < 0 || (answer.allow && ["frozen", "killed", "ended"].includes(answer.state))) throw new Error(UNREACHABLE);
      return {allow: answer.allow, reason: answer.reason, decision_id: answer.decision_id, state: answer.state, label: answer.label, confidence: answer.confidence, elapsed: answer.elapsed, messages: Array.isArray(answer.messages) ? answer.messages : undefined, steer: typeof answer.steer === "string" ? answer.steer : undefined};
    } catch {
      return {allow: false, reason: UNREACHABLE, state: "unreachable", decision_id: "", elapsed: 0, label: "clean", confidence: 0};
    }
  }
}

export default function belowOne(pi: ExtensionAPI) {
  const agent = process.env.BELOW_ONE_AGENT_ID;
  const url = process.env.BELOW_ONE_ENGINE_URL;
  if (!agent || !url) throw new Error("Launcher must assign engine URL and agent ID");
  const client = new Transport(url, agent);
  const pending = new Map<string, {action: Action; decision: Decision; before: Map<string, Buffer | null>}>();
  let watcher: AbortController | undefined;
  let baselineElapsed = 0, baselineTime = performance.now();
  const elapsed = () => baselineElapsed + (performance.now() - baselineTime) / 1000;
  const halt = (ctx: ExtensionContext, reason: string) => { ctx.ui.notify(reason, "error"); ctx.abort(); };
  const ready = async (ctx: ExtensionContext) => {
    const answer = await client.ask("/ready");
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
            if ((event.agent_id === agent && typeof event.kind === "string" && ["freeze", "kill"].includes(event.kind)) || (typeof state === "string" && ["frozen", "killed", "ended"].includes(state))) halt(ctx, `Agent ${state ?? event.kind}`);
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
    const before = new Map<string, Buffer | null>();
    try {
      if (typeof event.input.cwd === "string" && resolve(ctx.cwd, event.input.cwd) !== resolve(ctx.cwd)) throw new Error("Shell cwd override forbidden");
      for (const path of action.paths) {
        if (path.startsWith("__unknown__/")) continue;
        const target = await confinedPath(ctx.cwd, path);
        if (action.operation === "write") before.set(path, await readFile(target).catch(() => null));
      }
    } catch (error: unknown) {
      const reason = error instanceof Error ? error.message : "Invalid tool path";
      const decision = await client.ask("/decide", {action});
      if (decision.decision_id) await client.post("/record", {action, result: {ok: false, cost_usd: 0, error: reason}, decision_id: decision.decision_id, elapsed: elapsed()});
      return {block: true, reason};
    }
    const decision = await client.ask("/decide", {action});
    if (decision.steer) pi.sendMessage({customType: "below-one-steer", content: decision.steer, display: true}, {deliverAs: "steer"});
    if (!decision.allow) {
      if (decision.decision_id) await client.post("/record", {action, result: {ok: false, cost_usd: 0, error: decision.reason}, decision_id: decision.decision_id, elapsed: elapsed()});
      if (["frozen", "killed", "ended", "unreachable"].includes(decision.state)) halt(ctx, decision.reason);
      return {block: true, reason: decision.reason};
    }
    const claim = await client.ask("/start", {decision_id: decision.decision_id});
    if (!claim.allow) {
      await client.post("/record", {action, result: {ok: false, cost_usd: 0, error: claim.reason}, decision_id: decision.decision_id, elapsed: elapsed()});
      halt(ctx, claim.reason);
      return {block: true, reason: claim.reason};
    }
    pending.set(event.toolCallId, {action, decision, before});
  });
  pi.on("tool_result", async (event, ctx) => {
    const ticket = pending.get(event.toolCallId);
    if (!ticket) return;
    pending.delete(event.toolCallId);
    const result: Record<string, unknown> = {ok: !event.isError, cost_usd: 0, content: event.content.filter(part => part.type === "text").map(part => part.text).join("\n"), details: event.details};
    if (ticket.action.operation === "write") {
      result.changed = false;
      for (const [path, before] of ticket.before) {
        const after = await readFile(await confinedPath(ctx.cwd, path)).catch(() => null);
        if (before === null ? after !== null : after === null || !before.equals(after)) result.changed = true;
      }
    }
    if (ticket.action.operation === "receive") result.messages = ticket.decision.messages ?? [];
    if (ticket.action.operation === "finish" && event.details) Object.assign(result, event.details);
    try { await client.post("/record", {action: ticket.action, result, decision_id: ticket.decision.decision_id, elapsed: elapsed()}); }
    catch { halt(ctx, UNREACHABLE); }
  });

  pi.registerTool({name: "below_one_send", label: "Send teammate message", description: "Send a gated message to one launcher-assigned teammate.", loadMode: "essential", approval: "read", parameters: pi.zod.object({recipient: pi.zod.string(), content: pi.zod.string()}),
    async execute() { return {content: [{type: "text", text: "Message accepted; Engine records and delivers after this result."}], details: {}}; }});
  pi.registerTool({name: "below_one_inbox", label: "Read teammate inbox", description: "Read only messages actually delivered by Engine.", loadMode: "essential", approval: "read", parameters: pi.zod.object({}),
    async execute(callId) { const messages = pending.get(callId)?.decision.messages ?? []; return {content: [{type: "text", text: JSON.stringify(messages)}], details: {messages}}; }});
  pi.registerTool({name: "below_one_finish", label: "Check completion", description: "Run the real isolated held-out grader; never treat PASS or local tests as completion.", loadMode: "essential", approval: "read", parameters: pi.zod.object({}),
    async execute() { const result = await client.post("/adapter/finish", {}); return {content: [{type: "text", text: JSON.stringify(result)}], details: result}; }});

  // Runtime itself makes OpenAI-compatible requests; server holds all real
  // provider keys, U4 clients/cassettes, quota pin, and shared spend meter.
  pi.registerProvider("below-one", {baseUrl: `${url}/model/${agent}`, apiKey: "below-one-local-bridge", api: "openai-completions", models: [{id: "coding", name: process.env.BELOW_ONE_SYNTHETIC === "1" ? "SYNTHETIC DEV omp coding" : "Kimi coding via Below One", api: "openai-completions", reasoning: false, input: ["text"], cost: {input: 0, output: 0, cacheRead: 0, cacheWrite: 0}, contextWindow: 32000, maxTokens: 4096}]});
}
