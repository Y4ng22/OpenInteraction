"""Step 2 of the delegation data: label every tool in the pool with Qwen3-14B.

For each tool: its kind (lookup, search or action; only action tools make a request a handoff), its domain (used to
hold out whole domains for testing), and whether a person would plausibly ask a voice assistant for it. The model
being trained never sees these labels. Resumable: tools already in --out are skipped.

    python label_tools.py --tools tools.jsonl --out tool_labels.jsonl --url http://127.0.0.1:8200/v1 [--limit 20]
"""
import argparse
import json
import re
import threading
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

DOMAINS = ["travel", "finance", "shopping", "food", "communication", "calendar", "weather", "maps", "sports",
           "entertainment", "news", "health", "education", "jobs", "real_estate", "social_media", "devices",
           "developer", "data_lookup", "math", "other"]

PROMPT = """You label API tools for a voice assistant dataset.

Tool:
{tool}

Answer with JSON only, no other text:
{{"kind": "lookup" | "search" | "action", "domain": one of {domains}, "voice_ok": true | false}}

kind:
- "lookup": reads one small piece of information or computes something: current weather, a status, a conversion, a single record by id, a calculation.
- "search": searches, lists or filters many records: products, hotels, restaurants, news, listings.
- "action": changes something outside the assistant or commits the user to something: books, reserves, orders, buys, pays, transfers, sends, posts, cancels, deletes, schedules, registers, or creates or updates a stored record or account. Generating or formatting content that is only returned (a caption, a QR code, a summary, a converted file) is "lookup", not "action".
voice_ok: true if an ordinary person could plausibly ask a voice assistant for this; false for developer utilities, raw data processing or abstract math helpers."""


def ask(url, model, text):
    body = {"model": model, "messages": [{"role": "user", "content": text}], "temperature": 0, "max_tokens": 80,
            "chat_template_kwargs": {"enable_thinking": False}}
    req = urllib.request.Request(url.rstrip("/") + "/chat/completions", data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=120) as r:
        return json.loads(r.read())["choices"][0]["message"]["content"]


def parse(answer):
    m = re.search(r"\{.*\}", answer, re.S)
    out = json.loads(m.group(0)) if m else {}
    if out.get("kind") not in ("lookup", "search", "action") or out.get("domain") not in DOMAINS:
        raise ValueError(answer[:200])
    return {"kind": out["kind"], "domain": out["domain"], "voice_ok": bool(out.get("voice_ok"))}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tools", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--url", default="http://127.0.0.1:8200/v1")
    ap.add_argument("--model", default="qwen3-14b")
    ap.add_argument("--workers", type=int, default=32)
    ap.add_argument("--limit", type=int)
    args = ap.parse_args()

    tools = [json.loads(l) for l in args.tools.read_text(encoding="utf-8").splitlines()]
    done = set()
    if args.out.exists():
        done = {json.loads(l)["id"] for l in args.out.read_text(encoding="utf-8").splitlines() if l.strip()}
    todo = [t for t in tools if t["id"] not in done][: args.limit]
    lock, failed = threading.Lock(), []

    def work(tool):
        shown = {k: tool[k] for k in ("name", "description", "parameters")}
        for _ in range(2):
            try:
                label = parse(ask(args.url, args.model, PROMPT.format(tool=json.dumps(shown, ensure_ascii=False),
                                                                     domains=json.dumps(DOMAINS))))
                break
            except Exception as e:  # unreadable answer or a transient server error: one retry
                err = e
        else:
            with lock:
                failed.append((tool["id"], str(err)[:120]))
            return
        with lock, args.out.open("a", encoding="utf-8") as f:
            f.write(json.dumps({"id": tool["id"], "name": tool["name"], **label}, ensure_ascii=False) + "\n")

    with ThreadPoolExecutor(args.workers) as pool:
        list(pool.map(work, todo))
    print(f"labeled {len(todo) - len(failed)} of {len(todo)}; failed {len(failed)}")
    for f in failed[:10]:
        print("  failed", *f)


if __name__ == "__main__":
    main()
