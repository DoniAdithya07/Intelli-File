"""CLIP image preprocessing, kept standalone from clip_model.py so video
keyframes (Phase 8b) can reuse it without touching the model wrapper.

These constants come from the model's own preprocessor_config.json and
are NOT tunable: CLIP was trained with this exact resize/crop/normalize
recipe, and getting it wrong degrades embeddings silently — no error, no
crash, just quietly worse search results.
"""

import numpy as np
from PIL import Image

CLIP_IMAGE_SIZE = 224
CLIP_MEAN = np.array([0.48145466, 0.4578275, 0.40821073], dtype=np.float32)
CLIP_STD = np.array([0.26862954, 0.26130258, 0.27577711], dtype=np.float32)


def preprocess_image(image: Image.Image) -> np.ndarray:
    """Resize shortest edge to 224 (bicubic) -> center-crop 224x224 ->
    RGB -> CHW float32 -> normalize. Returns a (3, 224, 224) array."""
    image = image.convert("RGB")

    width, height = image.size
    scale = CLIP_IMAGE_SIZE / min(width, height)
    image = image.resize((round(width * scale), round(height * scale)), Image.BICUBIC)

    width, height = image.size
    left = (width - CLIP_IMAGE_SIZE) // 2
    top = (height - CLIP_IMAGE_SIZE) // 2
    image = image.crop((left, top, left + CLIP_IMAGE_SIZE, top + CLIP_IMAGE_SIZE))

    array = np.asarray(image, dtype=np.float32) / 255.0
    array = (array - CLIP_MEAN) / CLIP_STD
    return array.transpose(2, 0, 1)
