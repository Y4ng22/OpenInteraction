"""Fun-Audio-Chat-8B as a drop-in frontend for run_chain.py and the delegation runner.

Same call shape as run_chain.MiniCPMOFrontend: frontend(msgs, item) -> (raw text, first-token seconds, seconds).
msgs use run_chain's layout ({"role", "content": [str or 16 kHz waveform]}). The system message is prefixed with
Fun-Audio-Chat's own text-mode prompt, as its function-calling example does; tools stay in the same Qwen
<tools> / <tool_call> format, which is also Fun-Audio-Chat's. Text only (no speech tokens), greedy.

Needs the Fun-Audio-Chat repo (for its `funaudiochat` and `utils` packages) and transformers 4.52.3.
"""
import sys
import time
from pathlib import Path

import numpy as np

END = ("<|im_end|>", "<|endoftext|>")


class FunAudioFrontend:
    def __init__(self, args):
        import torch
        from transformers import AutoConfig, AutoModelForSeq2SeqLM, AutoProcessor
        sys.path.insert(0, str(Path(args.funaudio_repo).resolve()))
        from funaudiochat.register import register_funaudiochat
        register_funaudiochat()
        from utils.constant import AUDIO_TEMPLATE, DEFAULT_S2T_PROMPT
        self.audio_template, self.mode_prompt = AUDIO_TEMPLATE, DEFAULT_S2T_PROMPT
        gpus = [int(g) for g in args.gpus.split(",")]
        config = AutoConfig.from_pretrained(args.funaudio_model)
        self.processor = AutoProcessor.from_pretrained(args.funaudio_model)
        self.model = AutoModelForSeq2SeqLM.from_pretrained(
            args.funaudio_model, config=config, torch_dtype=torch.bfloat16, device_map="auto",
            max_memory={g: f"{args.max_mem_gib}GiB" for g in gpus}).eval()
        self.model.sp_gen_kwargs.update({"text_greedy": True, "disable_speech": True})
        self.max_new_tokens = args.max_new_tokens
        self.torch = torch

    def _conversation(self, msgs):
        conv, audios = [], []
        for m in msgs:
            parts = []
            for c in m["content"]:
                if isinstance(c, np.ndarray):
                    audios.append(c.astype(np.float32))
                    parts.append(self.audio_template)
                else:
                    parts.append(str(c))
            text = "\n".join(parts)
            if m["role"] == "system":
                text = self.mode_prompt + "\n\n" + text
            conv.append({"role": m["role"], "content": text})
        return conv, audios

    def __call__(self, msgs, item=None):
        conv, audios = self._conversation(msgs)
        text = self.processor.apply_chat_template(conv, add_generation_prompt=True, tokenize=False)
        inputs = self.processor(text=text, audio=audios or None, return_tensors="pt",
                                return_token_type_ids=False).to(self.model.device)
        started = time.perf_counter()
        with self.torch.no_grad():
            ids, _ = self.model.generate(**inputs, max_new_tokens=self.max_new_tokens)
        elapsed = time.perf_counter() - started
        raw = self.processor.decode(ids[0, inputs.input_ids.size(1):], skip_special_tokens=False)
        for end in END:
            raw = raw.split(end)[0]
        return raw.strip(), None, elapsed
