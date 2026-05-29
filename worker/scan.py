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

# Cap CPU threads before any ML libraries load — keeps usage within the 8 vCPU plan.
# Increase or remove if you want faster scans at the cost of potential overage.
_CPU_LIMIT = os.environ.get("CPU_THREAD_LIMIT", "8")
os.environ.setdefault("OMP_NUM_THREADS",      _CPU_LIMIT)
os.environ.setdefault("MKL_NUM_THREADS",      _CPU_LIMIT)
os.environ.setdefault("OPENBLAS_NUM_THREADS", _CPU_LIMIT)

import sys
import uuid
import logging
import requests
import numpy as np
from io import BytesIO
from PIL import Image
import cv2
from insightface.app import FaceAnalysis
from sklearn.cluster import AgglomerativeClustering

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


def api_get_auth(action: str, timeout: int = REQUEST_TIMEOUT, **params):
    """Authenticated GET — used for superadmin-only read endpoints."""
    r = requests.get(
        f"{GALLERY_URL}/api.php",
        params={"action": action, **params},
        headers=HEADERS,
        timeout=timeout,
    )
    r.raise_for_status()
    return r.json()


def api_post(action: str, payload: dict, timeout: int = REQUEST_TIMEOUT):
    r = requests.post(
        f"{GALLERY_URL}/api.php",
        params={"action": action},
        json=payload,
        headers=HEADERS,
        timeout=timeout,
    )
    r.raise_for_status()
    return r.json()


def check_stop() -> bool:
    """Returns True if the UI has requested a stop via faces-stop.flag."""
    try:
        r = requests.get(
            f"{GALLERY_URL}/api.php",
            params={"action": "faces-stop-check"},
            headers=GET_HEADERS,
            timeout=10,
        )
        return r.json().get("stop", False)
    except Exception:
        return False  # if check fails, keep going


def fetch_thumb_image(rel_path: str):
    """Return a PIL Image (RGB) for the given relative path, or None on error."""
    try:
        r = requests.get(
            f"{GALLERY_URL}/api.php",
            params={"action": "thumb", "path": rel_path, "size": "scan"},
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
            "embedding": face.normed_embedding.tolist(),   # 512 floats
            "rect": {
                "x":      int(x1),
                "y":      int(y1),
                "width":  int(x2 - x1),
                "height": int(y2 - y1),
            },
        })
    return results


# ---------------------------------------------------------------------------
# Potentially-<Name> matcher
# ---------------------------------------------------------------------------
# For each new unnamed cluster, check if it looks like a near-match to any
# existing NAMED group. If so, attach suggestedName/suggestedGroupId fields so
# the UI can render the cluster as "Potentially <Name>" next to that named
# group. This is advisory only — admins still confirm by merging or renaming.
#
# Rule: for each cluster face, find its nearest anchor face in the named group;
# require >= CONSENSUS_FRAC of cluster faces to have NN distance below a stricter
# MATCH_THRESHOLD. This avoids "Potentially X is everyone": a handful of lucky
# low-distance pairs in a large cluster don't count — a meaningful fraction of
# the cluster must agree. Excluded faces (recorded via bulk-remove on the named
# group) are masked out of the cluster before computing.
# Best-only: among qualifying named groups, pick the one with the lowest mean
# NN-distance over the consenting cluster faces.

SUGGESTION_MIN_NAMED_SIZE  = 50
SUGGESTION_MATCH_THRESHOLD = 1.10
SUGGESTION_CONSENSUS_FRAC  = 0.25


