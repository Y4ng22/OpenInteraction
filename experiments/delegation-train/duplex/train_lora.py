"""QLoRA smoke training of MiniCPM-o 4.5's language model on the duplex sequences from build_sequences.py.

The language model is the plain Qwen3 checkpoint from baseline/extract_llm.py, loaded in 4-bit on one 16 GB card,
or with --bf16 unquantized across the visible cards.
Each sequence's token ids are embedded and its audio placeholders replaced by the stored audio embeddings, so the
model sees exactly the duplex input; loss is on the labeled tokens only. The adapter uses MiniCPM-o's module names
under `llm.`, so it can be merged into the full model for the duplex test (merge_lora.py).

    python train_lora.py --llm /local/data/xy2764/models/minicpmo45-llm --seqs seqs --out lora_smoke
"""
import argparse
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


@torch.no_grad()
def eval_loss(model, samples, device):
    model.eval()
    tot, n = 0.0, 0
    for s in samples:
        x, y = embed(model, s, device)
        out = model(inputs_embeds=x, labels=y)
        k = int((y[:, 1:] != -100).sum())
        tot, n = tot + out.loss.item() * k, n + k
    model.train()
    return tot / max(n, 1)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--llm", type=Path, required=True)
    ap.add_argument("--seqs", type=Path, required=True)
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

    index = [json.loads(l) for l in (args.seqs / "seq_index.jsonl").read_text().splitlines()]
    load = lambda r: torch.load(args.seqs / f"{r['id']}.pt")
    train = [load(r) for r in index if r["split"] == "train"]
    test = [load(r) for r in index if r["split"] == "test"]

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
    log = {"test_loss_before": eval_loss(model, test, device), "steps": []}
    print(f"test loss before: {log['test_loss_before']:.4f}", flush=True)

    model.train()
    step, t0, run = 0, time.time(), []
    for epoch in range(args.epochs):
        order = list(range(len(train)))
        rng.shuffle(order)
        for i, j in enumerate(order):
            x, y = embed(model, train[j], device)
            loss = model(inputs_embeds=x, labels=y).loss
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
    args.out.mkdir(parents=True, exist_ok=True)
    model.save_pretrained(args.out)  # before the final eval, which can run out of memory on one card
    log["test_loss_after"] = eval_loss(model, test, device)
    print(f"test loss after: {log['test_loss_after']:.4f}", flush=True)
    (args.out / "train_log.json").write_text(json.dumps(log, indent=1))


if __name__ == "__main__":
    main()
