# 运行器

每个运行器把一种"谁决定委派"的做法跑在 `cases.jsonl` 上，输出统一格式的 JSON lines（`id`、`decision`、`objective`、`spoken`、`latency_s`），再用上一级的 `score.py` 评分。

| 运行器 | 组 | 做法 |
| --- | --- | --- |
| `minicpmo_delegate_tool.py` | 1 | MiniCPM-o 4.5 拿到工具定义，自己决定直接回答、调用前台工具，还是调用委派工具 |
| `dump_harness_prompt.mjs` | — | 用 harness（Qwen-Omni Gateway）自己的代码导出前台指令和工具清单 |

`minicpmo_delegate_tool.py` 的 `--prompt`：

| 取值 | 内容 |
| --- | --- |
| `harness`（默认） | harness 的真实前台指令和 11 个工具，委派工具是 `spawn_thinking`；需要 `--harness-json` |
| `rules` | 自拟的单个 `delegate` 工具，说明里写明委派规则 |
| `minimal` | 同上，说明只有一句 |

**harness 不公开**：`dump_harness_prompt.mjs` 的输出含 harness 的私有提示，只能放在私人目录（本地临时目录、服务器 `/local/data/xy2764/harness-private/`，权限 700），不能放进本文件夹，也不能放进共享目录。

```sh
node runners/dump_harness_prompt.mjs <Qwen-Omni/gateway/server/src> <私人目录>/harness_frontend.json
```

评分把模型行为分为：`delegate`（调用委派工具）、`frontend_tool`（调用 harness 提供的其他工具）、`self`（直接回答）、`invented`（调用没提供的工具）、`malformed`（无法解析）。

## 在 multilingual 服务器上跑第 1 组

按实验室规则：硕士用 4–7 号卡；每次先 `nvidia-smi`，用 `CUDA_VISIBLE_DEVICES` 指定卡；代码和数据放 `/local/data/xy2764/`。

1. 本地上传（不传结果）：

   ```sh
   rsync -av --exclude results "/Users/xinzhuoyang/Desktop/Interaction Model/experiments/delegation-bench" xy2764@multilingual.cs.columbia.edu:/local/data/xy2764/experiments/
   ```

2. 服务器上确认空卡（6、7 号应空闲，0% 利用率也要看显存和进程）：

   ```sh
   nvidia-smi
   ```

3. 环境和权重：复用 Matthew 的环境 `/local/data/ms7280/envs/minicpmo`（transformers 4.51.0）和权重 `/local/data/ms7280/models/MiniCPM-o-4_5`，**先征得他同意**，不要另外下载 19 GB 的副本。

4. 冒烟测试，10 条：

   ```sh
   cd /local/data/xy2764/experiments/delegation-bench
   source /local/data/ms7280/envs/minicpmo/bin/activate
   CUDA_VISIBLE_DEVICES=6,7 python runners/minicpmo_delegate_tool.py --gpus 0,1 --limit 10
   ```

5. 全量 200 条，加打字输入的对照组（用 nohup，断线不中断；可从断点续跑）：

   ```sh
   mkdir -p results
   CUDA_VISIBLE_DEVICES=6,7 nohup python runners/minicpmo_delegate_tool.py --gpus 0,1 > results/run-audio.log 2>&1 &
   CUDA_VISIBLE_DEVICES=6,7 nohup python runners/minicpmo_delegate_tool.py --gpus 0,1 --input text > results/run-text.log 2>&1 &
   ```

   两条命令共用 6、7 号卡，显存不够就跑完一条再跑另一条。

6. 评分：

   ```sh
   python3 score.py results/minicpmo45-bf16-delegate-tool-rules-text.jsonl
   python3 score.py results/minicpmo45-bf16-delegate-tool-rules-text-textin.jsonl
   ```

7. 跑完释放显存（进程结束即可），把 `results/` 拿回本地：

   ```sh
   rsync -av xy2764@multilingual.cs.columbia.edu:/local/data/xy2764/experiments/delegation-bench/results "/Users/xinzhuoyang/Desktop/Interaction Model/experiments/delegation-bench/"
   ```

## 选项

| 参数 | 说明 |
| --- | --- |
| `--tool-desc rules` / `minimal` | 工具说明写明委派规则（默认），或只说"后台有工具、实时信息、能做长任务" |
| `--input text` | 对照组：输入打字的原文而不是音频 |
| `--mode speech` | 用口语回复模板（实际语音使用时的模板），默认是文本模板 |
| `--precision awq --gpus 0` | 4-bit 权重，单卡；路径用 `--model` 指定 |
| `--ids s01 d01` / `--limit N` | 只跑部分用例 |
