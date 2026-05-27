"""
Face scanning worker for the PHP photo gallery.

Uses InsightFace (ArcFace buffalo_l) — outputs 512-dim L2-normalised embeddings.
Replaces the old face_recognition/dlib worker (128-dim).

NOTE: If faces.json already contains 128-dim embeddings from the old worker,
      trigger a full Reset from the Faces panel before re-scanning so clustering
      works correctly with the new 512-dim embeddings.

Env vars required:
  GALLERY_URL   — e.g. https://biefeni.com (no trailing slash)
  ADMIN_TOKEN   — sha256 hex token (same value stored in localStorage bfAdminToken)

Optional:
  BATCH_SIZE    — number of images per POST batch (default 10)

Run: python scan.py
"""

import os
import sys
import uuid
import logging
import requests
import numpy as np
from io import BytesIO
from PIL import Image
import cv2
from insightface.app import FaceAnalysis

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
GALLERY_URL     = os.environ.get("GALLERY_URL", "").rstrip("/")
ADMIN_TOKEN     = os.environ.get("ADMIN_TOKEN", "")
BATCH_SIZE      = int(os.environ.get("BATCH_SIZE", "10"))
REQUEST_TIMEOUT = 30

if not GALLERY_URL:
    sys.exit("ERROR: GALLERY_URL env var is required")
if not ADMIN_TOKEN:
    sys.exit("ERROR: ADMIN_TOKEN env var is required")


def _resolve_url(url: str) -> str:
    """Try http and https, return whichever responds."""
    candidates = [url]
    if url.startswith("https://"):
        candidates.append("http://" + url[8:])
    else:
        candidates.append("https://" + url[7:])
    for candidate in candidates:
        try:
            r = requests.get(
                f"{candidate}/api.php",
                params={"action": "faces-data"},
                headers={"User-Agent": "Mozilla/5.0 (compatible; FaceScanner/1.0)"},
                timeout=10,
            )
            if r.status_code < 500:
                log.info("Gallery reachable at %s", candidate)
                return candidate
        except Exception:
            pass
    sys.exit(f"ERROR: Could not reach gallery at {url}")


GALLERY_URL = _resolve_url(GALLERY_URL)

HEADERS = {
    "X-Admin-Token": ADMIN_TOKEN,
    "Content-Type": "application/json",
    "Accept": "application/json, text/plain, */*",
    "User-Agent": "Mozilla/5.0 (compatible; FaceScanner/1.0)",
}
GET_HEADERS = {
    "Accept": "application/json, text/plain, */*",
    "User-Agent": "Mozilla/5.0 (compatible; FaceScanner/1.0)",
}

# ---------------------------------------------------------------------------
# InsightFace initialisation (once, at module level)
# ---------------------------------------------------------------------------
log.info("Initialising ArcFace model (buffalo_l)…")
_face_app = FaceAnalysis(name="buffalo_l", providers=["CPUExecutionProvider"])
_face_app.prepare(ctx_id=0, det_size=(640, 640))
log.info("ArcFace model ready")

# ---------------------------------------------------------------------------
# Gallery API helpers
# ---------------------------------------------------------------------------

def api_get(action: str, **params):
    r = requests.get(
        f"{GALLERY_URL}/api.php",
        params={"action": action, **params},
        headers=GET_HEADERS,
        timeout=REQUEST_TIMEOUT,
    )
    r.raise_for_status()
    return r.json()


def api_post(action: str, payload: dict):
    r = requests.post(
        f"{GALLERY_URL}/api.php",
        params={"action": action},
        json=payload,
        headers=HEADERS,
        timeout=REQUEST_TIMEOUT,
    )
    r.raise_for_status()
    return r.json()


def fetch_thumb_image(rel_path: str):
    """Return a PIL Image (RGB) for the given relative path, or None on error."""
    try:
        r = requests.get(
            f"{GALLERY_URL}/api.php",
            params={"action": "thumb", "path": rel_path},
            headers=GET_HEADERS,
            timeout=REQUEST_TIMEOUT,
        )
        r.raise_for_status()
        img = Image.open(BytesIO(r.content)).convert("RGB")
        return img
    except Exception as e:
        log.warning("Could not fetch thumb for %s: %s", rel_path, e)
        return None


# ---------------------------------------------------------------------------
# Face detection — ArcFace 512-dim L2-normalised embeddings
# ---------------------------------------------------------------------------

