# Qwen-Omni 路线

## 结构

- `gateway/`：基于 qwen-audio-agent Gateway 的前台、Web 页面及任务编排。
- `harness/`：DeepSeek Harness 的 qwen-plus 配置和 ACP 改向适配。上游源码可拉到 `harness/upstream/` 作参考；运行时使用锁定版本的官方 npm 包。
- `latent-proxy/`：Latent Communication 代理（P0）。位于 Harness 与模型服务之间，把后台最终答案的 latent 按任务写入 `history/latents/`，Gateway 只把引用（latentRef）交给支持该能力的前台。默认不启用，详见 [`latent-proxy/README.md`](latent-proxy/README.md)。
- `.env`：本机配置，不提交；空位已预留。`.env.example` 是公开模板。
- `history/`：本机对话与后台任务历史，启动器自动创建，整个目录不提交到 Git。

## 当前交互架构

`gateway/` 提供三条模型路线共用的网页和连接入口。Qwen 路线由 Qwen3.8 Omni Flash Realtime 负责语音、视频及简短对话；需要联网或持续执行的工作交给独立的 DeepSeek Harness ACP 会话，由 qwen-plus 推进。前台可在后台任务运行时继续接收语音；后台结果和必要的阶段更新再回到同一对话。任务卡显示当前步骤和状态。

任务可新建、改向、取消或查询状态。改向应继续同一项任务，而不是另起一项。打断时以浏览器已经播放的音频为准，不能把尚未播出的字幕当作用户已经听到的内容。来源可用于后台核实，但用户没问时前台不主动播报网站或机构名称。MiniCPM 保留原有代码与部署方式；自研路线仅展示开发状态，不假装已有可用模型。

前台默认为 `qwen3.8-omni-flash-realtime`；后台 DeepSeek Harness 使用 DashScope 兼容接口的 `qwen-plus`。Harness 自带 `web_search`，它调用 DeepSeek 原生联网搜索，必须单独配置 `DEEPSEEK_API_KEY`，不能复用 DashScope 密钥。Gateway 的高德工具调用 v3 地理/逆地理编码和 v5 周边 POI 搜索，也分别预留了密钥。

本项目设置 `QWEN_AUDIO_WEB_TOOLS_ENABLED=0`，关闭 Gateway 前台默认的第三方网页搜索回退；联网检索由异步后台任务交给 DeepSeek Harness。这避免前台搜索失败时打断实时对话。若要另行启用前台搜索，可自行配置经验证的搜索供应商后再修改该项。

## 配置和启动

需要 Node.js 24 与 pnpm。首次运行：

```sh
cd 'Qwen-Omni'
pnpm install
cd gateway
pnpm install
pnpm --dir web run build
cd ..
```

仓库已预留本机 `.env` 空位（若另行克隆且没有该文件，可复制 `.env.example`）。在 `.env` 中填入：

- `DASHSCOPE_API_KEY`：Qwen 实时语音和 qwen-plus 共用。
- `QWEN_AUDIO_REALTIME_BASE_URL`：你的百炼业务空间中与 Qwen3.8 对应的专属 WebSocket 服务地址；通用地址不适用。
- `DEEPSEEK_API_KEY`：Harness 原生联网搜索。
- `AMAP_V3_API_KEY`：地址转坐标、坐标转地址、GPS 坐标转换及国内天气直查。
- `AMAP_V5_API_KEY`：附近地点搜索。两项高德密钥必须具有 Web 服务 API 权限；若同一个密钥同时授权 v3/v5，可在两处填写同一值。
- `MINICPM_O_REALTIME_URL`（可选）：已有 MiniCPM-o 4.5 服务的 `/v1/realtime?mode=audio` WebSocket 地址；只有配置此地址后，共用页面才允许切换到 MiniCPM。若服务有 Bearer 认证，再填 `MINICPM_O_AUTH_TOKEN`。本目录不会下载或启动 MiniCPM 权重。

然后执行 `pnpm start`。启动器把 Gateway 与 Harness 的用户数据限制在 Qwen-Omni 目录内，不改写全局配置。后端执行权限保持 Gateway 的 `native` 模式；更改此配置前请评估安全影响。

### 历史记录位置

