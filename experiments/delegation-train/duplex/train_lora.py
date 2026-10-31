"""LoRA training of MiniCPM-o 4.5's language model on the duplex sequences from build_sequences.py.

The language model is the plain Qwen3 checkpoint from baseline/extract_llm.py, loaded in 4-bit on one 16 GB card,
or with --bf16 unquantized across the visible cards.
Each sequence's token ids are embedded and its audio placeholders replaced by the stored audio embeddings, so the
model sees exactly the duplex input; loss is on the labeled tokens only. The adapter uses MiniCPM-o's module names
under `llm.`, so it can be merged into the full model for the duplex test (merge_lora.py).

The full run mixes the delegation sequences (with their two-request pairs) and the kept self-distillation
conversations (self_distill.py, filter_selfdistill.py). --fraction trains on the same stratified share of every target,
self-distillation included, for the learning curve. Sequences are read from disk at each step, not held in memory.
Eval loss is on a stratified sample of --eval-split, reported per target.

    python train_lora.py --llm /local/data/xy2764/models/minicpmo45-llm --seqs seqs --out lora_smoke
    python train_lora.py --llm ... --bf16 --seqs duplex_full/seqs_v4 --selfdistill duplex_sd/seqs \
        --sd-keep duplex_sd/keep.jsonl --fraction 0.125 --epochs 1 --out runs/lc_0125
"""
import argparse
import collections
import json
import math
import random
import time
from pathlib import Path

import torch
from peft import LoraConfig, get_peft_model, prepare_model_for_kbit_training
from transformers import AutoModelForCausalLM, BitsAndBytesConfig, get_cosine_schedule_with_warmup


def embed(model, sample, device):
    ids = torch.tensor(sample["ids"], device=device)
    x = model.get_input_embeddings()(ids)
    x = x.clone()
    for at, a in zip(sample["audio_at"], sample["audio"]):
        x[at:at + a.shape[0]] = a.to(device, x.dtype)
    return x.unsqueeze(0), torch.tensor(sample["labels"], device=device).unsqueeze(0)


def token_loss(model, x, y):
    """Mean loss on the labeled tokens. Logits are computed only there: over a whole 4,000-token sequence they need
    about 2 GB on the last card and run it out of memory."""
    lm = model.get_base_model()
    hidden = lm.model(inputs_embeds=x).last_hidden_state[:, :-1]
    target = y[:, 1:]
    keep = target != -100
    logits = lm.lm_head(hidden[keep.to(hidden.device)])
    return torch.nn.functional.cross_entropy(logits.float(), target[keep].to(logits.device))


@torch.no_grad()
def eval_loss(model, rows, device):
    """Token-weighted loss over the rows, overall ("all") and per target."""
    model.eval()
    tot, n = collections.Counter(), collections.Counter()
    for r in rows:
        x, y = embed(model, torch.load(r["path"]), device)
        loss = token_loss(model, x, y).item()
        k = int((y[:, 1:] != -100).sum())
        for key in ("all", r["target"]):
            tot[key] += loss * k
            n[key] += k
    model.train()
    return {key: tot[key] / n[key] for key in n}


def stratified(rows, fraction, rng):
    """The same share of every target, at least one each."""
    by = collections.defaultdict(list)
    for r in rows:
        by[r["target"]].append(r)
    out = []
    for target in sorted(by):
        group = sorted(by[target], key=lambda r: r["id"])
        rng.shuffle(group)
        out += group[:max(1, round(len(group) * fraction))]
    return out


