// 端到端语音交互测试：驱动真实 Chrome（伪麦克风播放本地合成语音）打开
// http://127.0.0.1:3101/ 控制台，走完整 Gateway → DashScope Realtime 链路，
// 断言 ASR 转写、语音回复、后台任务播报顺序与打断行为。
//
// 用法：node e2e/voice-e2e.mjs [A|B|C|D ...]（缺省全部）
// 环境变量：E2E_HEADED=1 用可见窗口；E2E_TAKEOVER=1 允许接管被占用的语音。
//
// 运行前提：
//   - gateway 已重启并加载最新代码（web/dist 已重建）
//   - 用户在 3101 的 Chrome 标签页已关闭（voice ownership 按 owner 串行）
//   - dsh（DeepSeek Harness）在运行（场景 B/C）
import { chromium } from 'playwright'
import { mkdtempSync, rmSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { buildTimelineWav, pickVoice } from './wav-timeline.mjs'

const BASE_URL = 'http://127.0.0.1:3101/'
const VOICE_BUTTON = 'button.voice'
const HEADED = process.env.E2E_HEADED === '1'
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
      if (task.turnId) frame.turnId = frame.turnId || task.turnId
    }
    return frame
  } catch {
    return null
  }
}

async function launchPage({ wavPath = null, sessionSuffix = '', log = null } = {}) {
  const args = [
    '--use-fake-ui-for-media-stream',
    '--use-fake-device-for-media-stream',
    '--autoplay-policy=no-user-gesture-required',
  ]
  if (wavPath) args.push(`--use-file-for-fake-audio-capture=${wavPath}`)
  const browser = await chromium.launch({ channel: 'chrome', headless: !HEADED, args })
  const context = await browser.newContext()
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
