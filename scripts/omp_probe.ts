import type { ExtensionAPI } from "@oh-my-pi/pi-coding-agent";
export default function (pi: ExtensionAPI) {
  const url = process.env.BELOW_ONE_PROBE_URL!;
  const report = async (kind: string, payload: unknown) => {
    const response = await fetch(url, {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify({kind, payload})});
    if (!response.ok) throw new Error(`probe server ${response.status}`);
  };
  pi.on("session_start", async (_event, ctx) => { await report("extension_loaded", {cwd: ctx.cwd}); });
  pi.on("tool_result", async (event) => {
    await report("tool_result", {toolName: event.toolName, input: event.input, content: event.content, details: event.details});
    if (event.toolName === "read") {
      pi.sendMessage({customType: "below-one-probe", content: "Operator correction: stop reading. Final response must be AFTER_STEER, not BEFORE_STEER.", display: false}, {deliverAs: "steer"});
      await report("steer_sent", {});
    }
  });
}
