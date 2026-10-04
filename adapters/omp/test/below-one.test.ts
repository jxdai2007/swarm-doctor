import {test, expect} from "bun:test";
import {mkdtemp, writeFile, symlink, rm, realpath} from "node:fs/promises";
import {tmpdir} from "node:os";
import {join} from "node:path";
import {setTimeout as delay} from "node:timers/promises";
import {Transport, normalizeTool, confinedPath, readResource, readLiteralText, UNREACHABLE} from "../below-one";

const CAPABILITY = "private-agent-capability-123456789";

test("named shell read stays a read; unclear access never invented", () => {
  expect(normalizeTool("bash", {command: "cat 'reports/export.py'"}).paths).toEqual(["reports/export.py"]);
  expect(normalizeTool("bash", {command: "cat reports/export.py"}).operation).toBe("read");
  expect(normalizeTool("bash", {command: "python arbitrary_script.py | tee unknown"}).operation).toBe("unknown");
  expect(normalizeTool("bash", {command: "python arbitrary_script.py | tee unknown"}).input.unknown_access).toBe(true);
  expect(normalizeTool("below_one_send", {recipient: "a1", content: "note"}).operation).toBe("send");
  expect(normalizeTool("below_one_inbox", {}).operation).toBe("receive");
});

test("local transport fails closed on missing/malformed/contradictory answers", async () => {
  const values = [{allow: true}, {allow: true, reason: "bad", state: "frozen", decision_id: "d", label: "clean", confidence: .9, elapsed: 0}];
  for (const value of values) {
    const client = new Transport("http://127.0.0.1:1", "a0", CAPABILITY, async () => new Response(JSON.stringify(value)));
    expect((await client.ask("/ready")).allow).toBe(false);
    expect((await client.ask("/decide", {})).reason).toBe(UNREACHABLE);
  }
  const unavailable = new Transport("http://127.0.0.1:1", "a0", CAPABILITY, async () => {throw new Error("offline");});
  expect((await unavailable.ask("/decide")).allow).toBe(false);
  expect(() => new Transport("https://example.com", "a0", CAPABILITY)).toThrow();
});

test("frozen and denied answers preserve reason; agent never receives control token", async () => {
  let body = "";
  const client = new Transport("http://localhost:1", "a1", CAPABILITY, async (_url, init) => {
    body = String(init?.body);
    expect(new Headers(init?.headers).get("authorization")).toBe(`Bearer ${CAPABILITY}`);
    return new Response(JSON.stringify({allow: false, reason: "Frozen after trace", state: "frozen", decision_id: "ready", label: "violation", confidence: .9, elapsed: 2, proposal_seq: null, proposal_elapsed: null}));
  });
  expect((await client.ask("/ready")).reason).toBe("Frozen after trace");
  expect(JSON.parse(body)).toEqual({agent_id: "a1"});
  expect(body).not.toContain("operator");
});

test("actual filesystem confinement rejects outside, symlink, and locked spec", async () => {
  const root = await mkdtemp(join(tmpdir(), "below-one-omp-paths-"));
  try {
    await writeFile(join(root, "good.py"), "good");
    await symlink(join(root, "good.py"), join(root, "alias"));
    expect(await confinedPath(root, "good.py")).toBe(join(await realpath(root), "good.py"));
    for (const path of ["../escape", "/tmp/escape", "alias", "goal-spec.json"]) await expect(confinedPath(root, path)).rejects.toThrow();
  } finally {await rm(root, {recursive: true});}
});

test("read resources preserve literal colon files and canonical selector targets", async () => {
  const root = await mkdtemp(join(tmpdir(), "below-one-omp-selectors-"));
  try {
    await writeFile(join(root, ".env.production"), "protected");
    await writeFile(join(root, ".env.production:1"), "literal");
    const literal = await readResource(root, ".env.production:1");
    expect(literal.path).toBe(".env.production:1");
    expect(literal.inputPath).toBe(".env.production:1");
    expect(await readResource(root, ".env.production:1:conflicts")).toEqual({path: ".env.production:1", inputPath: ".env.production:1:conflicts", selector: "conflicts"});
    await writeFile(join(root, "notes:topic"), "literal colon base");
    const notes = await readResource(root, "notes:topic:raw");
    expect(notes.path).toBe("notes:topic");
    expect(notes.inputPath).toBe("notes:topic:raw");
    for (const suffix of ["1-", "-2", "1,3", "raw:1-2", "1-2:raw", "raw", "conflicts", "L1..L3", "img"]) {
      expect((await readResource(root, `./.env.production:${suffix}`)).path).toBe(".env.production");
    }
    for (const suffix of ["raw:raw", "conflicts:1", "0", "-0", "3-1", "1+0", "1+", "raw:-2-3", "sqlite"]) {
      await expect(readResource(root, `.env.production:${suffix}`)).rejects.toThrow();
    }
    await expect(readResource(root, "missing.py:1")).rejects.toThrow();
    expect(normalizeTool("write", {path: ".env.production:1", content: "literal"}).paths).toEqual([".env.production:1"]);
  } finally {await rm(root, {recursive: true});}
});

