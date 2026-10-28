// 端到端语音交互测试：驱动真实 Chrome（伪麦克风播放本地合成语音）打开
// http://127.0.0.1:3101/ 控制台，走完整 Gateway → DashScope Realtime 链路，
// 断言 ASR 转写、语音回复、后台任务播报顺序与打断行为。
//
// 用法：node e2e/voice-e2e.mjs [A|B|C|D|T1|T2 ...]（缺省全部）
// 环境变量：E2E_HEADED=1 用可见窗口；E2E_TAKEOVER=1 允许接管被占用的语音；
//   E2E_EXPECT_LATENT=1 要求场景 T 被改向的任务在 store 与任务记录中有最终 latentRef（需经 latent-proxy 启动）；
//   E2E_HISTORY_DIR 指定 Qwen-Omni/history 以外的记录目录。
//
// 运行前提：
//   - gateway 已重启并加载最新代码（web/dist 已重建）
//   - 用户在 3101 的 Chrome 标签页已关闭（voice ownership 按 owner 串行）
//   - dsh（DeepSeek Harness）在运行（场景 B/C）
import { chromium } from 'playwright'
import { existsSync, mkdtempSync, readFileSync, readdirSync, rmSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'
import { buildTimelineWav, pickVoice, synthesizeSpeech } from './wav-timeline.mjs'

const BASE_URL = 'http://127.0.0.1:3101/'
const VOICE_BUTTON = 'button.voice'
const HEADED = process.env.E2E_HEADED === '1'
const EXPECT_LATENT = process.env.E2E_EXPECT_LATENT === '1'
// Qwen-Omni/history：pnpm start 写入任务记录和 latent store 的位置。
const HISTORY_DIR = process.env.E2E_HISTORY_DIR
  || join(dirname(fileURLToPath(import.meta.url)), '..', '..', 'history')

// 页面收到的任务事件不带 latentRef（latent 只交给前台模型服务），所以直接检查
// latent-proxy 写入的 store 和 Gateway 持久化的任务记录。
async function latentRefFor(taskId, timeoutMs = 10_000) {
  const deadline = Date.now() + timeoutMs
  while (Date.now() < deadline) {
    const storeDir = join(HISTORY_DIR, 'latents', taskId)
    const finals = existsSync(storeDir)
      ? readdirSync(storeDir).filter(name => name.endsWith('.json'))
        .map(name => JSON.parse(readFileSync(join(storeDir, name), 'utf8')))
        .filter(manifest => manifest.final)
      : []
    let saved = null
    try {
      const state = JSON.parse(readFileSync(join(HISTORY_DIR, 'tasks.json'), 'utf8'))
      const tasks = Array.isArray(state) ? state : Object.values(state.tasks || state)
      saved = tasks.find(task => task.id === taskId)?.latentRef || null
    } catch {}
    const newest = finals.sort((left, right) => right.seq - left.seq)[0]
    if (newest && saved?.key === `${taskId}/${newest.seq}`) return saved
    await new Promise(resolve => setTimeout(resolve, 500))
  }
  return null
}
const TAKEOVER = process.env.E2E_TAKEOVER === '1'

class FrameLog {
  constructor() {
    this.items = []
    this.cursor = 0
  }

  push(frame) {
    this.items.push(frame)
  }

  // 从上次消费位置向后找第一个匹配帧。
  wait(predicate, timeoutMs, label) {
    return new Promise((resolve, reject) => {
      const start = Date.now()
      const check = () => {
        for (let index = this.cursor; index < this.items.length; index += 1) {
          if (predicate(this.items[index])) {
            this.cursor = index + 1
            return resolve(this.items[index])
          }
        }
        if (Date.now() - start > timeoutMs) {
          return reject(new Error(`等待超时（${label}）`))
        }
        setTimeout(check, 250)
      }
      check()
    })
  }

  countSince(timestamp, predicate = () => true) {
    return this.items.filter(frame => frame.ts >= timestamp && predicate(frame)).length
  }
}

function projectFrame(dir, raw) {
  try {
    const parsed = typeof raw === 'string' ? JSON.parse(raw) : raw
    if (!parsed?.type) return null
    if (dir === 'sent') {
      // 上行音频负载很大，只保留类型与回执所需的小字段。
      const frame = { dir, type: String(parsed.type), ts: Date.now() }
      for (const key of ['responseId', 'taskId', 'turnId']) {
        if (parsed[key] !== undefined) frame[key] = parsed[key]
      }
      return frame
    }
    const frame = { dir, type: String(parsed.type), ts: Date.now() }
    for (const key of [
      'turnId', 'responseId', 'taskId', 'taskIds', 'role', 'state',
      'origin', 'reason', 'content', 'status',
    ]) {
      if (parsed[key] !== undefined) frame[key] = parsed[key]
    }
    const task = parsed.task
    if (task && typeof task === 'object') {
      if (frame.taskId === undefined) frame.taskId = task.id
      if (task.status) frame.status = task.status
      if (task.objective) frame.objective = task.objective
      if (task.message) frame.message = task.message
      if (task.turnId) frame.turnId = frame.turnId || task.turnId
    }
    return frame
  } catch {
    return null
  }
}

async function launchPage({ wavPath = null, liveMic = false, sessionSuffix = '', log = null } = {}) {
  const args = [
    '--use-fake-ui-for-media-stream',
    '--use-fake-device-for-media-stream',
    '--autoplay-policy=no-user-gesture-required',
  ]
  if (wavPath) args.push(`--use-file-for-fake-audio-capture=${wavPath}`)
  const browser = await chromium.launch({ channel: 'chrome', headless: !HEADED, args })
  const context = await browser.newContext()
  if (liveMic) {
    // 由测试控制的麦克风：getUserMedia 返回一个 AudioContext 输出流，平时是
    // 静音，测试调用 window.__e2eSpeak 时才播放一句合成语音。页面自己的采集、
    // 门控和上行链路保持不变，只是说话时机可以跟着对话走。
    await context.addInitScript(() => {
      const devices = navigator.mediaDevices
      if (!devices?.getUserMedia) return
      const getUserMedia = devices.getUserMedia.bind(devices)
      let mic = null
      const ensureMic = async () => {
        if (!mic) {
          const context = new AudioContext({ sampleRate: 48_000 })
          mic = { context, destination: context.createMediaStreamDestination() }
        }
        if (mic.context.state !== 'running') await mic.context.resume()
        return mic
      }
      devices.getUserMedia = async constraints => {
        if (!constraints?.audio) return getUserMedia(constraints)
        // 页面关闭采集时会 stop 音轨，每次给一个独立的克隆。
        return (await ensureMic()).destination.stream.clone()
      }
      window.__e2eSpeak = async (base64, sampleRate) => {
        const { context, destination } = await ensureMic()
        const bytes = Uint8Array.from(atob(base64), char => char.charCodeAt(0))
        const pcm = new Int16Array(bytes.buffer)
        const buffer = context.createBuffer(1, pcm.length, sampleRate)
        const channel = buffer.getChannelData(0)
        for (let index = 0; index < pcm.length; index += 1) channel[index] = pcm[index] / 0x8000
        const source = context.createBufferSource()
        source.buffer = buffer
        source.connect(destination)
        await new Promise(resolve => {
          source.onended = resolve
          source.start()
        })
        return buffer.duration
      }
    })
  } else {
    // Chrome 155 的伪麦克风在 echoCancellation: { ideal: 'all' } 下会立刻结束音轨、
    // 不出声音，页面的采集生命周期随之反复重启。仅在测试中把它换成 true，页面、
    // Gateway 与模型链路保持原样。
    await context.addInitScript(() => {
      const devices = navigator.mediaDevices
      if (!devices?.getUserMedia) return
      const getUserMedia = devices.getUserMedia.bind(devices)
      devices.getUserMedia = constraints => {
        const audio = constraints?.audio
        if (audio && typeof audio === 'object' && audio.echoCancellation?.ideal === 'all') {
          return getUserMedia({ ...constraints, audio: { ...audio, echoCancellation: true } })
        }
        return getUserMedia(constraints)
      }
    })
  }
  const page = await context.newPage()
  const frames = new FrameLog()
  page.on('websocket', ws => {
    if (!ws.url().includes('/api/realtime')) return
    ws.on('framereceived', event => {
      const frame = projectFrame('recv', event.payload)
      if (frame) frames.push(frame)
    })
    ws.on('framesent', event => {
      const frame = projectFrame('sent', event.payload)
      if (frame) frames.push(frame)
    })
  })
  page.on('dialog', dialog => {
    // voice 被其他客户端占用时，接管需显式 opt-in，避免抢走用户会话。
    log?.(`[dialog] ${dialog.type()}: ${dialog.message()}`)
    if (TAKEOVER) dialog.accept()
    else dialog.dismiss()
  })
  const consoleMessages = []
  page.on('console', message => consoleMessages.push(message.text()))
  page.on('pageerror', error => consoleMessages.push(`pageerror: ${error.message}`))
  await page.goto(`${BASE_URL}?session=e2e-${sessionSuffix}-${Date.now()}`, {
    waitUntil: 'domcontentloaded',
  })
  return { browser, context, page, frames, consoleMessages }
}

async function enableMic(page, log) {
  log('点击「开启麦克风」…')
  await page.locator(VOICE_BUTTON).click()
  try {
    await page.waitForFunction(() => (
      document.querySelector('button.voice')?.getAttribute('aria-label') === '麦克风静音'
    ), null, { timeout: 20_000 })
  } catch (error) {
    const label = await page.locator(VOICE_BUTTON).getAttribute('aria-label')
    if (label === '取消等待') {
      throw new Error('语音正被其他客户端占用：请关闭 3101 的 Chrome 标签页/桌面端后重试，或设 E2E_TAKEOVER=1')
    }
    throw new Error(`麦克风未在 20s 内就绪（当前按钮：${label}）`)
  }
  log('麦克风已开启')
}

async function submitText(page, text, log) {
  log(`文本提交：${text}`)
  await page.locator('.multimodal-composer textarea').fill(text)
  await page.locator('.multimodal-composer .composer-send').click()
}

const isUserFinal = frame => (
  frame.type === 'transcript.final' && frame.role === 'user'
)
const isModelResponse = frame => (
  frame.type === 'response.started' && (frame.origin || 'model') === 'model'
)

function waitUntil(predicate, timeoutMs, label) {
  return new Promise((resolve, reject) => {
    const start = Date.now()
    const check = () => {
      if (predicate()) return resolve()
      if (Date.now() - start > timeoutMs) {
        return reject(new Error(`等待超时（${label}）`))
      }
      setTimeout(check, 250)
    }
    check()
  })
}

async function runScenario(name, run, log) {
  const started = Date.now()
  log(`=== 场景 ${name} 开始 ===`)
  try {
    await run(log)
    log(`=== 场景 ${name} 通过（${Math.round((Date.now() - started) / 1000)}s）===`)
    return true
  } catch (error) {
    log(`=== 场景 ${name} 失败：${error.message} ===`)
    return false
  }
}

// 任务控制（全程语音）：两次开口各建一个后台任务（多任务），再按 controls 的
// 顺序开口改向一个、取消另一个。改向必须作用于原任务（不另起新任务）；被改向
// 的任务完成并播报，被取消的任务不完成、不播报。
//
// 说话由测试控制的麦克风完成：会话就绪后才开口，每句都等模型上一句播完再说，
// 避免打断导致 ASR 丢掉句首。任务要求分多地检索，留出改向和取消的时间。
const SPEECH_SAMPLE_RATE = 16_000

function speechBase64(text, voice) {
  const samples = synthesizeSpeech(text, voice)
  const pcm = new Int16Array(samples.length)
  for (let index = 0; index < samples.length; index += 1) {
    pcm[index] = Math.round(Math.max(-1, Math.min(1, samples[index])) * 0x7fff)
  }
  return Buffer.from(pcm.buffer).toString('base64')
}

function taskControlScenario({ suffix, tasks, controls }) {
  return async log => {
    const voice = pickVoice()
    const steps = [...tasks, ...controls].map(step => ({ ...step, audio: speechBase64(step.say, voice) }))
    const { browser, page, frames } = await launchPage({ liveMic: true, sessionSuffix: suffix, log })
    const isCreated = frame => ['task.accepted', 'task.scheduled', 'task.running'].includes(frame.type)
    const firstSeen = taskId => frames.items.find(frame => isCreated(frame) && frame.taskId === taskId)?.ts ?? Infinity
    const createdIds = () => [...new Set(frames.items.filter(isCreated).map(frame => frame.taskId).filter(Boolean))]
    // 改向会把 objective 改成新指令，按任务第一次出现时的 objective 归类。
    const objectiveOf = taskId => frames.items
      .find(frame => frame.taskId === taskId && frame.objective)?.objective || ''
    const idsFor = keyword => createdIds().filter(id => objectiveOf(id).includes(keyword))
    const announcedIds = frame => [
      ...(Array.isArray(frame.taskIds) ? frame.taskIds : []),
      frame.taskId,
    ].filter(Boolean)
    // 模型空闲：最近一条 voice.state 为 idle，且此后有声音的回复都已播完、被打断
    // 或被清除，并且 1 秒内没有新的回复开始。只调用工具、不出声音的回复不会
    // 有 playback.* 回执，所以只追踪页面报告过 playback.started 的回复。
    const responseEnded = responseId => frames.items.some(frame => frame.responseId === responseId && (
      (frame.dir === 'sent' && ['playback.ended', 'playback.cancelled'].includes(frame.type))
      || (frame.dir === 'recv' && ['response.interrupted', 'playback.clear'].includes(frame.type))
    ))
    const modelIdle = since => {
      const voiceState = frames.items.filter(frame => frame.dir === 'recv' && frame.type === 'voice.state').at(-1)
      if (voiceState && voiceState.state !== 'idle') return false
      const playing = frames.items.filter(frame => (
        frame.dir === 'sent' && frame.type === 'playback.started' && frame.ts >= since
      ))
      if (!playing.every(frame => responseEnded(frame.responseId))) return false
      const lastStart = frames.items.filter(frame => frame.type === 'response.started').at(-1)?.ts ?? 0
      return Date.now() - Math.max(lastStart, since) >= 1_000
    }
    const waitModelIdle = async (since, timeoutMs = 20_000) => {
      const deadline = Date.now() + timeoutMs
      while (Date.now() < deadline) {
        if (modelIdle(since)) return
        await page.waitForTimeout(250)
      }
      log('模型 20 秒内未空闲，继续下一句')
    }
    let lastSpokeAt = Date.now()
    const say = async step => {
      await waitModelIdle(lastSpokeAt)
      log(`说：${step.say}`)
      lastSpokeAt = Date.now()
      await page.evaluate(([audio, rate]) => window.__e2eSpeak(audio, rate), [step.audio, SPEECH_SAMPLE_RATE])
      const heard = await frames.wait(
        frame => isUserFinal(frame) && frame.ts >= lastSpokeAt && String(frame.content || '').includes(step.keyword),
        30_000, `「${step.keyword}」的 ASR 转写`,
      )
      log(`转写：${heard.content}`)
      return heard
    }
    try {
      await enableMic(page, log)
      await frames.wait(frame => frame.type === 'voice.ready', 30_000, '实时语音就绪')
      await page.waitForTimeout(2_000)
      // 多任务：两次语音各创建一个后台任务，按关键词归类。
      for (const task of tasks) {
        await say(steps.find(step => step.say === task.say))
        await waitUntil(() => idsFor(task.keyword).length > 0, 60_000, `「${task.keyword}」后台任务被创建`)
      }
      const taskIds = Object.fromEntries(tasks.map(task => [task.keyword, idsFor(task.keyword)]))
      log(`多任务通过：${tasks.map(task => `${task.keyword}=${taskIds[task.keyword].join('/')}`).join('，')}`)
      const extraBefore = createdIds().filter(id => !Object.values(taskIds).flat().includes(id))
      if (extraBefore.length) log(`注意：模型额外创建了任务 ${extraBefore.join(', ')}`)

      let steeredId = null
      const cancelledIds = new Set()
      let controlsStartedAt = Infinity
      for (const control of controls) {
        const heard = await say(steps.find(step => step.say === control.say))
        controlsStartedAt = Math.min(controlsStartedAt, heard.ts)
        const targets = taskIds[control.target]
        if (control.kind === 'steer') {
          const isSteer = frame => (
            frame.type === 'task.updated'
            && targets.includes(frame.taskId)
            && String(frame.message || '').startsWith('已改向')
          )
          await waitUntil(() => frames.items.some(isSteer), 60_000, `「${control.target}」任务被改向`)
          steeredId = frames.items.find(isSteer).taskId
          log(`改向通过：${steeredId}`)
        } else {
          const isCancel = frame => frame.type === 'task.cancelled' && targets.includes(frame.taskId)
          await waitUntil(() => frames.items.some(isCancel), 60_000, `「${control.target}」任务被取消`)
          frames.items.filter(isCancel).forEach(frame => cancelledIds.add(frame.taskId))
          log(`取消通过：${[...cancelledIds].join(', ')}`)
        }
      }
      const lateTasks = createdIds().filter(id => firstSeen(id) >= controlsStartedAt)
      if (lateTasks.length) {
        throw new Error(`改向或取消时另起了新任务：${lateTasks.join(', ')}（应作用于原任务）`)
      }

      // 被改向的任务完成并播报；被取消的任务不完成、不播报。
      await frames.wait(
        frame => frame.type === 'task.completed' && frame.taskId === steeredId,
        600_000, `被改向任务 ${steeredId} 完成`,
      )
      if (EXPECT_LATENT) {
        const latentRef = await latentRefFor(steeredId)
        if (!latentRef) {
          throw new Error(`任务 ${steeredId} 完成后 store 与任务记录中没有对应的最终 latentRef（latent 链路未通）`)
        }
        log(`latent 通过：latentRef=${latentRef.key}（${latentRef.tokens}×${latentRef.dim} ${latentRef.dtype}）`)
      }
      const announcement = await frames.wait(
        frame => (
          frame.type === 'response.started'
          && frame.origin === 'announcement'
          && announcedIds(frame).includes(steeredId)
        ),
        120_000, `被改向任务 ${steeredId} 的结果播报`,
      )
      await frames.wait(
        frame => (
          frame.dir === 'sent'
          && frame.type === 'playback.ended'
          && frame.responseId === announcement.responseId
        ),
        180_000, '结果播报播放完毕回执',
      )
      log('被改向任务已完成并播报完毕')
      for (const cancelledId of cancelledIds) {
        if (frames.items.some(frame => frame.type === 'task.completed' && frame.taskId === cancelledId)) {
          throw new Error(`已取消的任务 ${cancelledId} 仍然完成了`)
        }
        if (frames.items.some(frame => (
          frame.type === 'response.started'
          && frame.origin === 'announcement'
          && announcedIds(frame).includes(cancelledId)
        ))) {
          throw new Error(`已取消的任务 ${cancelledId} 仍被当作结果播报`)
        }
      }
      log('已取消的任务没有完成也没有播报')
    } finally {
      await browser.close()
    }
  }
}

const SCENARIOS = {
  // 排空：把此前中止运行遗留的跨会话播报全部领走并播完，避免它们
  // 在新场景的第一问时刻注入并与语音相撞。连接两次：第一次领取连接
  // 时刻已有的遗留播报，第二次领取第一次期间才完成的任务。
  0: async log => {
    // 静默判定只看有意义的服务端下行帧：keep-alive 上行和 session 心跳不算。
    const activity = frame => (
      frame.dir === 'recv'
      && !String(frame.type).startsWith('session.')
      && !['error'].includes(frame.type)
    )
    for (const pass of [1, 2]) {
      const dir = mkdtempSync(join(tmpdir(), 'qwene2e-drain-'))
      const wavPath = join(dir, 'drain.wav')
      buildTimelineWav({ filePath: wavPath, voice: pickVoice(), utterances: [], tailSeconds: 10 })
      const { browser, page, frames } = await launchPage({ wavPath, sessionSuffix: 'drain', log })
      try {
        await enableMic(page, log)
        const quietSince = 20_000
        const deadline = Date.now() + 240_000
        while (Date.now() < deadline) {
          const last = frames.items.filter(activity).at(-1)
          if (last && Date.now() - last.ts >= quietSince) break
          await page.waitForTimeout(1_000)
        }
        const announcements = frames.items.filter(frame => (
          frame.type === 'response.started' && frame.origin === 'announcement'
        )).length
        log(`第 ${pass} 轮排空完成：领走遗留播报 ${announcements} 条`)
      } finally {
        await browser.close()
        rmSync(dir, { recursive: true, force: true })
      }
    }
  },

  // 回归：连续两问都必须出现 ASR 转写并得到语音回答。
  A: async log => {
    const dir = mkdtempSync(join(tmpdir(), 'qwene2e-A-'))
    const wavPath = join(dir, 'A.wav')
    buildTimelineWav({
      filePath: wavPath,
      voice: pickVoice(),
      tailSeconds: 120,
      utterances: [
        { text: '你好，请用一句话介绍一下你自己', atSec: 1.5 },
        { text: '现在几点了，用一句话回答我', atSec: 22 },
      ],
    })
    const { browser, page, frames, consoleMessages } = await launchPage({
      wavPath,
      sessionSuffix: 'A',
      log,
    })
    try {
      await enableMic(page, log)
      const firstSent = await frames.wait(
        frame => frame.dir === 'sent' && frame.type === 'audio.append',
        15_000, '第一条上行的 audio.append（麦克风通路）',
      )
      log(`第一条 audio.append 已上行`)
      const firstQuestion = await frames.wait(
        frame => isUserFinal(frame) && String(frame.content || '').includes('介绍'),
        45_000, '第一问 ASR 转写',
      )
      log(`第一问转写：${firstQuestion.content}`)
      await frames.wait(
        frame => isModelResponse(frame),
        90_000, '第一问的语音回答',
      )
      log('第一问已回答')
      const secondQuestion = await frames.wait(
        frame => isUserFinal(frame) && String(frame.content || '').includes('几点'),
        60_000, '第二问 ASR 转写（回归点：音频不再被拥塞丢弃）',
      )
      log(`第二问转写：${secondQuestion.content}`)
      await frames.wait(
        frame => isModelResponse(frame),
        90_000, '第二问的语音回答',
      )
      log('第二问已回答')
      // 门控生效：全程上行 audio.append 数量应远低于门控前的 ~375 条/秒。
      // 40s 场景内：keep-alive 2.5 条/秒 + 语音段 ~12.5 条/秒。
      const elapsedSeconds = Math.max(1, (Date.now() - firstSent.ts) / 1000)
      const appendCount = frames.countSince(firstSent.ts, frame => (
        frame.dir === 'sent' && frame.type === 'audio.append'
      ))
      log(`上行 audio.append 共 ${appendCount} 条（${(appendCount / elapsedSeconds).toFixed(1)} 条/秒，门控前约 375 条/秒）`)
      if (appendCount / elapsedSeconds > 30) {
        throw new Error('上行音频速率未下降，能量门控疑似未生效')
      }
      const userMessages = await page.locator('.messages article.user').count()
      await page.waitForFunction(
        count => document.querySelectorAll('.messages article.assistant').length >= count,
        2,
        { timeout: 60_000 },
      )
      const assistantMessages = await page.locator('.messages article.assistant').count()
      if (userMessages < 2 || assistantMessages < 2) {
        throw new Error(`DOM 消息数不足：user=${userMessages} assistant=${assistantMessages}`)
      }
      log(`DOM 消息：user=${userMessages} assistant=${assistantMessages}`)
      const errors = consoleMessages.filter(message => /error|失败|异常/i.test(message))
      if (errors.length) log(`页面 console 警告：${errors.slice(0, 3).join(' | ')}`)
    } finally {
      await browser.close()
      rmSync(dir, { recursive: true, force: true })
    }
  },

  // 工具与对话并行：后台任务运行期间闲聊照常回答；任务结果在闲聊之后播报。
  B: async log => {
    const dir = mkdtempSync(join(tmpdir(), 'qwene2e-B-'))
    const wavPath = join(dir, 'B.wav')
    buildTimelineWav({
      filePath: wavPath,
      voice: pickVoice(),
      tailSeconds: 480,
      utterances: [
        // 文本提交后有 30s 手动输入守卫，闲聊放在 25s 之后（守卫一般被
        // 模型对文本的响应提前解除）。
        { text: '顺便问一下，一加一等于几', atSec: 25 },
      ],
    })
    const { browser, page, frames } = await launchPage({ wavPath, sessionSuffix: 'B', log })
    try {
      await enableMic(page, log)
      await submitText(page, '帮我后台搜索一下最新的人工智能新闻，整理成要点告诉我', log)
      await frames.wait(
        frame => ['task.scheduled', 'task.accepted', 'task.running'].includes(frame.type),
        120_000, '后台任务被创建（spawn_thinking）',
      )
      log('后台任务已创建，任务执行期间继续说话')
      const chatQuestion = await frames.wait(
        frame => isUserFinal(frame) && String(frame.content || '').includes('一加一'),
        60_000, '任务运行期间闲聊的 ASR 转写',
      )
      log(`闲聊转写：${chatQuestion.content}`)
      const chatResponse = await frames.wait(
        frame => isModelResponse(frame),
        90_000, '闲聊的回答（任务不应阻塞对话）',
      )
      log(`闲聊回答已开始（responseId=${chatResponse.responseId}）`)
      const chatPlaybackEnded = await frames.wait(
        frame => (
          frame.dir === 'sent'
          && frame.type === 'playback.ended'
          && frame.responseId === chatResponse.responseId
        ),
        120_000, '闲聊回答播放完毕回执',
      )
      log('闲聊回答播放完毕')
      await frames.wait(
        frame => frame.type === 'task.completed' || frame.type === 'task.failed',
        240_000, '后台任务完成',
      )
      const announcement = await frames.wait(
        frame => frame.type === 'response.started' && frame.origin === 'announcement',
        90_000, '任务结果播报',
      )
      log(`任务结果播报已开始（responseId=${announcement.responseId}）`)
      if (announcement.ts <= chatPlaybackEnded.ts) {
        throw new Error('任务结果播报抢在闲聊播放结束之前开始')
      }
      log('顺序正确：闲聊优先，结果在闲聊之后播报')
    } finally {
      await browser.close()
      rmSync(dir, { recursive: true, force: true })
    }
  },

  // 多任务：按完成顺序逐个播报，两条播报不重叠、不抢播。
  // WAV 只有静音（本场景靠文本提交，不开麦说话）。
  C: async log => {
    const dir = mkdtempSync(join(tmpdir(), 'qwene2e-C-'))
    const wavPath = join(dir, 'C.wav')
    buildTimelineWav({
      filePath: wavPath,
      voice: pickVoice(),
      utterances: [],
      tailSeconds: 10,
    })
    const { browser, page, frames } = await launchPage({ wavPath, sessionSuffix: 'C', log })
    try {
      await enableMic(page, log)
      await submitText(page, '搜索三个不同的主题：气候变化、量子计算、新能源汽车，分别总结要点', log)
      const firstScheduled = await frames.wait(
        frame => ['task.scheduled', 'task.accepted'].includes(frame.type),
        120_000, '第一个任务（慢）被创建',
      )
      await page.waitForTimeout(5_000)
      // 第二个任务：后端任务道串行执行（laneLimit=1），任务会排队等待。
      // 模型偶尔会内联回答而不调用 spawn_thinking，超时后换措辞重试。
      const secondPhrasings = [
        '帮我后台搜索一下今天北京的天气情况，用一句话总结',
        '请把「搜索今天北京的天气并一句话总结」创建成一个后台任务来执行，调用后台任务工具',
      ]
      let secondScheduled = null
      for (const [attempt, phrasing] of secondPhrasings.entries()) {
        await submitText(page, phrasing, log)
        try {
          secondScheduled = await frames.wait(
            frame => (
              ['task.scheduled', 'task.accepted'].includes(frame.type)
              && frame.taskId !== firstScheduled.taskId
            ),
            60_000, `第 ${attempt + 1} 次提交创建出后台任务`,
          )
          break
        } catch {
          log(`第 ${attempt + 1} 次提交未创建任务，换措辞重试`)
          await page.waitForTimeout(2_000)
        }
      }
      if (!secondScheduled) {
        throw new Error('模型多次未把第二个任务交给后台执行')
      }
      const ownTaskIds = new Set([firstScheduled.taskId, secondScheduled.taskId].filter(Boolean))
      log(`两个任务：${[...ownTaskIds].join(' / ')}`)
      // 先到先跑：第一个任务可能在第二个任务创建之前就已完成，因此
      // 从全量帧里收集完成事件，而不是按游标顺序等待。
      await waitUntil(
        () => frames.items.filter(frame => (
          frame.type === 'task.completed' && ownTaskIds.has(frame.taskId)
        )).length >= 2,
        900_000,
        '两个任务都完成',
      )
      const completed = frames.items
        .filter(frame => (
          frame.type === 'task.completed' && ownTaskIds.has(frame.taskId)
        ))
        .sort((left, right) => left.ts - right.ts)
        .slice(0, 2)
        .map(frame => frame.taskId)
      completed.forEach((taskId, index) => (
        log(`任务完成 #${index + 1}：taskId=${taskId}`)
      ))
      // 播报可能在某一个任务完成后立即开始（先到先播），也可能晚于两个
      // 任务都完成之后。先等到属于本次两个任务的播报都开始，再回扫全量帧。
      const taskIdsFor = frame => (
        Array.isArray(frame.taskIds) && frame.taskIds.length
          ? frame.taskIds
          : [frame.taskId]
      ).filter(Boolean)
      const announcementStarted = () => frames.items.filter(frame => (
        frame.type === 'response.started'
        && frame.origin === 'announcement'
        && taskIdsFor(frame).every(taskId => ownTaskIds.has(taskId))
      ))
      await waitUntil(
        () => announcementStarted().length >= 2,
        120_000,
        '两条结果播报都已开始',
      )
      const announcements = announcementStarted().map(started => ({
        started,
        ended: frames.items.find(frame => (
          frame.dir === 'sent'
          && frame.type === 'playback.ended'
          && frame.responseId === started.responseId
        )),
      }))
      if (announcements.length !== 2) {
        throw new Error(`期望 2 条结果播报，实际 ${announcements.length} 条`)
      }
      for (const [index, announcement] of announcements.entries()) {
        if (announcement.ended) continue
        announcement.ended = await frames.wait(
          frame => (
            frame.dir === 'sent'
            && frame.type === 'playback.ended'
            && frame.responseId === announcement.started.responseId
          ),
          180_000, `第 ${index + 1} 条播报播放完毕回执`,
        )
      }
      const flatOrder = announcements.flatMap(announcement => taskIdsFor(announcement.started))
      if (JSON.stringify(flatOrder) !== JSON.stringify(completed)) {
        throw new Error(`播报顺序 ${JSON.stringify(flatOrder)} 与完成顺序 ${JSON.stringify(completed)} 不一致`)
      }
      log(`两条播报顺序 = 完成顺序（${flatOrder.join(' → ')}）`)
      if (announcements[1].started.ts <= announcements[0].ended.ts) {
        throw new Error('第二条播报抢在第一条播放结束之前开始')
      }
      log('两条播报不重叠：前一条播完才播下一条')
    } finally {
      await browser.close()
      rmSync(dir, { recursive: true, force: true })
    }
  },

  // 打断：模型说话期间开口，播放被清、旧回复标「已打断」，新问题照常回答。
  D: async log => {
    const dir = mkdtempSync(join(tmpdir(), 'qwene2e-D-'))
    const wavPath = join(dir, 'D.wav')
    buildTimelineWav({
      filePath: wavPath,
      voice: pickVoice(),
      tailSeconds: 540,
      utterances: [
        { text: '请详细介绍一下中国的四大发明，每一条都详细展开说明', atSec: 1.5 },
        { text: '那明天是星期几，用一句话回答', atSec: 14 },
      ],
    })
    const { browser, page, frames } = await launchPage({ wavPath, sessionSuffix: 'D', log })
    try {
      await enableMic(page, log)
      const firstQuestion = await frames.wait(
        frame => isUserFinal(frame) && String(frame.content || '').includes('四大发明'),
        45_000, '第一问 ASR 转写',
      )
      log(`第一问转写：${firstQuestion.content}`)
      const firstResponse = await frames.wait(
        frame => isModelResponse(frame),
        90_000, '第一问的语音回答',
      )
      log(`第一问回答已开始（responseId=${firstResponse.responseId}）`)
      const secondQuestion = await frames.wait(
        frame => isUserFinal(frame) && String(frame.content || '').includes('星期几'),
        60_000, '打断话语的 ASR 转写',
      )
      log(`打断话语转写：${secondQuestion.content}`)
      // playback.clear 在 speech_started 时下发，可能早于转写 final 到达，
      // 所以从全量帧里找第二次 turn 开始之后的清播帧。
      const secondTurnStarted = [...frames.items].reverse().find(frame => (
        frame.type === 'turn.started' && frame.ts <= secondQuestion.ts
      ))
      await waitUntil(
        () => frames.items.some(frame => (
          frame.type === 'playback.clear'
          && frame.reason === 'user_interruption'
          && (!secondTurnStarted || frame.ts >= secondTurnStarted.ts)
        )),
        10_000,
        '服务端下发 playback.clear（用户打断）',
      )
      log('播放已清（playback.clear/user_interruption）')
      let interrupted = false
      try {
        await waitUntil(
          () => frames.items.some(frame => (
            frame.type === 'response.interrupted'
            && (!secondTurnStarted || frame.ts >= secondTurnStarted.ts)
          )),
          15_000,
          'response.interrupted 标记',
        )
        interrupted = true
        log('服务端已发 response.interrupted')
      } catch {
        log('未收到 response.interrupted（可能打断发生在回答开始前），继续验证新问题')
      }
      await frames.wait(
        frame => isModelResponse(frame),
        90_000, '打断后新问题的回答',
      )
      log('打断后新问题已正常回答')
      await page.waitForSelector('.messages small.interrupted', { timeout: 5_000 })
        .catch(() => {})
      const domInterrupted = await page.locator('.messages small.interrupted').count()
      if (interrupted && domInterrupted === 0) {
        throw new Error('服务端标记了打断，但页面未显示「已打断」')
      }
      if (domInterrupted > 0) log(`页面显示「已打断」×${domInterrupted}`)
      // 新问题若被模型交给后台任务，等它的结果播报完再关闭浏览器，
      // 不给下一场景留未投递的播报（否则会与新场景的第一问相撞）。
      const delegatedTask = frames.items.find(frame => (
        ['task.scheduled', 'task.accepted'].includes(frame.type)
        && frame.turnId === secondQuestion.turnId
      ))
      if (delegatedTask) {
        log(`新问题被交给后台任务 ${delegatedTask.taskId}，等它播报完`)
        await frames.wait(
          frame => frame.type === 'task.completed' && frame.taskId === delegatedTask.taskId,
          300_000, '新问题后台任务完成',
        )
        await frames.wait(
          frame => (
            frame.type === 'response.started'
            && frame.origin === 'announcement'
            && [...(Array.isArray(frame.taskIds) ? frame.taskIds : []), frame.taskId]
              .includes(delegatedTask.taskId)
          ),
          90_000, '新问题后台任务结果播报',
        )
        log('新问题后台任务已播报完毕')
      }
    } finally {
      await browser.close()
      rmSync(dir, { recursive: true, force: true })
    }
  },

  // 任务控制流程 1：先改向第一个任务，再取消第二个任务。
  T1: taskControlScenario({
    suffix: 'T1',
    tasks: [
      { keyword: '新能源', say: '请在后台帮我分别调研北京上海广州深圳四个城市最近一个月的新能源汽车新闻并按城市整理要点' },
      { keyword: '人工智能', say: '再在后台帮我分别查一下美国中国欧洲日本四个地区最新的人工智能新闻并按地区整理要点' },
    ],
    controls: [
      { kind: 'steer', target: '新能源', keyword: '电池', say: '新能源汽车那个任务改一下只要电池技术相关的新闻' },
      { kind: 'cancel', target: '人工智能', keyword: '取消', say: '人工智能新闻那个任务不用做了帮我取消掉' },
    ],
  }),

  // 任务控制流程 2：先取消第一个任务，再改向第二个任务。
  T2: taskControlScenario({
    suffix: 'T2',
    tasks: [
      { keyword: '芯片', say: '请在后台帮我分别整理中国美国韩国日本四个国家最近半年芯片行业的重要进展并按国家列出要点' },
      { keyword: '气候', say: '再在后台帮我分别调研亚洲欧洲非洲美洲四个大洲最近的气候变化新闻并按大洲整理要点' },
    ],
    controls: [
      { kind: 'cancel', target: '芯片', keyword: '取消', say: '芯片那个任务不用做了帮我取消掉' },
      { kind: 'steer', target: '气候', keyword: '极端', say: '气候变化那个任务改一下只关注极端天气' },
    ],
  }),
}

const selected = process.argv.slice(2).filter(arg => arg in SCENARIOS)
const order = selected.length ? selected : Object.keys(SCENARIOS)
const results = {}
for (const name of order) {
  results[name] = await runScenario(name, SCENARIOS[name], message => {
    console.log(`[${new Date().toISOString()}] [${name}] ${message}`)
  })
}
const failed = Object.entries(results).filter(([, ok]) => !ok)
console.log(`\nE2E 结果：${Object.keys(results).map(name => `${name}=${results[name] ? '通过' : '失败'}`).join(' ')}`)
if (failed.length) process.exitCode = 1
