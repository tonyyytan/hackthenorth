"""Smoke test: InsightFace buffalo_l detect + 512-d ArcFace embed on CPU."""
import time
import numpy as np
from insightface.app import FaceAnalysis
from insightface.data import get_image

# ponytail: detection+recognition only - genderage/landmark models triple the latency for nothing
app = FaceAnalysis(name="buffalo_l", providers=["CPUExecutionProvider"],
                   allowed_modules=["detection", "recognition"])
app.prepare(ctx_id=-1, det_size=(640, 640))

img = get_image("t1")  # bundled sample, 6 faces
t = time.perf_counter()
faces = app.get(img)
dt = time.perf_counter() - t

print(f"{len(faces)} faces in {dt*1000:.0f} ms ({img.shape[1]}x{img.shape[0]})")
embs = np.stack([f.normed_embedding for f in faces])
print("embedding shape:", embs.shape)

sim = embs @ embs.T  # normed -> cosine
off = sim[~np.eye(len(sim), dtype=bool)]
print(f"self-sim {np.diag(sim).min():.3f}  cross-sim max {off.max():.3f} mean {off.mean():.3f}")

assert embs.shape[1] == 512
assert np.allclose(np.diag(sim), 1, atol=1e-3)
assert off.max() < 0.4, "distinct people scored above the 0.4 match threshold"
print("OK")
