"""Let the fine-tuned frontend hear SpeechFCEval BFCL-Single and call its own tool or write an objective for the backend.

The text baseline for the latent handoff: what Qwen3-14B gets when the only thing crossing is the frontend's own
spawn_thinking objective. Each clip goes through the real full-duplex loop as in duplex_test.py (one-second units,
greedy, no nudge). Tools are split as in training: fast tools (label_time.py, 0.5 s) are the frontend's, slow tools
exist only in the backend, and items whose correct tool is unsure are left out. The frontend holds the item's fast
tools plus fast held-out decoys, 2 to 9 in all as a training sample has. Its right move on a fast item is to call the
tool and on a slow item to hand off. Whether it does is part of the result. --all-backend measures the handoff itself:
every item's tools stay in the backend whatever their speed, unsure ones included, and the frontend holds only decoys.

The dev split is Matthew's (latent-kv-transfer common.dev_split): every index, seed 0, 100 sampled. --out .jsonl has
everything; --out .transcripts.jsonl has {index, transcript} in his transcript layout, with an empty transcript for
items that were not handed off.

    python objective_bfcl.py --src <snapshot> --tools tools_split.jsonl --tool-time tool_time_reviewed.jsonl \
        --bfcl-tools bfcl_tools.jsonl --bfcl-time bfcl_time.jsonl --lora runs/ft_short \
        --allow ../duplex_full/allowed_banned_ids_v4.json --data <SpeechFCEval>/SpeechFC_BFCL_Single --out ../latent/objective_dev
"""
import argparse
import collections
import json
import random
from pathlib import Path

import librosa
import numpy as np
import torch

from build_sequences import SR, system_prompt
from duplex_test import first_turn, intercept, merge_lora, prepare

DEV_SIZE, SPLIT_SEED = 100, 0


def dev_split(items):
    rng = random.Random(SPLIT_SEED)
    chosen = set(rng.sample([it["index"] for it in items], DEV_SIZE))
    return [it for it in items if it["index"] in chosen], [it for it in items if it["index"] not in chosen]


def speeds(path):
    return {r["id"]: r["speed"] for r in map(json.loads, path.read_text(encoding="utf-8").splitlines())}


def gold_names(item):
    gold = item["ground_truth"]
    return [next(iter(c)) for c in (json.loads(gold) if isinstance(gold, str) else gold)]


def frontend_tools(item, speed, pool, all_backend):
    """The item's fast tools plus fast decoys, shuffled, 2 to 9 in all."""
    own = [n for n in (t.get("function", t)["name"] for t in json.loads(item["function"])) if speed[n] == "fast" and not all_backend]
    rng = random.Random(f"decoy-{item['index']}")
    listed = own + rng.sample(pool, max(0, rng.randint(2, 9) - len(own)))
    rng.shuffle(listed)
    return listed