def detect_faces(pil_image: Image.Image) -> list[dict]:
    """
    Run InsightFace ArcFace on a PIL Image (RGB).

    Returns list of dicts:
      {
        "id":        "f_<hex>",
        "embedding": [512 floats, L2-normalised],
        "rect":      {"x": int, "y": int, "width": int, "height": int}
      }

    InsightFace uses BGR internally, so we convert before passing.
    Bounding box comes back as [x1, y1, x2, y2].
    """
    img_bgr = cv2.cvtColor(np.array(pil_image), cv2.COLOR_RGB2BGR)
    faces   = _face_app.get(img_bgr)

    results = []
    for face in faces:
        bbox = face.bbox.astype(int)      # [x1, y1, x2, y2]
        x1, y1, x2, y2 = bbox
        results.append({
            "id":        "f_" + uuid.uuid4().hex[:12],
            "embedding": face.embedding.tolist(),   # 512 floats
            "rect": {
                "x":      int(x1),
                "y":      int(y1),
                "width":  int(x2 - x1),
                "height": int(y2 - y1),
            },
        })
    return results


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    # 1. Fetch current faces.json state
    log.info("Fetching current faces data from gallery…")
    try:
        faces_data = api_get("faces-data")
    except Exception as e:
        sys.exit(f"ERROR: Could not reach gallery: {e}")

    already_scanned = set(faces_data.get("scanned", []))

    # Warn if existing embeddings are 128-dim (old dlib worker)
    existing_faces = faces_data.get("faces", [])
    if existing_faces:
        sample_dim = len(existing_faces[0].get("embedding", []))
        if sample_dim == 128:
            log.warning(
                "WARNING: faces.json contains 128-dim embeddings (old dlib worker). "
                "Clustering will not work correctly with mixed embedding sizes. "
                "Recommend: Reset face data from the Faces panel, then re-run this scan."
            )
        elif sample_dim == 512:
            log.info("Existing embeddings are 512-dim (ArcFace) — compatible.")

    log.info("Already scanned: %d images", len(already_scanned))

    # 2. Fetch full file list
    log.info("Fetching file list…")
    try:
        files_resp = api_get("files")
    except Exception as e:
        sys.exit(f"ERROR: Could not fetch file list: {e}")

    if isinstance(files_resp, list):
        all_files = [f["rel"] if isinstance(f, dict) else f for f in files_resp]
    elif isinstance(files_resp, dict):
        raw = (
            files_resp.get("files")
            or files_resp.get("data")
            or files_resp.get("images")
            or []
        )
        all_files = [f["rel"] if isinstance(f, dict) else f for f in raw]
    else:
        all_files = []

    IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".gif", ".webp"}
    pending = [
        f for f in all_files
        if os.path.splitext(f)[1].lower() in IMAGE_EXTS
        and f not in already_scanned
    ]

    log.info("Total files: %d | Pending scan: %d", len(all_files), len(pending))

    if not pending:
        log.info("Nothing to scan. All done.")
        return

    # 3. Scan each pending image and POST results in batches
    batch_faces   = []
    batch_scanned = []
    total_faces   = 0
    errors        = 0

    for i, rel_path in enumerate(pending, 1):
        log.info("[%d/%d] %s", i, len(pending), rel_path)

        img = fetch_thumb_image(rel_path)
        if img is None:
            errors += 1
            batch_scanned.append(rel_path)   # still mark scanned to avoid endless retry
        else:
            faces = detect_faces(img)
            total_faces += len(faces)
            log.info("  → %d face(s) found", len(faces))
            for face in faces:
                face["rel"] = rel_path
                batch_faces.append(face)
            batch_scanned.append(rel_path)

        if len(batch_scanned) >= BATCH_SIZE or i == len(pending):
            log.info(
                "  Posting batch (%d scanned, %d faces)…",
                len(batch_scanned),
                len(batch_faces),
            )
            try:
                resp = api_post("faces-store", {
                    "faces":   batch_faces,
                    "scanned": batch_scanned,
                })
                log.info("  API response: %s", resp)
            except Exception as e:
                log.error("  FAILED to post batch: %s", e)
                errors += 1

            batch_faces   = []
            batch_scanned = []

    log.info(
        "Done. Scanned %d images, found %d faces, %d errors.",
        len(pending),
        total_faces,
        errors,
    )


if __name__ == "__main__":
    main()