test("literal text delivers actual bounded ranges and rejects binary or unsupported rendering", async () => {
  const root = await mkdtemp(join(tmpdir(), "below-one-omp-literal-"));
  const text = Array.from({length: 310}, (_, i) => `actual-line-${i + 1}`).join("\n") + "\n";
  const read = async (name: string) => (await readLiteralText(root, await readResource(root, name))).content[0].text;
  try {
    await writeFile(join(root, "text:topic"), text);
    expect(await read("text:topic:raw:10-12")).toBe("actual-line-10\nactual-line-11\nactual-line-12\n");
    expect(await read("text:topic:10+3:raw")).toBe(await read("text:topic:raw:10-12"));
    expect(await read("text:topic:raw:12,10,11")).toBe(await read("text:topic:raw:10-12"));
    expect(await read("text:topic:raw:-2")).toBe("actual-line-309\nactual-line-310\n");
    expect(await read("text:topic:L10..L12")).toBe(Array.from({length: 7}, (_, i) => `${i + 9}|actual-line-${i + 9}`).join("\n"));
    const head = await read("text:topic");
    expect(head).toContain("300|actual-line-300");
    expect(head).not.toContain("301|actual-line-301");
    expect(head).toContain("page with :N-M");
    await writeFile(join(root, "no-newline:topic"), "one\ntwo");
    expect(await read("no-newline:topic:raw")).toBe("one\ntwo");
    await writeFile(join(root, "binary:topic"), Buffer.from([0, 255, 1]));
    await expect(read("binary:topic:raw")).rejects.toThrow("not UTF-8 text");
    await writeFile(join(root, "control:topic"), "before\0after");
    await expect(read("control:topic")).rejects.toThrow("binary control");
    await expect(read("text:topic:img")).rejects.toThrow("UTF-8 text ranges");
    await expect(read("text:topic:conflicts")).rejects.toThrow("UTF-8 text ranges");
    await writeFile(join(root, "huge:topic"), Buffer.alloc(4 * 1024 * 1024 + 1, 65));
    await expect(read("huge:topic")).rejects.toThrow("at most 4 MiB");
    await writeFile(join(root, "long:topic"), "A".repeat(600));
    expect((await read("long:topic")).replace(/^1\|/, "")).toBe("A".repeat(512) + "…");
    await writeFile(join(root, "oversized-line:topic"), "A".repeat(60_000));
    expect(await read("oversized-line:topic:raw:1-1")).toBe("A".repeat(50 * 1024) + "\n[Literal text output limited to 3000 lines and 51200 bytes; page with :N-M.]");
    await writeFile(join(root, "many-lines:topic"), Array.from({length: 3100}, (_, i) => String(i + 1)).join("\n"));
    const capped = await read("many-lines:topic:raw:1-3100");
    expect(capped).toContain("\n3000\n");
    expect(capped).not.toContain("\n3001\n");
    expect(capped).toContain("limited to 3000 lines");
  } finally { await rm(root, {recursive: true}); }
});

test("only authenticated finish survives a slow local grader; ordinary transport stays bounded", async () => {
  const requests: string[] = [];
  const server = Bun.serve({
    hostname: "127.0.0.1", port: 0, idleTimeout: 60,
    async fetch(request) {
      const body = await request.json() as {agent_id: string};
      if (request.headers.get("authorization") !== `Bearer ${CAPABILITY}` || body.agent_id !== "a0") return new Response("forbidden", {status: 403});
      requests.push(new URL(request.url).pathname);
      await delay(31_000);
      return Response.json({completed: true});
    },
  });
  try {
    const client = new Transport(`http://127.0.0.1:${server.port}`, "a0", CAPABILITY);
    const ordinary = client.post("/record", {}).then(() => false, () => true);
    expect(await client.post("/adapter/finish", {agent_id: "a1"})).toEqual({completed: true});
    expect(await ordinary).toBe(true);
    expect(requests.sort()).toEqual(["/adapter/finish", "/record"]);
  } finally {server.stop(true);}
}, 60_000);
