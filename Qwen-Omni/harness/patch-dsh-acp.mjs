import { createHash } from 'node:crypto'
import { readFileSync, writeFileSync } from 'node:fs'
import { createRequire } from 'node:module'

// DeepSeek Harness ACP 0.1.7 has followup and cancel, but does not expose
// Agent.steer over ACP. This bounded patch adds one private RPC for the exact
// dsh version pinned by this project; unknown releases fail closed.
const require = createRequire(import.meta.url)
const file = require.resolve('@deepseek-ai/dsh-acp')
const original = readFileSync(file, 'utf8')
const marker = 'openinteraction:session-steer-v3'
const legacyMarker = 'openinteraction:session-steer-v2'
if (original.includes(legacyMarker) && !original.includes(marker)) {
  const legacyHash = createHash('sha256').update(original).digest('hex')
  if (legacyHash !== 'f3bed065f51e4917d4e9213497c97765a7af1c81e4f766d822fec1562b3cad96') {
    throw new Error(`Unknown DeepSeek ACP steering patch (${legacyHash})`)
  }
  writeFileSync(file, original
    .replace(legacyMarker, marker)
    .replace(
      'if (!this.inflight || this.agent.status !== "running") throw invalidParams$1("session is not accepting steering");',
      'if (!this.inflight) throw invalidParams$1("session is not accepting steering");',
    ))
} else if (!original.includes(marker)) {

const sha256 = createHash('sha256').update(original).digest('hex')
if (sha256 !== 'c08ed51ebe53b7b45ca8f475630afc0271bf4ccb8b53f13b456fe2b4568a61b3') {
  throw new Error(`Unsupported DeepSeek ACP build (${sha256}); expected 0.1.7-rc.2`)
}

function replaceOnce(source, before, after) {
  if (!source.includes(before) || source.indexOf(before) !== source.lastIndexOf(before)) {
    throw new Error('DeepSeek ACP patch anchor is missing or ambiguous')
  }
  return source.replace(before, after)
}

let patched = replaceOnce(original,
  '\tasync prompt(params, imageEnabled, requestSignal) {',
  `\t// ${marker}\n\tsteer(text) {\n\t\tthis.assertActive();\n\t\tif (!this.inflight) throw invalidParams$1("session is not accepting steering");\n\t\tif (typeof text !== "string" || !text.trim()) throw invalidParams$1("steer text is required");\n\t\tif (this.ctx.agents.get(this.agent.id) !== this.agent) throw invalidParams$1("session is no longer active");\n\t\tthis.agent.steer(createUserMessage({ content: [{ type: "text", text: text.trim() }], source: { kind: "user" } }));\n\t\treturn { accepted: true };\n\t}\n\tasync prompt(params, imageEnabled, requestSignal) {`,
)
patched = replaceOnce(patched,
  '\t\tasync prompt(params, requestSignal) {',
  '\t\tsteer(params) {\n\t\t\tassertOpen();\n\t\t\treturn requireSession(brandString(params.sessionId)).steer(params.text);\n\t\t},\n\t\tasync prompt(params, requestSignal) {',
)
patched = replaceOnce(patched,
  '.onRequest(methods.agent.session.prompt, ({ params, signal }) => implementation.prompt(params, signal)).onNotification',
  '.onRequest(methods.agent.session.prompt, ({ params, signal }) => implementation.prompt(params, signal)).onRequest("session/steer", (params) => params, ({ params }) => implementation.steer(params)).onNotification',
)
writeFileSync(file, patched)
}