通过本目录的 `pnpm start` 启动时，后续记录存于 `history/`：

- `history/sessions/`：网页对话和会话事件，每个会话一个 `session.jsonl`；
- `history/tasks.json`：后台任务的状态和结果；
- `history/acp-sessions.json`：Gateway 与 Harness 的后台会话关联；
- `history/deepseek/`：DeepSeek Harness 自己的会话记录和本地运行配置。

`history/` 已在根目录 `.gitignore` 中整体忽略；不应提交其中内容。长期记忆、其他 Gateway 状态和日志仍放在 `.gateway-state/`。清理旧历史时应先停服务，并核对是否存在未完成任务；仅删除对话文件不会同时清掉后台任务与 Harness 会话。

页面 `http://127.0.0.1:3101/` 现在是三条路线的共用入口。Qwen 与已配置的 MiniCPM 共用对话和语音页面，切换前台时会重建该实时连接；具体任务工具能力仍以各模型适配器为准，不能把 MiniCPM 误标成具有 Qwen 的全部工具能力。后台任务由 Gateway 独立管理。页面只有在选中路线真正建立连接后才标“可用”，仅有配置但尚未连接会标“已配置，待连接”。自研路线只显示状态，不假装已有可部署模型。MiniCPM 原目录及其独立部署不受影响。

针对跨地域链路上偶发的音频发送积压，Qwen 前台从根上收紧了上行带宽：麦克风音频先经过能量门控（静音不发送；开口前带 240 ms 预滚、句尾保留 600 ms 尾音，确保服务端 VAD 能正常判定停顿），再把 16 kHz PCM 按 80 ms 批量发送。静音期间只发约 2.5 条/秒的稀疏静音帧维持 VAD 时间轴，上行速率从门控前的约 375 条/秒（约 80 KiB/s）降到约 6 条/秒，慢链路也能排空缓冲，连续提问不再被拥塞丢弃。Gateway 到 DashScope 的 64 KiB 丢弃策略保留为最后兜底；控制消息（session.update）同轮突发会合并成一次发送。其他供应商仍保持原有的失败保护策略。

视频和语音同时开启时，相机以最高 480×270、每 2 秒一帧、每帧最多 24 KiB 发送；浏览器或 DashScope 连接拥堵时只跳过当前视频帧，不排队挤占语音。简单的国内天气查询由前台高德专用工具直接处理；其他需要联网的后台任务使用精简的 Harness ACP 配置，保留搜索而关闭写代码用的终端、文件和子任务工具。任务卡持续展示当前步骤，语音阶段更新有最短间隔并去重。

进度播报和后台结果播报属于 Gateway 主动发起的语音：发出前要求用户已安静 `QWEN_AUDIO_PROACTIVE_USER_FLOOR_MS`（默认 1000 ms，按上行音频能量判断，比服务端 VAD 早约 1 秒）。用户在等待期间开口时，这条播报只会被撤下、稍后重试，不会再出现"播报请求已发出 → 用户开口取消 → DashScope 不回应取消 → 断线重连吞掉用户整句话"的情况。spawn 回执若因用户抢话而未送达，Gateway 会把 task_id 作为上下文补给模型，保证之后仍能改向或取消原任务。

“我在哪里”或“附近有什么”会按需触发浏览器定位权限，只有用户主动提出位置问题才获取 GPS 坐标。浏览器定位不依赖高德 IP 定位；美国等海外地区，高德 Web 服务可能返回空地址或空 POI，不能把空结果当作定位成功。海外逆地理编码和周边搜索的覆盖/权限需向高德单独确认。

## Latent Communication（P0，可选）

设置 `.env` 中的 `LATENT_PROXY_BASE_URL` 并先启动 `latent-proxy` 后，`pnpm start` 会把 Harness 的 qwen-plus 请求改走代理，Gateway 在后台指令前加任务关联标记，任务完成时附上 latentRef，并沿任务快照 → 公告 → 投递传到前台。只有声明 `latentInjection` 能力的前台 provider 才会通过 `buildLatentInjection()` 收到引用；当前的 DashScope 前台不声明该能力，仍按原样收文本。P0 的 latent 由桩抽取器生成，用于打通链路；接收端计划用开源权重 Qwen3-Omni，真实抽取（本地 Qwen3 + vLLM）在 P1 接入。未设置 `LATENT_PROXY_BASE_URL` 时行为与之前完全相同。

