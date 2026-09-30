import { spawn } from 'node:child_process'
import { existsSync, mkdirSync, copyFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'
import './harness/patch-dsh-acp.mjs'

const root = dirname(fileURLToPath(import.meta.url))
const historyDirectory = join(root, 'history')
const localHome = join(historyDirectory, 'deepseek')
const profile = join(localHome, 'profiles', 'acp', 'cordis.patch.yml')
const dshBinary = join(root, 'node_modules', '.bin', 'dsh')
const gatewayCli = join(root, 'gateway', 'cli', 'bin', 'qwenaudio.mjs')

if (!existsSync(dshBinary)) {
  throw new Error('DeepSeek Harness is missing. Run npm install in Qwen-Omni first.')
}
if (!existsSync(join(root, 'gateway', 'node_modules'))) {
  throw new Error('Gateway dependencies are missing. Run npm install in Qwen-Omni/gateway first.')
}
mkdirSync(dirname(profile), { recursive: true })
// This ACP profile is generated from the project template; apply updates on
// every launch so a stale first-run copy cannot keep the coding-agent tools.
copyFileSync(join(root, 'harness', 'cordis.patch.yml'), profile)

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
    PATH: `${join(root, 'node_modules', '.bin')}:${dirname(process.execPath)}:${process.env.PATH || ''}`,
  },
})
for (const signal of ['SIGINT', 'SIGTERM']) process.on(signal, () => child.kill(signal))
child.on('exit', (code, signal) => {
  process.exitCode = signal ? 1 : code ?? 1
})
