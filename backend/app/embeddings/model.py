"""Local embedding inference: tokenize -> ONNX Runtime -> mean pooling ->
L2 normalize. Standard sentence-transformers recipe for all-MiniLM-L6-v2,
run fully offline once the model files exist locally (see
scripts/download_model.py) — no network calls happen here.
"""

import json
from pathlib import Path

import numpy as np
from tokenizers import Tokenizer

from .hardware import create_session

EMBEDDING_DIMENSION = 384
MAX_SEQUENCE_LENGTH = 256  # the shipped models' trained max_seq_length (MiniLM 256; bge-small accepts 512 but our chunks are ≤ 254 tokens)

# The shipped text-embedding model, in preference order. bge-small-en-v1.5
# measured in Phase 13: vector-only Recall@5 100% vs 93% for MiniLM on the
# 60-query corpus (before its query instruction); MiniLM stays as the
# fallback so a checkout without the new download still runs.
PREFERRED_EMBEDDING_MODELS = ("bge-small-en-v1.5", "all-MiniLM-L6-v2")


def default_model_dir(models_root: Path | None = None) -> Path:
    from ..paths import models_dir

    root = models_root or models_dir()
    for name in PREFERRED_EMBEDDING_MODELS:
        if (root / name / "model.onnx").exists():
            return root / name
    return root / PREFERRED_EMBEDDING_MODELS[0]


class EmbeddingModel:
    def __init__(self, model_dir: Path, pooling: str | None = None):
        model_path = model_dir / "model.onnx"
        tokenizer_path = model_dir / "tokenizer.json"
        # Sentence-transformers models differ in how a sentence vector is
        # read out of the token states: MiniLM = mean over tokens, BGE =
        # the [CLS] token. A `pooling.txt` next to the model says which
        # (Phase 13's comparison models); the shipped model is mean.
        pooling_file = model_dir / "pooling.txt"
        self.pooling = pooling or (pooling_file.read_text().strip() if pooling_file.exists() else "mean")
        # Retrieval models like BGE are trained with an instruction in front
        # of the QUERY only ("Represent this sentence for searching relevant
        # passages: "); documents are embedded bare. A `query_prefix.txt`
        # next to the model carries it; embed_queries() applies it.
        prefix_file = model_dir / "query_prefix.txt"
        self.query_prefix = prefix_file.read_text().rstrip("\n") if prefix_file.exists() else ""
        self.name = model_dir.name
        self.model_id = f"{model_dir.name}@{self.pooling}"  # changes whenever the embedding recipe changes → re-index
        # Distance thresholds belong to the model, not the search code: each
        # model has its own similarity scale. scripts/tune_semantic_thresholds.py
        # writes thresholds.json from the labelled corpus; MiniLM's hand-tuned
        # values are the defaults for a model folder without one.
        self.thresholds = {"relevance_cutoff": 1.6, "strong_with_literal": 1.45, "strong_meaning_only": 1.50, "strong_max_gap_from_best": 0.08}
        thresholds_file = model_dir / "thresholds.json"
        if thresholds_file.exists():
            try:
                self.thresholds.update({k: float(v) for k, v in json.loads(thresholds_file.read_text()).items() if k in self.thresholds})
            except (ValueError, TypeError):
                pass
        if not model_path.exists() or not tokenizer_path.exists():
            raise FileNotFoundError(
                f"Model files not found in {model_dir}. Run scripts/download_model.py first."
            )

        self.tokenizer = Tokenizer.from_file(str(tokenizer_path))
        self.tokenizer.enable_padding()
        self.tokenizer.enable_truncation(max_length=MAX_SEQUENCE_LENGTH)
        # A second, untruncated copy for the chunker: it needs every token
        # of a whole document with character offsets, and the inference
        # tokenizer above would silently cut it at MAX_SEQUENCE_LENGTH.
        self._span_tokenizer = Tokenizer.from_file(str(tokenizer_path))
        # tokenizer.json ships with its own truncation rule (128 tokens for
        # this model) — it must be switched off, not merely left unset.
        self._span_tokenizer.no_truncation()
        self._span_tokenizer.no_padding()

        self.session = create_session(model_path, label=f"embedding model {model_dir.name}")
        self.active_provider = self.session.get_providers()[0]

    def token_spans(self, text: str) -> list[tuple[int, int, bool]]:
        """(start, end, continues_previous_word) for every model token in
        `text`, without special tokens or truncation — what the chunker uses
        to keep each chunk inside the model's window (see chunker.py)."""
        encoding = self._span_tokenizer.encode(text, add_special_tokens=False)
        return [
            (start, end, token.startswith("##"))
            for (start, end), token in zip(encoding.offsets, encoding.tokens)
        ]

    def embed_queries(self, texts: list[str]) -> np.ndarray:
        """Query-side embedding: the model's instruction prefix, if any, then embed_texts."""
        return self.embed_texts([self.query_prefix + t for t in texts]) if self.query_prefix else self.embed_texts(texts)

    def embed_texts(self, texts: list[str]) -> np.ndarray:
        if not texts:
            return np.zeros((0, EMBEDDING_DIMENSION), dtype=np.float32)

        encodings = self.tokenizer.encode_batch(texts)
        input_ids = np.array([e.ids for e in encodings], dtype=np.int64)
        attention_mask = np.array([e.attention_mask for e in encodings], dtype=np.int64)
        token_type_ids = np.zeros_like(input_ids)

        outputs = self.session.run(
            None,
            {
                "input_ids": input_ids,
                "attention_mask": attention_mask,
                "token_type_ids": token_type_ids,
            },
        )
        last_hidden_state = outputs[0]  # [batch, seq_len, 384]

        if self.pooling == "cls":
            pooled = last_hidden_state[:, 0, :]
        else:
            mask = attention_mask[:, :, None].astype(np.float32)  # [batch, seq_len, 1]
            summed = (last_hidden_state * mask).sum(axis=1)
            counts = np.clip(mask.sum(axis=1), a_min=1e-9, a_max=None)
            pooled = summed / counts  # mean pooling over real (non-padding) tokens

        norms = np.linalg.norm(pooled, axis=1, keepdims=True)
        normalized = pooled / np.clip(norms, a_min=1e-9, a_max=None)
        return normalized.astype(np.float32)