def compute_suggestions(new_groups: list, named_groups: list, exclusions: dict,
                         embeddings_data: dict, threshold: float,
                         match_threshold: float = SUGGESTION_MATCH_THRESHOLD,
                         consensus_frac: float = SUGGESTION_CONSENSUS_FRAC,
                         min_named_size: int = SUGGESTION_MIN_NAMED_SIZE) -> None:
    """Mutates new_groups in place, adding suggestedName/suggestedGroupId.

    match_threshold, consensus_frac, min_named_size override the module
    defaults so the worker can pick them up from faces.json.config without
    a code push.
    """
    # Prebuild eligible named-group embedding matrices.
    candidates = []
    for ng in named_groups:
        name = ng.get("name") or ""
        if not name:
            continue
        face_ids = ng.get("faceIds", []) or []
        if len(face_ids) < min_named_size:
            continue
        emb_rows = [embeddings_data[fid] for fid in face_ids if fid in embeddings_data]
        if not emb_rows:
            continue
        excl = set(exclusions.get(ng["id"], []) or [])
        candidates.append({
            "id":   ng["id"],
            "name": name,
            "excl": excl,
            "emb":  np.array(emb_rows, dtype=np.float32),
        })

    if not candidates:
        log.info("Potentially-* matcher: no named groups with >= %d faces — nothing to suggest.",
                 min_named_size)
        return

    log.info(
        "Potentially-* matcher: %d eligible named group(s); scanning %d cluster(s) "
        "(match_threshold=%.2f, consensus=%.0f%%, min_named_size=%d)…",
        len(candidates), len(new_groups),
        match_threshold, consensus_frac * 100, min_named_size,
    )

    annotated = 0
    for g in new_groups:
        if g.get("name"):
            continue  # already named — nothing to suggest
        cluster_ids = g.get("faceIds", []) or []
        rows = [(fid, embeddings_data[fid]) for fid in cluster_ids if fid in embeddings_data]
        if not rows:
            continue
        kept_ids = [r[0] for r in rows]
        C_full   = np.array([r[1] for r in rows], dtype=np.float32)
        n_total  = len(kept_ids)  # consensus denominator = full cluster size (pre-exclusion)

        best = None  # (mean_nn_below, named_id, named_name)
        for cand in candidates:
            # Mask out cluster faces previously excluded from this named group.
            if cand["excl"]:
                mask = np.array([fid not in cand["excl"] for fid in kept_ids], dtype=bool)
                if not mask.any():
                    continue
                C = C_full[mask]
            else:
                C = C_full

            # L2-normalised embeddings: ||c - n|| = sqrt(2 - 2 c·n).
            dots = C @ cand["emb"].T                          # (|C|, |N|)
            D    = np.sqrt(np.clip(2.0 - 2.0 * dots, 0.0, None))
            nn   = D.min(axis=1)                              # nearest anchor per cluster face
            below = nn < match_threshold
            n_below = int(below.sum())
            # Consensus: fraction of the ORIGINAL cluster (pre-exclusion) that strongly matches.
            if n_below / n_total < consensus_frac:
                continue
            mean_nn = float(nn[below].mean())
            if best is None or mean_nn < best[0]:
                best = (mean_nn, cand["id"], cand["name"])

        if best is not None:
            g["suggestedName"]    = best[2]
            g["suggestedGroupId"] = best[1]
            annotated += 1

    log.info("Potentially-* matcher: attached suggestions to %d unnamed group(s).", annotated)


# ---------------------------------------------------------------------------
# Clustering — greedy centroid, numpy-accelerated
# ---------------------------------------------------------------------------