## 任务交互

参考 dsh-voice-agent 的三项命令语义，适配到 Gateway 的四个任务工具：

| 意图 | Gateway 工具 | dsh-voice-agent 对应概念 |
| --- | --- | --- |
| 新建异步任务 | `spawn_thinking` | `realtime_delegation` |
| 修改进行中的任务 | `send_task_message` | `send_task_message` |
| 取消任务 | `cancel_agent_task` | `cancel_task` |
| 查询状态 | `get_agent_task_status` | 任务 observation；本项目补成显式工具 |

Gateway 的任务 ID 字段为 `task_id`，只接受服务端返回的真实 ID。后台执行不中断语音对话，状态和计划步骤在任务卡实时显示。打断时浏览器回报已播放音频毫秒数；如果没有可靠的音频—文字对齐，系统不会猜测用户究竟听到了哪几个字，也不会把完整生成字幕当成已听到的事实。

## 端到端测试（控制台）

`gateway/e2e/voice-e2e.mjs` 用 Playwright 驱动本机 Chrome（伪麦克风播放 macOS `say` 合成的中文语音），走 3101 真实页面与完整 Gateway → DashScope → Harness 链路，断言覆盖：

- **A 连续两问**：两问均有 ASR 转写与语音回答（音频拥塞回归）；
- **B 后台任务 + 对话并行**：任务执行期间闲聊照常回答，结果在闲聊播完后播报；
- **C 多任务顺序**：任务按完成顺序逐个播报，前一条播完才播下一条；
- **D 打断**：模型说话时开口 → 播放清空、旧回复标「已打断」、新问题照常回答。
- **T1 / T2 任务控制（全程语音）**：两次开口各建一个后台任务（多任务），再开口改向其中一个（必须改向原任务），最后开口取消另一个；被改向的任务完成并播报，被取消的任务不完成、不播报。两个流程互换改向与取消的对象。设 `E2E_EXPECT_LATENT=1` 时还要求完成的任务带 latentRef（需经 latent-proxy 启动）。

运行前先重建 `web/dist` 并重启 Gateway，关闭 3101 的其他标签页（语音所有权按 owner 串行）：

```sh
cd gateway && node e2e/voice-e2e.mjs        # 全部场景
node e2e/voice-e2e.mjs A B                  # 指定场景
E2E_HEADED=1 E2E_EXPECT_LATENT=1 node e2e/voice-e2e.mjs T1 T2   # 任务控制 + latent 链路
```

T1/T2 由测试控制的麦克风逐句"说话"：会话就绪后才开口，每句都等模型上一句播完。后台任务约 25–35 秒即完成，跑 T1/T2 时让 latent-proxy 暂缓最终答案（仅测试用），留出改向与取消的时间：

```sh
cd latent-proxy && python3 -m latent_proxy --store ../history/latents --final-delay-ms 45000
```

环境变量：`E2E_HEADED=1` 使用可见浏览器窗口；`E2E_TAKEOVER=1` 允许接管被占用的语音。每个场景会真实调用 DashScope 与 DeepSeek Harness；单个场景约 1-5 分钟，C（多任务）在 Harness 繁忙时最长可达 10 分钟。

## 验证与限制

项目包含单元测试和控制台端到端用例，但语音、摄像头、后台任务回报等实际体验还应在使用者自己的浏览器验证。联网检索与高德服务仍取决于相应密钥、服务权限和地域覆盖。ACP `session/steer` 由此项目对固定版 `@deepseek-ai/dsh-acp` 做精确版本补丁；升级该依赖时补丁会拒绝未知构建，需先重新验证适配。

上游 Gateway 以 Apache-2.0 授权，保留原仓库的 `LICENSE`。其源码来自 [QwenAudio/qwen-audio-agent](https://github.com/QwenAudio/qwen-audio-agent)；任务交互参考 [WayneYu430/dsh-voice-agent](https://github.com/WayneYu430/dsh-voice-agent)，Harness 来自 [deepseek-ai/deepseek-harness](https://github.com/deepseek-ai/deepseek-harness)。