def load_rows(args):
    rows = []
    for d in args.seqs:
        for l in (d / "seq_index.jsonl").read_text().splitlines():
            r = json.loads(l)
            rows.append({"id": r["id"], "split": r["split"], "target": r["target"], "tokens": r["tokens"], "path": d / f"{r['id']}.pt"})
    if args.selfdistill:
        for l in args.sd_keep.read_text().splitlines():
            r = json.loads(l)
            if r["keep"]:
                rows.append({"id": r["id"], "split": "train", "target": "selfdistill", "tokens": r["tokens"],
                             "path": args.selfdistill / f"{r['id']}.pt"})
    return rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--llm", type=Path, required=True)
    ap.add_argument("--seqs", type=Path, nargs="+", required=True, help="directories with seq_index.jsonl")
    ap.add_argument("--selfdistill", type=Path, help="self_distill.py sequences, mixed into train")
    ap.add_argument("--sd-keep", type=Path, help="filter_selfdistill.py output; only kept conversations are used")
    ap.add_argument("--fraction", type=float, default=1.0, help="stratified share of the train rows (learning curve)")
    ap.add_argument("--oversample", nargs="*", default=[], help="target=N: use that target's train rows N times (split=2)")
    ap.add_argument("--eval-split", default="test")
    ap.add_argument("--eval-n", type=int, default=0, help="stratified sample of the eval split (0: all of it)")
    ap.add_argument("--save-every", type=int, default=0, help="also save the adapter every this many steps")
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--epochs", type=int, default=3)
    ap.add_argument("--accum", type=int, default=4)
    ap.add_argument("--lr", type=float, default=2e-4)
    ap.add_argument("--rank", type=int, default=16)
    ap.add_argument("--seed", type=int, default=20261029)
    ap.add_argument("--bf16", action="store_true", help="plain bf16 LoRA across the visible GPUs instead of 4-bit QLoRA")
    args = ap.parse_args()
    torch.manual_seed(args.seed)
    rng = random.Random(args.seed)
    device = "cuda"

    rows = load_rows(args)
    train = [r for r in rows if r["split"] == "train"]
    if args.fraction < 1:
        train = stratified(train, args.fraction, random.Random(args.seed))
    for spec in args.oversample:
        target, times = spec.split("=")
        train += [r for r in train if r["target"] == target] * (int(times) - 1)
    test = [r for r in rows if r["split"] == args.eval_split]
    if args.eval_n:
        test = stratified(test, args.eval_n / len(test), random.Random(args.seed))
    print("train", dict(collections.Counter(r["target"] for r in train)), "eval", dict(collections.Counter(r["target"] for r in test)),
          flush=True)
    args.out.mkdir(parents=True, exist_ok=True)

    if args.bf16:  # plain LoRA, the model split across the visible cards
        model = AutoModelForCausalLM.from_pretrained(
            args.llm, torch_dtype=torch.bfloat16, attn_implementation="sdpa", device_map="auto",
            max_memory={g: "13GiB" for g in range(torch.cuda.device_count())})
        model.gradient_checkpointing_enable(gradient_checkpointing_kwargs={"use_reentrant": False})
        model.enable_input_require_grads()
    else:  # QLoRA on one card
        model = AutoModelForCausalLM.from_pretrained(
            args.llm, torch_dtype=torch.bfloat16, attn_implementation="sdpa", device_map={"": 0},
            quantization_config=BitsAndBytesConfig(load_in_4bit=True, bnb_4bit_quant_type="nf4",
                                                   bnb_4bit_compute_dtype=torch.bfloat16, bnb_4bit_use_double_quant=True))
        model = prepare_model_for_kbit_training(model, use_gradient_checkpointing=True)
    model = get_peft_model(model, LoraConfig(r=args.rank, lora_alpha=2 * args.rank, lora_dropout=0.05, task_type="CAUSAL_LM",
                                             target_modules=["q_proj", "k_proj", "v_proj", "o_proj", "gate_proj", "up_proj", "down_proj"]))
    model.print_trainable_parameters()

    steps = math.ceil(len(train) * args.epochs / args.accum)
    opt = torch.optim.AdamW([p for p in model.parameters() if p.requires_grad], lr=args.lr, weight_decay=0.0)
    sched = get_cosine_schedule_with_warmup(opt, max(1, steps // 10), steps)
    log = {"args": {k: str(v) for k, v in vars(args).items()}, "train": len(train),
           "test_loss_before": eval_loss(model, test, device), "steps": []}
    print("test loss before:", json.dumps(log["test_loss_before"]), flush=True)

    model.train()
    x, y = embed(model, torch.load(max(train, key=lambda r: r["tokens"])["path"]), device)
    token_loss(model, x, y).backward()  # fail now, not hours in, if the largest sequence does not fit
    opt.zero_grad(set_to_none=True)
    print(f"largest sequence fits: {x.shape[1]} tokens, {torch.cuda.max_memory_allocated() / 2**30:.1f} GiB", flush=True)
    step, t0, run = 0, time.time(), []
    for epoch in range(args.epochs):
        order = list(range(len(train)))
        rng.shuffle(order)
        for i, j in enumerate(order):
            x, y = embed(model, torch.load(train[j]["path"]), device)
            loss = token_loss(model, x, y)
            (loss / args.accum).backward()
            run.append(loss.item())
            if (i + 1) % args.accum == 0 or i == len(order) - 1:
                torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0)
                opt.step(); sched.step(); opt.zero_grad(set_to_none=True)
                step += 1
                if step % 10 == 0 or step == steps:
                    mean = sum(run) / len(run)
                    log["steps"].append({"step": step, "epoch": epoch, "train_loss": mean})
                    print(f"step {step}/{steps} epoch {epoch} loss {mean:.4f} {time.time() - t0:.0f}s "
                          f"{torch.cuda.max_memory_allocated() / 2**30:.1f} GiB", flush=True)
                    run = []
                if args.save_every and step % args.save_every == 0:
                    model.save_pretrained(args.out / f"step_{step}")
    model.save_pretrained(args.out)  # before the final eval, which can run out of memory on one card
    log["test_loss_after"] = eval_loss(model, test, device)
    print("test loss after:", json.dumps(log["test_loss_after"]), flush=True)
    (args.out / "train_log.json").write_text(json.dumps(log, indent=1))


if __name__ == "__main__":
    main()