def cluster_faces(existing_groups: list, threshold: float) -> list:
    """
    Fetch all embeddings from the gallery, cluster orphaned faces using
    Agglomerative Hierarchical Clustering with average linkage.

    Why AHC with average linkage:
      - Order-independent (unlike greedy centroid)
      - No chaining effect (unlike DBSCAN) — merges clusters based on the
        average distance between all points in two clusters, not nearest neighbor
      - distance_threshold maps naturally to the UI threshold knob
      - Singleton clusters (size == 1) are excluded as noise

    Faces that end up alone in a cluster of 1 are silently dropped.
    """
    log.info("Fetching faces data for clustering…")
    faces_data = api_get("faces-data")
    faces      = faces_data.get("faces", [])

    if not faces:
        log.info("No faces found — skipping clustering.")
        return existing_groups

    log.info("Fetching embeddings (this may take a moment)…")
    embeddings_data = api_get_auth("faces-embeddings-fetch", timeout=120)

    if not embeddings_data:
        log.info("No embeddings found — skipping clustering.")
        return existing_groups

    # Build set of already-assigned face IDs
    assigned = {fid for g in existing_groups for fid in g.get("faceIds", [])}

    # Collect orphaned faces that have embeddings
    orphans = [f for f in faces if f["id"] not in assigned and f["id"] in embeddings_data]

    if not orphans:
        log.info("No orphaned faces to cluster — all faces already in groups.")
        return existing_groups

    log.info("Clustering %d orphaned faces (AHC average linkage, threshold=%.2f)…", len(orphans), threshold)

    ids        = [f["id"] for f in orphans]
    emb_matrix = np.array([embeddings_data[fid] for fid in ids], dtype=np.float32)

    model  = AgglomerativeClustering(
        n_clusters=None,
        distance_threshold=threshold,
        metric="euclidean",
        linkage="average",
    )
    labels = model.fit_predict(emb_matrix)

    # Group face IDs by cluster label
    clusters: dict[int, list] = {}
    for fid, label in zip(ids, labels):
        clusters.setdefault(int(label), []).append(fid)

    # Exclude singleton clusters (lone faces with no match = noise)
    new_groups = [
        {"id": "g_" + uuid.uuid4().hex[:12], "name": "", "faceIds": fids}
        for fids in clusters.values()
        if len(fids) >= 2
    ]

    noise_count = sum(1 for fids in clusters.values() if len(fids) < 2)
    log.info(
        "Clustering complete: %d group(s) from %d face(s), %d singleton(s) excluded as noise.",
        len(new_groups), len(orphans) - noise_count, noise_count,
    )

    # Annotate new unnamed clusters with Potentially-<Name> suggestions where they
    # closely match an existing named group. faces_data is the source of truth
    # for named groups (it merges faces-named.json) and groupExclusions.
    named_groups = [g for g in faces_data.get("groups", []) if g.get("name")]
    exclusions   = faces_data.get("groupExclusions", {}) or {}
    if isinstance(exclusions, list):
        exclusions = {}  # PHP may serialise empty assoc array as []
    # Matcher knobs come from faces.json.config when present (tunable from the
    # UI's collapsible Tuning row), falling back to module defaults otherwise.
    cfg = faces_data.get("config", {}) or {}
    try:
        match_threshold = float(cfg.get("matchThreshold", SUGGESTION_MATCH_THRESHOLD))
        consensus_frac  = float(cfg.get("consensusFrac",  SUGGESTION_CONSENSUS_FRAC))
        min_named_size  = int  (cfg.get("minNamedSize",   SUGGESTION_MIN_NAMED_SIZE))
    except (TypeError, ValueError):
        match_threshold = SUGGESTION_MATCH_THRESHOLD
        consensus_frac  = SUGGESTION_CONSENSUS_FRAC
        min_named_size  = SUGGESTION_MIN_NAMED_SIZE
    try:
        compute_suggestions(new_groups, named_groups, exclusions, embeddings_data, threshold,
                            match_threshold=match_threshold,
                            consensus_frac=consensus_frac,
                            min_named_size=min_named_size)
    except Exception as e:
        log.warning("Potentially-* matcher failed (non-fatal): %s", e)

    return existing_groups + new_groups


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    # 1. Fetch current faces.json state + config
    log.info("Fetching current faces data from gallery…")
    try:
        faces_data = api_get("faces-data")
    except Exception as e:
        sys.exit(f"ERROR: Could not reach gallery: {e}")

    config         = faces_data.get("config", {})
    cluster_only   = bool(config.get("clusterOnly", False))
    threshold      = float(config.get("threshold", 1.0))
    existing_groups = faces_data.get("groups", [])

    log.info(
        "Mode: %s | Threshold: %.2f",
        "cluster-only" if cluster_only else "full scan + cluster",
        threshold,
    )

    stopped = False

    if not cluster_only:
        already_scanned = set(faces_data.get("scanned", []))

        # 2. Fetch full file list
        log.info("Fetching file list…")
        try:
            files_resp = api_get("files", excludeTrash=1)
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

        log.info("Total files: %d | Already scanned: %d | Pending: %d",
                 len(all_files), len(already_scanned), len(pending))

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
                batch_scanned.append(rel_path)   # mark scanned to avoid endless retry
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
                    len(batch_scanned), len(batch_faces),
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

                if check_stop():
                    log.info("Stop requested by UI — skipping clustering.")
                    stopped = True
                    break

        log.info(
            "Scan done. Scanned %d images, found %d faces, %d errors.",
            len(pending), total_faces, errors,
        )

    # 4. Cluster — skip only if the user stopped the scan mid-way
    if stopped:
        log.info("Scan was stopped — clustering skipped.")
        return

    log.info("Starting clustering…")
    try:
        # In cluster-only mode, treat NAMED groups as fixed — keep their faces out
        # of the orphan pool so they aren't re-clustered into duplicate "Potentially
        # <Name>" clusters that mirror themselves. Unnamed groups are rebuilt.
        # In full-scan mode, pass existing_groups (named + unnamed) so previously-
        # assigned faces aren't re-clustered — only newly-scanned faces are.
        if cluster_only:
            groups_for_clustering = [g for g in existing_groups if g.get("name")]
            log.info("Cluster-only mode: preserving %d named group(s); re-clustering everything else.",
                     len(groups_for_clustering))
        else:
            groups_for_clustering = existing_groups
        all_groups = cluster_faces(groups_for_clustering, threshold)
        resp = api_post("faces-cluster-store", {"groups": all_groups}, timeout=60)
        log.info("Clustering stored — %d total group(s). Response: %s", len(all_groups), resp)
    except Exception as e:
        log.error("Clustering failed: %s", e)


if __name__ == "__main__":
    main()