def objective_of(calls):
    """The objective of the first well-formed spawn_thinking, and the names of every call."""
    objective, names = "", []
    for c in calls:
        try:
            obj = json.loads(c.strip())
            names.append(obj["name"])
            if obj["name"] == "spawn_thinking" and not objective:
                objective = obj["arguments"]["objective"]
        except (ValueError, KeyError, TypeError):
            names.append("[malformed]")
    return objective, names


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", type=Path, required=True)
    ap.add_argument("--tools", type=Path, required=True)
    ap.add_argument("--tool-time", type=Path, required=True)
    ap.add_argument("--bfcl-tools", type=Path, required=True)
    ap.add_argument("--bfcl-time", type=Path, required=True)
    ap.add_argument("--lora", type=Path, required=True)
    ap.add_argument("--allow", required=True, help="JSON file of banned token ids the training targets use")
    ap.add_argument("--data", type=Path, required=True)
    ap.add_argument("--split", choices=["dev", "test", "all"], default="dev")
    ap.add_argument("--silence-s", type=int, default=30)
    ap.add_argument("--max-mem-gib", type=int, default=14)
    ap.add_argument("--limit", type=int)
    ap.add_argument("--all-backend", action="store_true")
    ap.add_argument("--shard", default="0/1", help="i/n: run every n-th item starting at i, for replicas on separate cards")
    ap.add_argument("--out", type=Path, required=True)
    args = ap.parse_args()

    from transformers import AutoModel, AutoTokenizer
    n_gpu = torch.cuda.device_count()
    model = AutoModel.from_pretrained(args.src, trust_remote_code=True, attn_implementation="sdpa", torch_dtype=torch.bfloat16,
                                      init_vision=False, init_audio=True, init_tts=True, device_map="auto",
                                      max_memory={g: f"{args.max_mem_gib}GiB" for g in range(n_gpu)}).eval()
    tok = AutoTokenizer.from_pretrained(args.src, trust_remote_code=True)
    merge_lora(model.llm, args.lora)
    model.init_tts = lambda *a, **k: None
    duplex = model.as_duplex(generate_audio=False)
    allow = set(json.loads(Path(args.allow).read_text()))
    duplex.decoder.forbidden_token_ids[:] = [i for i in duplex.decoder.forbidden_token_ids if i not in allow]

    tools = {t["id"]: t for t in map(json.loads, args.tools.read_text(encoding="utf-8").splitlines())}
    known = speeds(args.tool_time)
    pool = sorted(i for i, t in tools.items() if t["split"] == "heldout" and known[i] == "fast")
    tools.update({t["id"]: t for t in map(json.loads, args.bfcl_tools.read_text(encoding="utf-8").splitlines())})
    speed = speeds(args.bfcl_time)
    items = [json.loads(l) for l in (args.data / "metadata.jsonl").read_text(encoding="utf-8").splitlines() if l.strip()]
    gold_speed = {it["index"]: {speed[n] for n in gold_names(it)} for it in items}
    if args.split != "all":
        items = dict(zip(("dev", "test"), dev_split(items)))[args.split]
    items = [it for it in items if args.all_backend or "unsure" not in gold_speed[it["index"]]]
    shard, shards = map(int, args.shard.split("/"))
    items = items[shard::shards][:args.limit]

    args.out.parent.mkdir(parents=True, exist_ok=True)
    rows_file = args.out.with_suffix(".jsonl")
    rows = [json.loads(l) for l in rows_file.read_text(encoding="utf-8").splitlines()] if rows_file.exists() else []
    done = {r["index"] for r in rows}
    with rows_file.open("a", encoding="utf-8") as f:
        for item in items:
            if item["index"] in done:
                continue
            fast = frontend_tools(item, speed, pool, args.all_backend)
            torch.manual_seed(0)
            prepare(duplex, system_prompt({"frontend_tools": fast}, tools))
            speech, _ = librosa.load(args.data / item["file_name"], sr=SR, mono=True)
            n_speech = int(np.ceil(len(speech) / SR))
            speech = np.pad(speech, (0, n_speech * SR - len(speech))).astype(np.float32)
            chunks = [speech[i * SR:(i + 1) * SR] for i in range(n_speech)] + [np.zeros(SR, np.float32)] * args.silence_s
            units, ended_at = [], None
            for t, chunk in enumerate(chunks):
                if not duplex.streaming_prefill(audio_waveform=chunk)["success"]:
                    continue
                before = len(duplex.res_ids)
                got = duplex.streaming_generate(decode_mode="greedy")
                raw = tok.decode(duplex.res_ids[before:], skip_special_tokens=False)
                units.append({"t": t, "listen": got["is_listen"], "raw": raw})
                if "<|turn_eos|>" in raw and ended_at is None:
                    ended_at = t
                if ended_at is not None and t > ended_at:
                    break
            spoken, calls = intercept(first_turn(units))
            objective, names = objective_of(calls)
            spoke = [u["t"] for u in units if u["raw"].strip()]
            row = {"index": item["index"], "utterance": item["utterance"], "speech_s": n_speech,
                   "gold_speed": next(iter(gold_speed[item["index"]])), "gold_tools": gold_names(item),
                   "frontend_tools": [tools[i]["name"] for i in fast], "spoken": spoken, "calls": names, "raw_calls": calls,
                   "objective": objective, "first_output_t": spoke[0] if spoke else None,
                   "spoke_early": bool(spoke) and spoke[0] < n_speech - 1, "units": units}
            rows.append(row)
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
            f.flush()
            print(item["index"], names, "|", spoken[:60], "|", objective[:120], flush=True)

    args.out.with_suffix(".transcripts.jsonl").write_text(
        "".join(json.dumps({"index": r["index"], "transcript": r["objective"]}, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8")
    kinds = collections.Counter("handoff" if r["objective"] else ",".join(r["calls"]) or ("spoke" if r["spoken"].strip() else "silent") for r in rows)
    print(f"{len(rows)} items:", kinds.most_common(), "spoke early:", sum(r["spoke_early"] for r in rows))


if __name__ == "__main__":
    main()
