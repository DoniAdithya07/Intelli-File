"""Local CLIP inference: text -> vector and image -> vector, projected
into ONE shared 512-dim space so a typed query can be matched directly
against photo embeddings (the whole basis of Phase 8 visual search).

Same shape as model.py's EmbeddingModel, with two ONNX sessions instead
of one (CLIP ships its text and vision towers separately). Runs fully
offline once the model files exist locally (see
scripts/download_clip_model.py) — no network calls happen here.
"""

from pathlib import Path

import numpy as np
import onnxruntime as ort
from PIL import Image
from tokenizers import Tokenizer

from .clip_preprocess import preprocess_image

IMAGE_EMBEDDING_DIMENSION = 512
MAX_TEXT_TOKENS = 77  # CLIP's trained context length (text_config.max_position_embeddings)


def _l2_normalize(vectors: np.ndarray) -> np.ndarray:
    norms = np.linalg.norm(vectors, axis=1, keepdims=True)
    return (vectors / np.clip(norms, a_min=1e-9, a_max=None)).astype(np.float32)


class ClipModel:
    def __init__(self, model_dir: Path):
        # fp16 weights, chosen 2026-09-11 against PyTorch ground truth on the
        # demo photos (CPU, one image per run):
        #   precision  size    ms/img  image-embedding cosine vs truth  distance error (mean / max)
        #   int8      152 MB    33       min 0.897, mean 0.963            0.021 / 0.111
        #   fp16      300 MB    38       1.000                            0.000 / 0.000
        #   fp32      599 MB    30       1.000                            0.000 / 0.000
        # The int8 export's 0.02-0.11 error was larger than the gaps the
        # relevance cutoff has to resolve — it put a photo of children under
        # a plane wreck at 1.496 for "Airplane" where the true model says
        # 1.607. fp16 is exact for half of fp32's size. int8 stays as a
        # fallback so an older checkout still runs.
        precision = "fp16" if (model_dir / "onnx" / "vision_model_fp16.onnx").exists() else "quantized"
        text_path = model_dir / "onnx" / f"text_model_{precision}.onnx"
        vision_path = model_dir / "onnx" / f"vision_model_{precision}.onnx"
        tokenizer_path = model_dir / "tokenizer.json"
        missing = [p for p in (text_path, vision_path, tokenizer_path) if not p.exists()]
        if missing:
            raise FileNotFoundError(
                f"CLIP model files not found: {', '.join(str(p) for p in missing)}. "
                "Run scripts/download_clip_model.py first."
            )

        self.tokenizer = Tokenizer.from_file(str(tokenizer_path))
        self.tokenizer.enable_truncation(max_length=MAX_TEXT_TOKENS)

        # CPU only, one image per run — for reproducibility, not speed.
        # (Measured 2026-09-11 on the "_quantized" (dynamic int8) export;
        # fp16 is deterministic under any batch/provider, but the CPU +
        # batch-1 recipe is kept so the int8 fallback behaves too.)
        # the same photo's embedding moved by up to 0.09 (per coordinate)
        # depending on how many images shared its batch, because dynamic
        # quantization picks the int8 scale per input tensor; and the text
        # tower gave different vectors on CoreML vs CPU. Together that
        # shifted query distances by 0.02-0.05 — larger than the resolution
        # the relevance cutoff is tuned at, and it would have made the
        # cutoff wrong on any machine that ran a different provider (the
        # grading laptop). CPU int8 at batch size 1 is deterministic, and
        # costs nothing: 40 ms/image vs 46 ms on CoreML.
        providers = ["CPUExecutionProvider"]
        self.text_session = ort.InferenceSession(str(text_path), providers=providers)
        self.vision_session = ort.InferenceSession(str(vision_path), providers=providers)
        self.active_provider = self.text_session.get_providers()[0]
        # Embedding width comes from the export, not a constant: ViT-B/32 and
        # ViT-B/16 are 512-wide, ViT-L/14 is 768. The images table is created
        # at this width.
        self.dimension = int(self.text_session.get_outputs()[0].shape[-1]) or IMAGE_EMBEDDING_DIMENSION
        # Identifies the model for the index: vectors from two different CLIP
        # variants live in the same space *name* but are not comparable.
        # The recipe is part of the identity: vectors produced under a
        # different provider/batching are not comparable to these.
        self.model_id = f"{model_dir.name}@cpu-{precision}-batch1"
        self.precision = precision

    def embed_texts(self, texts: list[str]) -> np.ndarray:
        """Embed query text into the shared image/text space."""
        if not texts:
            return np.zeros((0, self.dimension), dtype=np.float32)

        # One string per run, deliberately. This export takes only
        # `input_ids` — there's no attention_mask input to tell the model
        # which tokens are padding, and CLIP pools its text output at the
        # end-of-text token's position. Batching would require padding,
        # which risks shifting that position and silently corrupting the
        # embedding. Search embeds a single query at a time anyway, so
        # there's nothing real to gain from batching here.
        embeddings = []
        for text in texts:
            input_ids = np.array([self.tokenizer.encode(text).ids], dtype=np.int64)
            outputs = self.text_session.run(None, {"input_ids": input_ids})
            embeddings.append(outputs[0][0])
        return _l2_normalize(np.stack(embeddings))

    def embed_images(self, images: list[Image.Image]) -> np.ndarray:
        """Embed images into the same space as embed_texts()."""
        if not images:
            return np.zeros((0, self.dimension), dtype=np.float32)

        # One image per run on purpose — see __init__ for the measurement.
        embeddings = []
        for image in images:
            pixel_values = preprocess_image(image)[np.newaxis].astype(np.float32)
            embeddings.append(self.vision_session.run(None, {"pixel_values": pixel_values})[0][0])
        return _l2_normalize(np.stack(embeddings))
