"""Keep the self-distillation conversations where the untrained model behaved well (decided 2026-10-30).

self_distill.py records whatever the untrained model did. Three things are dropped, by whole conversation:
- the model never spoke;
- it called itself Qwen or said Alibaba made it (MiniCPM-o's language model is a Qwen3; the identity leaks);
- after the user cut in, it went on speaking for 3 seconds or more (the user's first three seconds all have speech).
Starting to answer before the user has finished is kept: the model decides every second whether it has heard
enough, and that is how it is meant to work.

The conversation's timeline is rebuilt with the same rules and random draws as self_distill.py run, so each unit is
known to be the user talking, the model's reply, or the quiet second after it.

    python filter_selfdistill.py --seqs duplex_sd/seqs --scripts duplex_sd/scripts.jsonl \
        --manifest duplex_sd/manifest_*.jsonl --src <snapshot> --out duplex_sd/keep.jsonl
"""
import argparse
import collections
import json
import math
import random
import re
from pathlib import Path

import torch

IDENTITY = re.compile(r"\b(qwen|alibaba|tongyi)\b", re.I)


def units_of(d, unit, unit_end):
    ids, at = d["ids"], dict(zip(d["audio_at"], [a.shape[0] for a in d["audio"]]))
    out, i = [], 0
    while i < len(ids):
        if ids[i] == unit:
            j = i + 1 + at.get(i + 1, 0)
            k = ids.index(unit_end, j)
            out.append(ids[j:k])
            i = k + 1
        else:
            i += 1
    return out


def cut_in_overruns(sid, timing, wavs, units, speak, turn_eos, max_wait=12):
    """How many times the model kept speaking through the first 3 s of a user turn that cut into its reply."""
    rng = random.Random(sid)
    rng.randrange(2**31)  # the torch seed draw in self_distill.py
    idx, cut_open, overruns = 0, False, 0
    for k, w in enumerate(wavs):
        n = math.ceil(w["seconds"] - 1e-9)
        user = units[idx:idx + n]
        idx += n
        if cut_open and len(user) >= 3 and all(speak in u for u in user[:3]):
            overruns += 1
        last = k == len(wavs) - 1
        cut = rng.randint(1, 3) if timing == "barge_in" and not last else None
        spoken = waited = 0
        ended = False
        while waited < max_wait and idx < len(units):
            p = units[idx]
            idx += 1
            spoken += speak in p
            waited += 1
            if turn_eos in p:
                ended = True
                break
            if cut and spoken >= cut:
                break
        idx += 1  # the quiet second
        cut_open = cut is not None and not ended and spoken > 0
    return overruns


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--seqs", type=Path, required=True)
    ap.add_argument("--scripts", type=Path, required=True)
    ap.add_argument("--manifest", type=Path, nargs="+", required=True)
    ap.add_argument("--src", type=Path, required=True, help="MiniCPM-o snapshot, for the tokenizer")
    ap.add_argument("--out", type=Path, required=True)
    args = ap.parse_args()

    from transformers import AutoTokenizer
    tok = AutoTokenizer.from_pretrained(args.src, trust_remote_code=True)
    unit, unit_end, speak, turn_eos = (tok.convert_tokens_to_ids(t) for t in ["<unit>", "</unit>", "<|speak|>", "<|turn_eos|>"])
    scripts = {s["id"]: s for s in map(json.loads, args.scripts.read_text(encoding="utf-8").splitlines())}
    wavs = {m["id"]: m["wavs"] for p in args.manifest for m in map(json.loads, p.read_text(encoding="utf-8").splitlines())}

    rows, reasons = [], collections.Counter()
    for f in sorted(args.seqs.glob("*.pt")):
        sid = f.stem
        d = torch.load(f)
        units = units_of(d, unit, unit_end)
        text = tok.decode([t for u in units for t in u])
        reason = None
        if not any(speak in u for u in units):
            reason = "never spoke"
        elif IDENTITY.search(text):
            reason = "says it is Qwen or made by Alibaba"
        elif cut_in_overruns(sid, scripts[sid]["timing"], wavs[sid], units, speak, turn_eos):
            reason = "kept speaking 3 s into a cut-in"
        reasons[reason or "kept"] += 1
        rows.append({"id": sid, "timing": scripts[sid]["timing"], "keep": reason is None, "reason": reason,
                     "tokens": len(d["ids"]), "units": len(units)})
    args.out.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    by_timing = collections.Counter((r["timing"], r["reason"] or "kept") for r in rows)
    print(json.dumps({"all": dict(reasons), "by timing": {f"{a}: {b}": n for (a, b), n in sorted(by_timing.items())}}, indent=1))


if __name__ == "__main__":
    main()
