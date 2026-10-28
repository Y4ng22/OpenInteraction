# latent-proxy（Latent Communication · P0）

把后台模型的 latent 交给前台，Gateway 只负责传引用。本目录是 DeepSeek Harness 与模型服务之间的 OpenAI 兼容代理：

```
Gateway ──ACP(指令前加关联标记)──▶ DeepSeek Harness ──OpenAI API──▶ latent-proxy ──▶ 模型服务（P0：DashScope qwen-plus）
   ▲                                      │                         │ 最终答案 → 抽取 latent
   └── task.completed { latentRef } ◀─────┘                         ▼
                                                           history/latents/<task_id>/<seq>.{bin,json}
```

- **P0（当前）**：`StubExtractor` 根据答案文本生成确定性的伪 latent，不需要模型权重，用来打通全链路。
- **P1**：换成本地 Qwen3（vLLM）+ 真实 hidden state 抽取，只需新增一个 extractor。
- **前台**：只有声明了 `latentInjection` 能力的 provider（计划中的开源 Qwen3-Omni）才会收到 latentRef；DashScope 前台照旧收文本。

## 目录

| 路径 | 作用 |
| --- | --- |
| `latent_proxy/server.py` | HTTP 代理：剥离标记、转发、在最终答案处写 latent |
| `latent_proxy/markers.py` | `<latent_task id="…"/>` 关联标记的解析与剥离 |
| `latent_proxy/store.py` | 目录 store 写入（原子写入：先 payload、后 manifest） |
| `latent_proxy/extractors.py` | latent 抽取器；P0 只有 `StubExtractor` |
| `tests/` | 离线测试（假上游，不调用任何 API） |

Gateway 侧对应代码在 `gateway/server/src/latent/`：`latent-ref.mjs`（契约）、`latent-store.mjs`（只读 store）、`latent-bridge.mjs`（加标记、附 latentRef）。

## 契约

**关联标记**：Gateway 在后台指令最前面加一行 `<latent_task id="task_12"/>`。proxy 取对话里**最后一个**标记作为任务 ID（改向消息不带标记，所以仍归原任务），并在转发前把所有标记删掉，模型看不到它。任务 ID 只能用 `[A-Za-z0-9_.-]`，不超过 128 个字符，且不能以 `.` 开头。

**何时写 latent**：一轮回复以 `finish_reason=stop` 结束、不含 tool call、正文非空，才算最终答案。latent 会在最后一个字节返回给 Harness **之前**写好，所以任务一完成，Gateway 就能读到。工具调用轮不写。

**store 目录结构**（`<root>/<task_id>/`）：

- `<seq>.bin`：行优先的 `[tokens, dim]` 张量，小端序；
- `<seq>.json`：`{version:1, task_id, seq, final, model, layer, tokens, dim, dtype, payload:"<seq>.bin", byte_length, created_at_ms}`。

**LatentRef**（Gateway 传给前台的内容）：`{version, taskId, seq, key:"<taskId>/<seq>", final, model, layer, tokens, dim, dtype, createdAtMs}`。前台模型服务用 `key` 到同一个 store 里读取张量。

## 使用

```sh
cd Qwen-Omni/latent-proxy
python3 -m latent_proxy --store ../history/latents          # 默认监听 127.0.0.1:8765，上游为 DashScope 兼容接口
```

然后在 `Qwen-Omni/.env` 里设置 `LATENT_PROXY_BASE_URL=http://127.0.0.1:8765/v1`，再执行 `pnpm start`。启动器会把 Harness 的 qwen-plus 路由改到 proxy，并为 Gateway 打开 `QWEN_AUDIO_LATENT_ENABLED`，store 目录默认为 `history/latents`（可用 `QWEN_AUDIO_LATENT_STORE_DIR` 修改，两边必须一致）。不设 `LATENT_PROXY_BASE_URL` 时，一切保持原样。

只需要 Python 3.9+ 标准库，不需要安装依赖。

`--final-delay-ms N`（仅测试用，默认 0）：标记了任务的最终答案在返回前暂缓 N 毫秒，让语音端到端测试有时间改向或取消后台任务。工具调用轮和未标记的请求不受影响。

## 测试

```sh
cd Qwen-Omni/latent-proxy && python3 -m unittest discover -s tests -t .
```

Gateway 侧的离线测试在 `gateway/server/test/latent-communication.test.mjs` 和 `latent-delivery.test.mjs`。经过页面的真实语音测试见 `gateway/e2e/voice-e2e.mjs` 中的场景 `T1`、`T2`（设置 `E2E_EXPECT_LATENT=1` 时，会检查被改向的任务在 store 中有最终 latent，且 Gateway 任务记录里的 latentRef 指向它）。
