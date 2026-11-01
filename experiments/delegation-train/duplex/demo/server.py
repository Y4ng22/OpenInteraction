"""Try a trained adapter from a browser: one session, one second of microphone audio per request.

The browser posts each second of 16 kHz audio to /unit and long-polls /voice for the model's own voice. /unit runs only
the language model; a second thread turns its output into speech, so the two stages overlap and the voice arrives as
soon as it is made instead of with the next second of microphone audio. Tool calls are held back from speech and
answered with simulated results built from their arguments: fast tools two seconds after the call, the backend six.
The server binds to localhost only; open it through an SSH tunnel. The waveform decoder needs onnx, onnxruntime and a
few others that the training env lacks, so they sit in their own folder on PYTHONPATH.

    PYTHONPATH=/local/data/xy2764/envs/demo-site python demo/server.py --src <snapshot> --lora runs/lc_1 \
        --tools ../data/tools_split.jsonl --allow ../duplex_full/allowed_banned_ids_v4.json --port 8765
"""
import argparse
import json
import queue
import re
import sys
import threading
import time
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import numpy as np
import torch
from accelerate.hooks import remove_hook_from_module
from transformers import AutoModel, AutoTokenizer

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from build_sequences import system_prompt
from duplex_test import CLOSE, OPEN, merge_lora

TTS_BOS = 151703  # appears inside replies and breaks call JSON; never useful in text
FRONTEND = ["hourlyWeatherSnapshot", "make_todo_list", "getChildVaccinationStatus", "Body Mass Index Calculator"]
BACKEND_DELAY = 6  # units
MAX_SKIP = 8  # units run ahead of the clock in one request
SR = 16000
PITCH_FRAME = 640  # 40 ms
MIN_PITCHED = .24  # seconds a unit needs to count as speech; speech has .5 or more, noise and coughs under .2


