// Export the frontend instructions and tool list that the harness (Qwen-Omni Gateway) gives its
// voice model, using the Gateway's own builders, so an offline runner can replay them exactly.
// The output contains the harness's private prompt: write it to a private directory, never into
// this benchmark folder.
//
//   node runners/dump_harness_prompt.mjs <gateway/server/src> <out.json>
//
// Context mirrors the Qwen-Omni deployment: backend configured, no frontend web retrieval
// (QWEN_AUDIO_WEB_TOOLS_ENABLED=0), no long-term memory, no pending permission or input.
import { writeFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { pathToFileURL } from 'node:url'

const [src, out] = process.argv.slice(2)
if (!src || !out) {
  console.error('usage: node dump_harness_prompt.mjs <gateway/server/src> <out.json>')
  process.exit(2)
}
const load = path => import(pathToFileURL(resolve(src, path)).href)
const { buildFrontendInstructions, frontendTools } = await load('frontend/frontend-tools.mjs')
const { buildFrontendToolContext } = await load('frontend/tools/frontend-tool-context.mjs')
const { config } = await load('core/config.mjs')

const agentContext = {
  client: { timeZone: 'America/New_York', locale: 'en-US' },
  frontend: buildFrontendToolContext({
    disabledTools: config.frontendDisabledTools || [],
    backendAvailability: { snapshot: () => ({ configured: true, ok: true, known: true }) },
  }),
  memories: [],
  recentMessages: [],
}
const tools = frontendTools(agentContext)
writeFileSync(out, JSON.stringify({
  instructions: buildFrontendInstructions(agentContext),
  tools,
}, null, 2))
console.log(`${tools.length} tools: ${tools.map(t => t.function.name).join(', ')}`)
