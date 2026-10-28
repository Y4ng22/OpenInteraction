import { spawn } from 'node:child_process'
import { existsSync, mkdirSync, copyFileSync, readFileSync, writeFileSync } from 'node:fs'
import { dirname, join, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'
import { parseEnv } from 'node:util'
import './harness/patch-dsh-acp.mjs'
import { harnessProfileThroughLatentProxy } from './harness/latent-proxy-profile.mjs'

const root = dirname(fileURLToPath(import.meta.url))
const historyDirectory = join(root, 'history')
const localHome = join(historyDirectory, 'deepseek')
const profileTemplate = join(root, 'harness', 'cordis.patch.yml')
const profile = join(localHome, 'profiles', 'acp', 'cordis.patch.yml')
const dshBinary = join(root, 'node_modules', '.bin', 'dsh')
const gatewayCli = join(root, 'gateway', 'cli', 'bin', 'qwenaudio.mjs')
const envFile = join(root, '.env')

if (!existsSync(dshBinary)) {
  throw new Error('DeepSeek Harness is missing. Run pnpm install in Qwen-Omni first.')
}
if (!existsSync(join(root, 'gateway', 'node_modules'))) {
  throw new Error('Gateway dependencies are missing. Run pnpm install in Qwen-Omni/gateway first.')
}

// Only the latent switches are read here; the Gateway still loads .env itself.
const localEnv = existsSync(envFile) ? parseEnv(readFileSync(envFile, 'utf8')) : {}
const setting = name => String(process.env[name] ?? localEnv[name] ?? '').trim()
const latentProxyBaseUrl = setting('LATENT_PROXY_BASE_URL')
const latentStoreDirectory = setting('QWEN_AUDIO_LATENT_STORE_DIR')
  ? resolve(root, setting('QWEN_AUDIO_LATENT_STORE_DIR'))
  : join(historyDirectory, 'latents')

mkdirSync(dirname(profile), { recursive: true })
// This ACP profile is generated from the project template; apply updates on
// every launch so a stale first-run copy cannot keep the coding-agent tools.
if (latentProxyBaseUrl) {
  writeFileSync(profile, harnessProfileThroughLatentProxy(
    readFileSync(profileTemplate, 'utf8'),
    latentProxyBaseUrl,
  ))
  mkdirSync(latentStoreDirectory, { recursive: true })
  console.log(`[latent] Harness model requests go through ${latentProxyBaseUrl}; store: ${latentStoreDirectory}`)
} else {
  copyFileSync(profileTemplate, profile)
}

const child = spawn(process.execPath, ['--env-file=.env', gatewayCli, 'gateway'], {
  cwd: root,
  stdio: 'inherit',
  env: {
    ...process.env,
    DSH_HOME: localHome,
    QWAUDIO_CONFIG_DIR: join(root, '.gateway-state', 'config'),
    QWAUDIO_DATA_DIR: join(root, '.gateway-state', 'data'),
    QWAUDIO_STATE_DIR: join(root, '.gateway-state', 'state'),
    QWEN_AUDIO_SESSION_HISTORY_DIR: join(historyDirectory, 'sessions'),
    QWEN_AUDIO_AGENT_TASK_STATE_PATH: join(historyDirectory, 'tasks.json'),
    QWEN_AUDIO_AGENT_BACKEND_SESSION_STATE_PATH: join(historyDirectory, 'acp-sessions.json'),
    ...(latentProxyBaseUrl
      ? {
          QWEN_AUDIO_LATENT_ENABLED: '1',
          QWEN_AUDIO_LATENT_STORE_DIR: latentStoreDirectory,
        }
      : {}),
    PATH: `${join(root, 'node_modules', '.bin')}:${dirname(process.execPath)}:${process.env.PATH || ''}`,
  },
})
for (const signal of ['SIGINT', 'SIGTERM']) process.on(signal, () => child.kill(signal))
child.on('exit', (code, signal) => {
  process.exitCode = signal ? 1 : code ?? 1
})
