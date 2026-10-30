"""Flag requests that read API parameters aloud (the review's first problem group, decided 2026-10-29).

Rule from the review: a request is unnatural when the user says values only an API would use (database codes for
places, categories, sports, competitions or languages, such as a city's numeric ID, "sport 8", "competition 572";
API parameters like "page 0", "language 1", en-AU; raw coordinate strings; long opaque keys) or dictates a URL. The
number of a specific person or thing the user deals with (a player, team, order, account, policy) is fine. Dates in
ISO form are not flagged here; TTS reads them as spoken dates. Each user turn is judged by Qwen3-14B (served by vLLM, thinking off).

    python flag_api_speech.py --final delegation_final.jsonl --out check/api_speech.jsonl [--ids id1 id2 ...]
"""
import argparse
import concurrent.futures as cf
import json
import urllib.request
from pathlib import Path

PROMPT = """You check requests that a person says aloud to a voice assistant. Decide whether the request reads out values that only a computer API would use.

UNNATURAL (answer true) only for these:
- database codes for places, categories, sports, competitions, leagues, languages or hashtags: a city's numeric ID, "sport 8", "competition 572", "community 01", "hashtag ID 77812", "tournament 0 for all"
- API parameters and flags: "page 0", "language 1", "10 at a time", "in en-AU" or "en_GB", "split by league MLB", "distances in MI", "plain list", "filtered by region, show regions"
- raw coordinate strings like "+44.4759-073.2121"
- long opaque keys nobody can say from memory, such as a 24-character YouTube channel ID or a token like "0P0000OQN8"
- a dictated web address or file path ("youtube.com/watch?v=7dKqN3Rf2Hs", "https://...", "cdn.example.net/a.png", "voicemail.acme.com/vm/1.wav")

NATURAL (answer false), even when the number looks arbitrary:
- the number of a specific person or thing the user deals with: player 84417, team 4388, farm 3087, merchant 4410, customer 55821-K, student S-2270, auction 274839201, order, ticket, booking, account, policy, listing, product, song or video numbers
- codes the user would read off a document or screen: policy FT-12, service INT-100, flight numbers
- amounts, dates in any format, times, names, places, sizes, quantities, and ordinary task wording

Request: "{text}"

Answer with JSON only: {{"unnatural": true or false, "reason": "<at most 12 words>"}}"""


def ask(url, text):
    body = {"model": "qwen3-14b", "messages": [{"role": "user", "content": PROMPT.format(text=text)}], "temperature": 0,
            "max_tokens": 60, "chat_template_kwargs": {"enable_thinking": False}}
    req = urllib.request.Request(url, json.dumps(body).encode(), {"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=120) as r:
        content = json.loads(r.read())["choices"][0]["message"]["content"]
    return json.loads(content[content.index("{"): content.rindex("}") + 1])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--final", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--ids", nargs="*")
    ap.add_argument("--url", default="http://127.0.0.1:8200/v1/chat/completions")
    ap.add_argument("--workers", type=int, default=32)
    args = ap.parse_args()

    X = [json.loads(l) for l in args.final.read_text(encoding="utf-8").splitlines()]
    if args.ids:
        X = [x for x in X if x["id"] in set(args.ids)]
    jobs = [(x["id"], k, e["text"]) for x in X for k, e in enumerate(e for e in x["events"] if e["role"] == "user")]

    def run(job):
        sid, k, text = job
        try:
            v = ask(args.url, text)
        except Exception as e:  # counted, not dropped silently
            v = {"unnatural": None, "reason": f"error: {e!r}"[:100]}
        return {"id": sid, "turn": k, "text": text, **v}

    with cf.ThreadPoolExecutor(args.workers) as pool, args.out.open("w", encoding="utf-8") as f:
        for i, r in enumerate(pool.map(run, jobs)):
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
            if (i + 1) % 1000 == 0:
                print(f"{i + 1}/{len(jobs)}", flush=True)
    rows = [json.loads(l) for l in args.out.read_text(encoding="utf-8").splitlines()]
    bad = {r["id"] for r in rows if r["unnatural"]}
    print(f"{len(rows)} turns, {sum(r['unnatural'] is None for r in rows)} errors, {len(bad)} samples flagged -> {args.out}")


if __name__ == "__main__":
    main()
