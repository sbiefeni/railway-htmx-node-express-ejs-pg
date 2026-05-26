"""
Face scanning worker for the PHP photo gallery.

Env vars required:
  GALLERY_URL   — e.g. https://biefeni.com (no trailing slash)
  ADMIN_TOKEN   — sha256 hex token (same value stored in localStorage bfAdminToken)

Run: python scan.py
"""

import os
import sys
import uuid
import time
import json
import math
import logging
import requests
import numpy as np
from io import BytesIO
from PIL import Image
import face_recognition

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
GALLERY_URL = os.environ.get("GALLERY_URL", "").rstrip("/")
ADMIN_TOKEN = os.environ.get("ADMIN_TOKEN", "")
BATCH_SIZE  = int(os.environ.get("BATCH_SIZE", "10"))   # POST results every N images
REQUEST_TIMEOUT = 30  # seconds

if not GALLERY_URL:
    sys.exit("ERROR: GALLERY_URL env var is required")
if not ADMIN_TOKEN:
    sys.exit("ERROR: ADMIN_TOKEN env var is required")

HEADERS = {"X-Admin-Token": ADMIN_TOKEN, "Content-Type": "application/json"}


# ---------------------------------------------------------------------------
# Gallery API helpers
# ---------------------------------------------------------------------------

def api_get(action, **params):
    url = f"{GALLERY_URL}/api.php"
    r = requests.get(url, params={"action": action, **params}, timeout=REQUEST_TIMEOUT)
    r.raise_for_status()
    return r.json()


def api_post(action, payload):
    url = f"{GALLERY_URL}/api.php"
    r = requests.post(url, params={"action": action}, json=payload,
                      headers=HEADERS, timeout=REQUEST_TIMEOUT)
    r.raise_for_status()
    return r.json()


def fetch_thumb_image(rel_path):
    """Return a PIL Image (RGB) for the given relative path, or None on error."""
    url = f"{GALLERY_URL}/api.php"
    try:
        r = requests.get(url, params={"action": "thumb", "path": rel_path},
                         timeout=REQUEST_TIMEOUT)
        r.raise_for_status()
        img = Image.open(BytesIO(r.content)).convert("RGB")
        return img
    except Exception as e:
        log.warning("Could not fetch thumb for %s: %s", rel_path, e)
        return None


# ---------------------------------------------------------------------------
# Face detection
# ---------------------------------------------------------------------------

def detect_faces(pil_image):
    """
    Run face_recognition on a PIL Image.

    Returns list of dicts:
      { "id": "f_xxx", "embedding": [128 floats], "rect": {x, y, width, height} }

    face_recognition locations are (top, right, bottom, left).
    Gallery rect is {x, y, width, height} in pixel coords (origin top-left).
    """
    img_array = np.array(pil_image)

    locations  = face_recognition.face_locations(img_array, model="hog")
    encodings  = face_recognition.face_encodings(img_array, locations)

    results = []
    for loc, enc in zip(locations, encodings):
        top, right, bottom, left = loc
        results.append({
            "id":        "f_" + uuid.uuid4().hex[:12],
            "embedding": enc.tolist(),          # 128 floats — same dims as face-api.js
            "rect": {
                "x":      left,
                "y":      top,
                "width":  right - left,
                "height": bottom - top,
            },
        })
    return results


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    # 1. Fetch current faces.json state (already-scanned paths)
    log.info("Fetching current faces data from gallery…")
    try:
        faces_data = api_get("faces-data")
    except Exception as e:
        sys.exit(f"ERROR: Could not reach gallery at {GALLERY_URL}: {e}")

    already_scanned = set(faces_data.get("scanned", []))
    log.info("Already scanned: %d images", len(already_scanned))

    # 2. Fetch full file list
    log.info("Fetching file list…")
    try:
        files_resp = api_get("files")
    except Exception as e:
        sys.exit(f"ERROR: Could not fetch file list: {e}")

    # api.php?action=files may return a list directly or wrapped in a key
    if isinstance(files_resp, list):
        all_files = files_resp
    elif isinstance(files_resp, dict):
        # Try common wrapper keys
        all_files = (files_resp.get("files")
                     or files_resp.get("data")
                     or files_resp.get("images")
                     or [])
    else:
        all_files = []

    # Filter to image files not yet scanned
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
            # Still mark as scanned so we don't retry endlessly
            batch_scanned.append(rel_path)
        else:
            faces = detect_faces(img)
            total_faces += len(faces)
            log.info("  → %d face(s) found", len(faces))

            for face in faces:
                face["rel"] = rel_path
                batch_faces.append(face)
            batch_scanned.append(rel_path)

        # POST batch when full or on the last image
        if len(batch_scanned) >= BATCH_SIZE or i == len(pending):
            log.info("  Posting batch (%d scanned, %d faces)…",
                     len(batch_scanned), len(batch_faces))
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

    log.info("Done. Scanned %d images, found %d faces, %d errors.",
             len(pending), total_faces, errors)


if __name__ == "__main__":
    main()
