"""Cross-encoder reranking (Phase 18, Objective 3): the top candidates of
a hybrid search are re-scored by a model that reads the query and the
passage *together*. A bi-encoder embeds each side separately and compares
the two vectors, so it cannot tell "handling traffic spikes" from
"traffic spike incident report" — the cross-encoder can. It costs a
forward pass per candidate, so the router only asks for it on the queries
that need it (long, multi-concept, natural-language), and only over the
top RERANK_TOP_N fused results.

ms-marco-MiniLM-L-6-v2, ONNX, CPU, fully offline once downloaded
(scripts/download_reranker_model.py). Missing model = no reranking, the
route simply reports the stage as unavailable.
"""

from pathlib import Path

import numpy as np
import onnxruntime as ort
from tokenizers import Tokenizer

MAX_PAIR_TOKENS = 512
RERANK_TOP_N = 10


class Reranker:
    def __init__(self, model_dir: Path):
        model_path = model_dir / "model.onnx"
        tokenizer_path = model_dir / "tokenizer.json"
        if not model_path.exists() or not tokenizer_path.exists():
            raise FileNotFoundError(f"Reranker files not found in {model_dir}. Run scripts/download_reranker_model.py first.")
        self.tokenizer = Tokenizer.from_file(str(tokenizer_path))
        self.tokenizer.no_truncation()
        self.tokenizer.enable_truncation(max_length=MAX_PAIR_TOKENS)
        self.tokenizer.enable_padding()
        # CPU on purpose, like Whisper: a handful of pairs per query is
        # milliseconds of work and the accelerators were measured slower
        # or wrong for this family of models (see embeddings/hardware.py).
        self.session = ort.InferenceSession(str(model_path), providers=["CPUExecutionProvider"])
        self.name = model_dir.name

    def score(self, query: str, passages: list[str]) -> list[float]:
        """One relevance logit per passage (higher = more relevant). The
        scale is the model's own; only the order matters to callers."""
        if not passages:
            return []
        encodings = self.tokenizer.encode_batch([(query, p) for p in passages])
        input_ids = np.array([e.ids for e in encodings], dtype=np.int64)
        attention_mask = np.array([e.attention_mask for e in encodings], dtype=np.int64)
        token_type_ids = np.array([e.type_ids for e in encodings], dtype=np.int64)
        outputs = self.session.run(None, {"input_ids": input_ids, "attention_mask": attention_mask, "token_type_ids": token_type_ids})
        logits = outputs[0]
        return [float(row[0]) for row in logits]
