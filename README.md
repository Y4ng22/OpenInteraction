# OpenInteraction

本仓库按模型路线分成三个独立目录：

| 目录 | 状态 | 说明 |
| --- | --- | --- |
| [`MiniCPM/`](MiniCPM/) | 保留原样 | 原有 MiniCPM-o 实现、脚本和测试整体移入；不在本仓库下载模型或权重。 |
| [`Qwen-Omni/`](Qwen-Omni/) | 开发与验证中 | Qwen3.8 Omni Flash Realtime 前台、qwen-plus 后台、qwen-audio-agent Gateway 和 DeepSeek Harness。 |
| [`Self-Developed/`](Self-Developed/) | 原型保留 | 原仓库已有的 InteractFormer S1/S2、Bridge 与调度代码已原样复制；模型接口尚未确定，不新增适配。 |

MiniCPM 的原使用说明位于 [`MiniCPM/README.md`](MiniCPM/README.md)。Qwen 路线的配置、启动和已知限制见 [`Qwen-Omni/README.md`](Qwen-Omni/README.md)。自研原型说明见 [`Self-Developed/README.md`](Self-Developed/README.md)。MiniCPM 中的 InteractFormer 文件保留原状以维护已有运行路径；自研目录是其未经修改的源码副本。运行 MiniCPM 脚本时，请先切换到 `MiniCPM/`，以保留原有相对路径行为。

Qwen 路线沿用 qwen-audio-agent 的 Gateway：Qwen3.8 Omni Flash Realtime 负责实时语音、视频和前台交互；DeepSeek Harness 通过 ACP 驱动 qwen-plus 处理异步任务与联网检索。任务控制包含新建、改向、取消、状态查询四项操作，进行中仍可继续语音对话。任务卡展示后台步骤及当前节点。用户打断时，以浏览器实际播放的音频进度为依据，未播放的生成文字不会被当作用户已听到的内容。未要求来源时，前台只回答结果，不主动播报出处。

## 共用交互页面

启动 `Qwen-Omni/` 的 Gateway 后，打开 `http://127.0.0.1:3101/`。这是三条路线共用的页面，而非三个互不相干的入口。页面上方可选择 Qwen-Omni 或已配置的 MiniCPM 服务，并明确区分“可用”（已建立实时连接）、“已配置，待连接”、“连接异常”和“未配置服务”。自研模型也展示在同一处，但因模型接口及权重尚未确定，标为“开发中，暂不可用”，不能误选。MiniCPM 原有代码和部署路径保持不变；若要在共用页面使用，需把其现有 Realtime 服务地址配置为 `Qwen-Omni/.env` 中的 `MINICPM_O_REALTIME_URL`，不在本地下载权重。

上游： [qwen-audio-agent](https://github.com/QwenAudio/qwen-audio-agent)、[dsh-voice-agent](https://github.com/WayneYu430/dsh-voice-agent)、[deepseek-harness](https://github.com/deepseek-ai/deepseek-harness)。各自的许可证和来源说明保留在相应源码中。

## 本地记录

在 `Qwen-Omni/` 中执行 `pnpm start` 时，新对话、任务状态和 DeepSeek Harness 会话统一保存在 `Qwen-Omni/history/`。该目录已加入 Git 忽略规则，不会随仓库提交；具体文件和清理范围见 [`Qwen-Omni/README.md`](Qwen-Omni/README.md)。配置、日志及长期记忆仍在 `Qwen-Omni/.gateway-state/`，MiniCPM 和自研路线的原目录不受影响。
