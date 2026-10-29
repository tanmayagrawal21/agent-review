// Local PII model (Rampart, CC BY 4.0, National Design Studio). Reads [{id, text}] as JSON on stdin and writes
// [{id, text, labels}]: the text with PII replaced by stable placeholders ([GIVEN_NAME_1] ...) and the entity
// labels found. One guard for the whole batch, so a value gets the same placeholder everywhere.
// Runs offline once the model is cached; nothing leaves the machine.
import { createGuard } from "@nationaldesignstudio/rampart";

const chunks = [];
for await (const c of process.stdin) chunks.push(c);
const items = JSON.parse(Buffer.concat(chunks).toString("utf8") || "[]");
const guard = await createGuard({ device: "cpu" });
const out = [];
for (const { id, text } of items) {
  const r = await guard.protect(text);
  out.push({ id, text: r.text, labels: [...r.text.matchAll(/\[([A-Z_]+?)_\d+\]/g)].map((m) => m[1]) });
}
process.stdout.write(JSON.stringify(out));
