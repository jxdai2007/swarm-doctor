import {test, expect} from "bun:test";
import {mkdtemp, writeFile, symlink, rm, realpath} from "node:fs/promises";
import {tmpdir} from "node:os";
import {join} from "node:path";
import {Transport, normalizeTool, confinedPath, UNREACHABLE} from "../below-one";

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
    const client = new Transport("http://127.0.0.1:1", "a0", async () => new Response(JSON.stringify(value)));
    expect((await client.ask("/ready")).allow).toBe(false);
    expect((await client.ask("/decide", {})).reason).toBe(UNREACHABLE);
  }
  const unavailable = new Transport("http://127.0.0.1:1", "a0", async () => {throw new Error("offline");});
  expect((await unavailable.ask("/decide")).allow).toBe(false);
  expect(() => new Transport("https://example.com", "a0")).toThrow();
});

test("frozen and denied answers preserve reason; agent never receives control token", async () => {
  let body = "";
  const client = new Transport("http://localhost:1", "a1", async (_url, init) => {
    body = String(init?.body);
    return new Response(JSON.stringify({allow: false, reason: "Frozen after trace", state: "frozen", decision_id: "d2", label: "violation", confidence: .9, elapsed: 2}));
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