def pitched(pcm):
    """Seconds of the unit with a voice's pitch, 80 to 400 Hz. Noise is as loud as speech but has none."""
    frames = 0
    for i in range(0, len(pcm) - PITCH_FRAME + 1, PITCH_FRAME):
        x = pcm[i:i + PITCH_FRAME] - pcm[i:i + PITCH_FRAME].mean()
        if np.sqrt((x ** 2).mean()) < .02:
            continue
        r = np.fft.irfft(np.abs(np.fft.rfft(x, 2 * PITCH_FRAME)) ** 2)
        frames += r[SR // 400:SR // 80].max() > .5 * r[0]
    return frames * PITCH_FRAME / SR


def number(args, *names):
    for n in names:
        if isinstance(args.get(n), (int, float)):
            return float(args[n])


def simulate(name, args):
    """A made-up result built from the call's own arguments, so a wrong argument shows in what comes back."""
    if name == "spawn_thinking":
        return {"status": "completed", "result": f"Finished: {args.get('objective')}. Everything went as planned."}
    if name == "hourlyWeatherSnapshot":
        return {"request": args, "temperature_f": 58, "condition": "partly cloudy", "wind_mph": 9}
    if name == "make_todo_list":
        return {"status": "created", "tasks": args.get("tasks") or args}
    if name == "getChildVaccinationStatus":
        return {"child": args.get("child_name") or args, "status": "up to date", "next_due": "2027-03-01"}
    if name == "Body Mass Index Calculator":
        h, w = number(args, "height", "height_cm"), number(args, "weight", "weight_kg")
        return {"bmi": round(w / (h / 100) ** 2, 1)} if h and w else {"error": "height and weight are needed"}
    return {"status": "ok"}


class Session:
    def __init__(self, duplex, tok, system, voice, nudge):
        self.duplex, self.tok, self.system, self.voice, self.nudge = duplex, tok, system, voice, nudge
        self.lock, self.cache, self.n_call = threading.Lock(), None, 0
        self.jobs, self.voices, self.turn = queue.Queue(), queue.Queue(), 0
        self.open_id, self.close_id = tok.convert_tokens_to_ids(OPEN), tok.convert_tokens_to_ids(CLOSE)
        threading.Thread(target=self.speak_loop, daemon=True).start()
        self.reset()

    def reset(self):
        with self.lock:  # a second Start while the first is still preparing would corrupt the decoder
            self.jobs.join()
            d, cache = self.duplex, self.cache
            d.generate_audio = True
            d.force_listen_count = 0  # left over from a cut-in; prepare() restarts the count it is compared with
            feed, held = d.decoder.feed, []  # one token at a time this takes 30 s; the decoder takes them all at once
            d.decoder.feed = lambda embeds, return_logits=False: held.append(embeds)
            d.prepare(prefix_system_prompt=self.system, prompt_wav_path=None if cache else self.voice)
            d.decoder.feed = feed
            feed(torch.cat(held))
            if cache:
                d.flow_cache_base, d.hift_cache_base, d.token2wav_initialized = *cache, True
                d._reset_token2wav_for_new_turn()
            self.cache = d.flow_cache_base, d.hift_cache_base
            d.generate_audio = False  # speak() does this part on its own thread
            while not self.voices.empty():
                self.voices.get()
            self.in_call, self.call, self.calls, self.queue, self.t, self.n_call = False, "", [], [], 0, 0
            self.heard, self.quiet = False, 0

    def out(self):
        """Text since the last call, split into what is spoken and the finished tool calls."""
        ids = [i for i in self.duplex.res_ids[self.before:] if i != TTS_BOS]
        raw, spoken, calls, ended = self.tok.decode(ids, skip_special_tokens=False), "", [], False
        ended = "<|turn_eos|>" in raw
        raw = re.sub(r"<\|(?!turn_eos)[^|]*\|>", "", raw).replace("<|turn_eos|>", "")
        while raw:
            if self.in_call:
                head, sep, raw = raw.partition(CLOSE)
                self.call += head
                if sep:
                    calls.append(self.call)
                    self.call, self.in_call = "", False
            else:
                head, sep, raw = raw.partition(OPEN)
                spoken += head
                self.in_call = bool(sep)
        return spoken, calls, ended

    def spoken(self, hidden):
        """The unit's hidden states without the tool calls: the voice decoder has no business reading JSON."""
        keep, in_call = [], self.in_call
        for h in hidden:
            in_call = in_call or h[0] == self.open_id
            if not in_call:
                keep.append(h)
            in_call = in_call and h[0] != self.close_id
        return keep

    def speak_loop(self):
        while True:
            job = self.jobs.get()
            try:
                self.speak(*job)
            except Exception:
                traceback.print_exc()
            self.jobs.task_done()

    @torch.no_grad()
    def speak(self, hidden, ended, turn):
        """MiniCPMODuplex.streaming_generate from its TTS step on, for one unit of the language model's output."""
        d, t0 = self.duplex, time.time()
        first = d.tts_text_start_pos == 0
        if first and all(h[2] for h in hidden):  # the turn was only tool calls
            return
        new, kv = torch.zeros(0, dtype=torch.long), None
        if hidden:
            cond = d._convert_results_to_tts_input(hidden)
            new, kv = d.model.tts.generate_chunk(
                inputs_embeds=cond, temperature=d.tts_temperature, repetition_penalty=d.tts_repetition_penalty,
                eos_token=d.tts_eos_token, force_no_stop=False, max_new_token=26, min_new_tokens=0 if ended or first else 26,
                past_key_values=d.tts_past_key_values, logits_processors=d.tts_logits_processors,
                text_start_pos=d.tts_text_start_pos)
            d.tts_text_start_pos += cond.shape[1] + new.shape[1]
        d.tts_past_key_values = kv
        if ended:
            d.tts_text_start_pos, d.tts_past_key_values = 0, None
        wav = d._generate_waveform_from_tokens(new, self.voice, ended, force_flush=first)
        if ended:
            d._reset_token2wav_for_new_turn()
        if wav is not None:
            wav = np.trim_zeros(wav, "f") if first else wav  # the first chunk comes left-padded to a full second
            self.voices.put((turn, round(1000 * (time.time() - t0)), wav.astype(np.float32).tobytes()))

    def step(self, may_listen=True, **feed):
        d = self.duplex
        self.before = len(d.res_ids)
        if not d.streaming_prefill(**feed)["success"]:
            return {"text": "", "calls": [], "listen": True, "ended": False}
        banned = d.decoder.forbidden_token_ids
        if not may_listen:
            banned.append(d.listen_token_id)
        got = d.streaming_generate(decode_mode="greedy")
        if not may_listen:
            banned.remove(d.listen_token_id)
        hidden = [] if got["is_listen"] else self.spoken(d.total_hidden[-1])
        text, calls, ended = self.out()
        if hidden or ended:
            self.jobs.put((hidden, ended, self.turn))
        self.turn += ended
        return {"text": text, "calls": calls, "listen": got["is_listen"], "ended": ended}

    def answer(self, call):
        """(when, what the page shows, simulated result) for a finished call."""
        self.n_call += 1
        shown = {"id": self.n_call, "raw": call}
        try:
            c = json.loads(call)
            name, args = c["name"], c["arguments"]
        except (json.JSONDecodeError, KeyError, TypeError):
            return shown, None
        shown.update(name=name, arguments=args)
        return shown, (self.t + (BACKEND_DELAY if name == "spawn_thinking" else 2), self.n_call, name, simulate(name, args))

    def enqueue(self, calls):
        shown = []
        for call in calls:
            item, due = self.answer(call)
            shown.append(item)
            if due:
                self.queue.append(due)
        return shown

    def run(self, parts, events, may_listen=True, **feed):
        t0 = time.time()
        part = self.step(may_listen, **feed)
        parts.append(part)
        if part["text"]:
            events.append({"text": part["text"]})
        events += [{"call": c} for c in self.enqueue(part["calls"])]
        self.steps.append(round(1000 * (time.time() - t0)))
        self.t += "audio_waveform" in feed

    def deliver(self, parts, events):
        for item in [q for q in self.queue if q[0] <= self.t]:
            self.queue.remove(item)
            text = "<tool_response>" + json.dumps(item[3], ensure_ascii=False) + "</tool_response>"
            events.append({"result": {"id": item[1], "name": item[2], "result": item[3]}})
            self.run(parts, events, text_list=[text])

    def cut_in(self):
        """The user talked over the reply. Left alone the model may not listen until it ends its turn, so end it here."""
        d = self.duplex
        d.current_turn_ended, d.force_listen_count = True, d._streaming_generate_count + 1
        self.in_call, self.call = False, ""
        self.jobs.put(([], True, self.turn))
        self.turn += 1

    def unit(self, pcm, playing=False, cut=False):
        with self.lock:
            t0, parts, events, self.steps = time.time(), [], [], []
            if cut:
                self.cut_in()
            self.deliver(parts, events)
            self.quiet = 0 if pcm.any() else self.quiet + 1
            voiced = not playing and pitched(pcm) > MIN_PITCHED  # not a cough, not its own voice from the speakers
            self.heard = (self.heard or voiced) and self.quiet < 3
            # Training had almost no requests under three seconds, and after them the model keeps listening for good.
            # Once the user has been quiet for two units it has to answer.
            self.run(parts, events, not (self.nudge and self.heard and self.quiet == 2), audio_waveform=pcm)
            self.heard = self.heard and parts[-1]["listen"]
            # Nobody hears the model write a call or wait on a fast tool, so while the user is quiet those units need
            # not take a second each.
            for _ in range(MAX_SKIP):
                if pcm.any() or not (self.in_call or any(q[2] != "spawn_thinking" for q in self.queue)):
                    break
                self.deliver(parts, events)
                self.run(parts, events, audio_waveform=np.zeros(SR, np.float32))
            return {"events": events, "listen": parts[-1]["listen"], "ended": any(p["ended"] for p in parts), "turn": self.turn,
                    "ms": round(1000 * (time.time() - t0)), "steps": self.steps}


def serve(session, port):
    page = (Path(__file__).parent / "index.html").read_bytes()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def reply(self, body, kind="application/json", **headers):
            self.send_response(200)
            self.send_header("Content-Type", kind)
            self.send_header("Content-Length", str(len(body)))
            for k, v in headers.items():
                self.send_header(k, str(v))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            self.reply(page, "text/html; charset=utf-8")

        def do_POST(self):
            body = self.rfile.read(int(self.headers["Content-Length"]))
            if self.path == "/reset":
                session.nudge = json.loads(body or b"{}").get("nudge", session.nudge)
                session.reset()
                return self.reply(b"{}")
            if self.path == "/voice":
                try:
                    turn, ms, wav = session.voices.get(timeout=15)
                except queue.Empty:
                    return self.reply(b"", "application/octet-stream")
                return self.reply(wav, "application/octet-stream", **{"X-Turn": turn, "X-Ms": ms})
            pcm = np.frombuffer(body, np.float32).copy()
            self.reply(json.dumps(session.unit(pcm, "playing" in self.path, "cut" in self.path), ensure_ascii=False).encode())

    ThreadingHTTPServer(("127.0.0.1", port), Handler).serve_forever()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", type=Path, required=True)
    ap.add_argument("--lora", type=Path, required=True)
    ap.add_argument("--tools", type=Path, required=True)
    ap.add_argument("--allow", type=Path, required=True)
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--no-nudge", dest="nudge", action="store_false", help="let the model stay silent after short requests")
    ap.add_argument("--one-card", action="store_true", help="keep everything on one card with room for it, such as an A100")
    args = ap.parse_args()
    place = {"device_map": {"": 0}} if args.one_card else {"device_map": "auto", "max_memory": {0: "2GiB", 1: "14GiB", 2: "14GiB"}}
    model = AutoModel.from_pretrained(args.src, trust_remote_code=True, attn_implementation="sdpa", torch_dtype=torch.bfloat16,
                                      init_vision=False, init_audio=True, init_tts=True, **place).eval()  # the voice decoder takes card 0
    merge_lora(model.llm, args.lora)
    remove_hook_from_module(model.tts, recurse=True)  # the speech thread gets a card of its own, shared with the voice decoder
    model.tts.to(0)
    for m in (model.tts.emb_text, model.tts.projector_semantic):
        m.register_forward_pre_hook(lambda m, a: tuple(x.to(next(m.parameters()).device) for x in a))
    tok = AutoTokenizer.from_pretrained(args.src, trust_remote_code=True)
    model.init_tts(enable_float16=True, n_timesteps=5)  # 10 steps runs slower than real time on these cards
    duplex = model.as_duplex(generate_audio=True)
    allow = set(json.loads(args.allow.read_text()))
    duplex.decoder.forbidden_token_ids[:] = [i for i in duplex.decoder.forbidden_token_ids if i not in allow] + [TTS_BOS]
    tools = {}
    for line in args.tools.read_text(encoding="utf-8").splitlines():
        t = json.loads(line)
        if t["name"] in FRONTEND:
            tools.setdefault(t["id"], t)
    system = system_prompt({"frontend_tools": list(tools)}, tools)
    print("devices:", sorted({str(p.device) for m in (model.llm, model.apm, model.tts) for p in m.parameters()}),
          {n: str(next(getattr(model, n).parameters()).device) for n in ("apm", "tts")}, flush=True)
    print("tools:", [t["name"] for t in tools.values()], flush=True)
    print("ready", flush=True)
    serve(Session(duplex, tok, system, str(args.src / "assets/system_ref_audio.wav"), args.nudge), args.port)


if __name__ == "__main__":
    main()
