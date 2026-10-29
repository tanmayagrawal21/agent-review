// Reads [{id, text}] as JSON on stdin; writes [{id, labels}] (Rampart entity labels found in each text).
// Runs fully offline once the model is cached; nothing leaves the machine.
import { createGuard } from "@nationaldesignstudio/rampart";

const chunks = [];
for await (const c of process.stdin) chunks.push(c);
const items = JSON.parse(Buffer.concat(chunks).toString("utf8") || "[]");
const guard = await createGuard({ device: "cpu" });
const out = [];
for (const { id, text } of items) {
  const r = await guard.protect(text);
  out.push({ id, labels: [...r.text.matchAll(/\[([A-Z_]+?)_\d+\]/g)].map((m) => m[1]) });
}
process.stdout.write(JSON.stringify(out));
