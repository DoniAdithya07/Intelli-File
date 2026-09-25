"""Whisper on onnxruntime alone — no PyTorch, no optimum (Phase 14).

The Phase 7 pipeline drove the same two ONNX files through
`optimum.ORTModelForSpeechSeq2Seq`, whose `generate()` is PyTorch's — so
the whole 600 MB of torch shipped for a greedy decoding loop. This module
is that loop: log-mel features (transformers' feature extractor, numpy
path), the encoder once per 30 s window, then the merged decoder step by
step with its KV cache. Same weights, same prompt handling, same
suppression rules as the generation config, so the output is identical —
`prototype_transcription.py` check 7 asserts that against the recorded
transcripts, and the switch-over compared every demo clip word for word.
"""

import json
from pathlib import Path

import numpy as np
import onnxruntime as ort
from tokenizers import Tokenizer

WHISPER_SAMPLE_RATE = 16000
MAX_TARGET_POSITIONS = 448


class WhisperOnnx:
    def __init__(self, model_dir: Path):
        onnx_dir = model_dir / "onnx"
        encoder = next(onnx_dir.glob("encoder_model*.onnx"), None)
        decoder = next(onnx_dir.glob("decoder_model_merged*.onnx"), None)
        if encoder is None or decoder is None:
            raise FileNotFoundError(f"Whisper ONNX files not found in {onnx_dir}")
        opts = ort.SessionOptions()
        opts.log_severity_level = 3
        self.encoder = ort.InferenceSession(str(encoder), opts, providers=["CPUExecutionProvider"])
        self.decoder = ort.InferenceSession(str(decoder), opts, providers=["CPUExecutionProvider"])
        self.active_provider = self.encoder.get_providers()[0]

        from transformers import WhisperFeatureExtractor  # numpy path only; no torch import

        self.features = WhisperFeatureExtractor.from_pretrained(str(model_dir), local_files_only=True)
        self.tokenizer = Tokenizer.from_file(str(model_dir / "tokenizer.json"))

        gen = json.loads((model_dir / "generation_config.json").read_text())
        cfg = json.loads((model_dir / "config.json").read_text())
        self.decoder_start = gen["decoder_start_token_id"]
        self.eos = gen["eos_token_id"]
        self.prev_sot = gen.get("prev_sot_token_id", 50360)
        self.forced = [tok for _, tok in gen.get("forced_decoder_ids", [])]
        self.suppress = np.array(gen.get("suppress_tokens", []), dtype=np.int64)
        self.begin_suppress = np.array(gen.get("begin_suppress_tokens", []), dtype=np.int64)
        self.layers = cfg["decoder_layers"]
        self.heads = cfg["decoder_attention_heads"]
        self.head_dim = cfg["d_model"] // self.heads
        self.first_special = self.decoder_start  # every id from <|startoftranscript|> up is a control token
        self._past_names = [i.name for i in self.decoder.get_inputs() if i.name.startswith("past_key_values")]
        self._present_names = [o.name for o in self.decoder.get_outputs() if o.name.startswith("present")]
        self._has_cache_flag = any(i.name == "use_cache_branch" for i in self.decoder.get_inputs())

    # ----- prompt handling, mirroring WhisperProcessor.get_prompt_ids -----

    def prompt_ids(self, prompt: str) -> list[int]:
        """<|startofprev|> followed by the prompt's tokens (with the
        leading space Whisper's tokenizer expects), no other specials."""
        ids = self.tokenizer.encode(" " + prompt.strip(), add_special_tokens=False).ids
        return [self.prev_sot] + ids

    # ----- one ≤ 30 s window -----

    def transcribe_window(self, audio: np.ndarray, prompt_ids: list[int] | None = None) -> str:
        feats = self.features(audio, sampling_rate=WHISPER_SAMPLE_RATE, return_tensors="np")["input_features"].astype(np.float32)
        encoder_out = self.encoder.run(None, {"input_features": feats})[0]

        prefix = list(prompt_ids or []) + [self.decoder_start] + self.forced
        budget = MAX_TARGET_POSITIONS - len(prefix)
        generated: list[int] = []

        empty = np.zeros((1, self.heads, 0, self.head_dim), dtype=np.float32)
        past = {name: empty for name in self._past_names}
        input_ids = np.array([prefix], dtype=np.int64)
        use_cache = False
        for step in range(budget):
            feed = {"input_ids": input_ids, "encoder_hidden_states": encoder_out, **past}
            if self._has_cache_flag:
                feed["use_cache_branch"] = np.array([use_cache])
            outputs = self.decoder.run(None, feed)
            logits = outputs[0][0, -1].astype(np.float32)
            logits[self.suppress] = -np.inf
            if step == 0:
                logits[self.begin_suppress] = -np.inf
            token = int(np.argmax(logits))
            if token == self.eos:
                break
            generated.append(token)
            # Cache: the decoder's own keys/values grow every step; the
            # encoder's are computed once on the no-cache step and come
            # back as an EMPTY-BATCH placeholder (shape (0, heads, 1, dim))
            # from the cache branch, so they are frozen after step 0 — the
            # bug that made the first version stop after two tokens.
            present = dict(zip(self._present_names, outputs[1:]))
            for name in self._past_names:
                if ".decoder." in name or not use_cache:
                    past[name] = present[name.replace("past_key_values", "present")]
            input_ids = np.array([[token]], dtype=np.int64)
            use_cache = True
        text_ids = [t for t in generated if t < self.first_special and t != self.eos]
        return self.tokenizer.decode(text_ids, skip_special_tokens=True).strip()
