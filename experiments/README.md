# Experiments

One folder per experiment: its code, a README with the method and results, and small result reports. Raw model
outputs, audio and private prompts are not committed (each folder's `.gitignore`). Runs happen on the lab server
(`multilingual`, under `/local/data/xy2764/`).

| Folder | Question | Status |
| --- | --- | --- |
| [`fdb-chain/`](fdb-chain/README.md) | End to end on FDB-v3: how many requests does a voice frontend plus a Qwen3-14B backend complete, and how should the work be split? | Done (2026-10-22 to 10-27) |
| [`delegation-bench/`](delegation-bench/RESULTS.md) | On 200 spoken cases, does the voice model hand off what it should and answer the rest itself? | Done for MiniCPM-o 4.5 and Fun-Audio-Chat-8B |
| [`delegation-train/`](delegation-train/README.md) | Can the voice model be trained to make the handoff decision (fast tools itself, the rest handed to the backend)? | Building the data |

The private harness export (`--harness-json`) used by `fdb-chain` and `delegation-bench` is made with
`delegation-bench/runners/dump_harness_prompt.mjs` and must stay outside the repository.
