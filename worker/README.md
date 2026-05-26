# Face Scan Worker

Server-side face scanning for the PHP gallery. Uses Python `face_recognition` (dlib) — 10–20× faster than browser TF.js, same 128-dim embeddings and 0.6 Euclidean threshold.

## How it works

1. GETs `api.php?action=faces-data` — reads already-scanned paths (resume support)
2. GETs `api.php?action=files` — full image file list
3. For each unscanned image: fetches thumb, detects faces, accumulates results
4. POSTs batches to `api.php?action=faces-store` with `X-Admin-Token` header
5. Exits when done (one-shot, not a persistent server)

## Railway deployment

### 1. Add as a new service in your Railway project

- In Railway dashboard → your project → **New Service → GitHub Repo**
- Select this repo
- Set **Root Directory** to `worker`
- Railway will detect nixpacks and build from `requirements.txt`

### 2. Set environment variables

| Variable | Value |
|---|---|
| `GALLERY_URL` | `https://biefeni.com` (no trailing slash) |
| `ADMIN_TOKEN` | sha256 hex token (same as `bfAdminToken` in browser localStorage) |
| `BATCH_SIZE` | `10` (optional, default 10 — images per POST batch) |

### 3. Deploy / re-run

- The worker exits after one full scan
- To re-run: trigger a manual deploy in Railway dashboard
- Already-scanned images are skipped automatically (resume support)

## Notes

- **First deploy is slow** — dlib compiles from source, takes 5–10 minutes. Subsequent deploys use the build cache.
- **RAM:** ~400 MB during scan — within Railway hobby tier limits
- **`api.php?action=files`** must return a JSON array of relative image paths (strings). If your gallery api.php doesn't expose this action yet, add it — see the PHP snippet below.

### PHP snippet for `api.php?action=files` (if not already present)

```php
case 'files':
    // Return all image paths relative to GALLERY_ROOT, recursively
    $images = [];
    $exts   = ['jpg', 'jpeg', 'png', 'gif', 'webp'];
    $iter   = new RecursiveIteratorIterator(
        new RecursiveDirectoryIterator(GALLERY_ROOT, FilesystemIterator::SKIP_DOTS)
    );
    foreach ($iter as $file) {
        if (in_array(strtolower($file->getExtension()), $exts)) {
            $rel = ltrim(str_replace(GALLERY_ROOT, '', $file->getPathname()), '/\\');
            $images[] = str_replace('\\', '/', $rel);
        }
    }
    echo json_encode($images);
    exit;
```
