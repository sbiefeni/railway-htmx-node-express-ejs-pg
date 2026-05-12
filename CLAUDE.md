# Voiceflow Multi-Tenant Backend — Project Guide for Claude

## What this is

A Node.js/Express backend hosted on Railway that acts as the central server for a multi-tenant AI voice agent business. The product sells AI phone agents to small service businesses (salons, trades, etc.). One Voiceflow project handles all customers; this server provides the glue layer between Voiceflow, Twilio, and each client's data.

---

## Business model summary

- One Voiceflow project with all Twilio numbers connected inside it
- When a call comes in, Voiceflow fires a webhook to this server
- This server identifies which client (business) was called via the Twilio number (`agentNumber`)
- Voiceflow then makes API calls to this server mid-conversation to get the client's personalized greeting and full config (services, hours, staff, custom prompt)
- Each client has a portal login to manage their own data

---

## Hosting & infrastructure

- **Platform:** Railway (railway.app)
- **Public URL:** `https://web-xs17-production.up.railway.app`
- **Database:** Postgres (Railway managed, `Postgres-3g9G` service)
- **GitHub repo:** `https://github.com/sbiefeni/railway-htmx-node-express-ejs-pg.git`
- **Branch:** `master`
- **Deploy:** Auto-deploys on push to `master`
- **Port:** `process.env.PORT` (Railway sets this automatically, defaults to 8080)

### Git workflow note
The `.git` folder on the Windows mount has filesystem locking issues — git commands fail when run against the mounted path from the Linux sandbox. Workaround: copy changed files into `/tmp/repo_clone`, commit and push from there. Or have the user run git commands locally in PowerShell using the token auth pattern:
```
git push https://sbiefeni:<TOKEN>@github.com/sbiefeni/railway-htmx-node-express-ejs-pg.git master
```

---

## Stack

- **Runtime:** Node.js (v22)
- **Framework:** Express v5
- **Templating:** EJS (for client portal UI)
- **Frontend progressivity:** HTMX (for portal partial updates)
- **Database:** PostgreSQL via `pg` (node-postgres)
- **Styling:** Tailwind CSS (CDN)
- **No build step** — all JS/HTML edits are live on redeploy

---

## File map

```
src/
  index.js          — Express app entry point, middleware, route mounting, server start
  database.js       — pg Pool + migrate() function (runs CREATE TABLE IF NOT EXISTS on startup)
  callMap.js        — In-memory userNumber → agentNumber map (TTL 120s, module singleton)
  routes/
    webhook.js      — Voiceflow webhook receiver, callMap updates, inspection endpoints
test/
  callMap.test.js   — Unit tests for callMap (run with: node test/callMap.test.js)
views/
  home.ejs          — (legacy todo UI, to be replaced with client portal)
  partials/
    todo-item.ejs   — (legacy, to be removed)
Dockerfile          — Multi-stage Node 22 Alpine build, exposes 8080
package.json        — dependencies: express, ejs, pg
```

---

## Database schema

### `call_events`
Stores `runtime.call.start` and `runtime.call.end` events from Voiceflow.

| Column | Type | Notes |
|--------|------|-------|
| id | SERIAL PK | |
| type | TEXT | `runtime.call.start` or `runtime.call.end` |
| call_sid | TEXT | Twilio call SID (e.g. `CAxxxxx`) |
| user_number | TEXT | Caller's phone number |
| agent_number | TEXT | Twilio number that was called — identifies the client |
| raw_payload | JSONB | Full webhook payload |
| received_at | TIMESTAMPTZ | Default NOW() |

### `session_events`
Stores `runtime.session.start` and `runtime.session.end` events from Voiceflow.

| Column | Type | Notes |
|--------|------|-------|
| id | SERIAL PK | |
| type | TEXT | `runtime.session.start` or `runtime.session.end` |
| user_number | TEXT | Caller's phone number |
| raw_payload | JSONB | Full webhook payload |
| received_at | TIMESTAMPTZ | Default NOW() |

---

## Voiceflow webhook

### What Voiceflow sends
Voiceflow is configured to POST all session lifecycle events to a single URL:
```
POST https://web-xs17-production.up.railway.app/webhook/voiceflow
```

### Example `runtime.call.start` payload
```json
{
  "type": "runtime.call.start",
  "data": {
    "userID": "+19876543210",
    "environmentID": "69f3df348d8088f34a622739",
    "projectID": "6772da2e485189279fa5b9da",
    "startTime": 1743563467788,
    "platform": "twilio",
    "metadata": {
      "callSid": "CA01ed76f14fee29c50f5de59400474006",
      "callType": "inbound",
      "userNumber": "+19876543210",
      "agentNumber": "+17782006110"
    }
  },
  "time": 1743563467874,
  "resource": "project-6772da2e485189279fa5b9da"
}
```

### Event routing (inside the server)
The single webhook endpoint routes events by `type`:
- `runtime.call.start` / `runtime.call.end` → `call_events` table
- `runtime.session.start` / `runtime.session.end` → `session_events` table
- Unknown types → logged as warning, not stored

### Key field extraction
- `agentNumber` (which Twilio number was called) → identifies the client/business
- `userNumber` / `userID` → the caller's phone number
- `callSid` → Twilio call identifier

### Voiceflow webhook secret
Voiceflow provides a webhook secret in project Settings → Behavior → Session lifecycle webhook. This should be used to verify incoming requests are genuinely from Voiceflow (not yet implemented — planned).

---

## Architecture

### Call flow
1. Call arrives → Voiceflow fires `runtime.call.start` webhook
2. Server stores short-lived mapping: `userNumber → agentNumber` in `callMap` (TTL 120s) ✓
3. Voiceflow hits `GET /api/greeting?userNumber=...` → server resolves client via agentNumber, returns greeting text
4. Voiceflow hits `GET /api/client-data?userNumber=...` → returns full client config
5. Call ends → `runtime.call.end` webhook → server cleans up callMap entry ✓

### In-memory call map (`src/callMap.js`) ✓

`callMap` is a Node.js module singleton — the same `Map` instance is shared across all route files because Node caches modules after first load.

**API:**
- `callMap.set(userNumber, agentNumber)` — store mapping, arm 120s TTL timer
- `callMap.get(userNumber)` — returns `agentNumber` or `null` if not found
- `callMap.del(userNumber)` — remove entry and cancel its timer
- `callMap.snapshot()` — returns plain object copy of current map (for debug)
- `callMap.size()` — number of live entries

**Lifecycle:**
- `runtime.call.start` webhook → `callMap.set(userNumber, agentNumber)`
- `runtime.call.end` webhook → `callMap.del(userNumber)`
- TTL fires after 120s if `call.end` is never received (guards against dropped webhooks)
- Server restart wipes the map — any call in progress at deploy time loses its mapping. Acceptable for now; Redis is the upgrade path if zero-downtime deploys become a requirement.

**Why in-memory Map (not Redis):**
- Single Railway container — no cross-instance sharing needed
- Webhook → API call gap is only 1–3 seconds — no persistence needed
- Zero extra cost or infrastructure
- `callMap.js` is designed so swapping to Redis is a one-file change

**Anonymous/blocked caller edge case (not yet built):**
When caller ID is blocked, `userNumber` arrives as `anonymous` or empty. Multiple blocked callers could collide on the same Map key. Handle with a FIFO queue keyed on `anonymous` — webhook pushes to the queue, API call pops the oldest entry. Low priority until call volume warrants it.

---

## Terminology

**Caller / user** — the end customer who phones the business. Identified by `userNumber` (their phone number). They never interact with this server directly; they only talk to the Voiceflow agent.

**Agent / client** — OUR customer. The small business (salon, tradesperson, etc.) that pays for the AI phone service. Identified by `agentNumber` (the Twilio number assigned to them). They have an account on this system and log into the portal to manage their own data.

The naming in the codebase follows this convention: `userNumber` = caller's phone, `agentNumber` = the business's Twilio number.

---

## TODO

### TODO: Terminology audit
Review all existing variable names, comments, and DB column names to make sure they consistently use `user` for caller and `agent`/`client` for the business owner. The `clients` table uses "client" which is fine — just needs to stay consistent with `agentNumber` in the callMap and call_events.

### TODO: `clients` table
The core record for each business that uses the service.

Shape is not fully locked in yet, but expected columns:
- `id` — SERIAL PK
- `agent_number` — TEXT, the Twilio number assigned to this business (matches `agentNumber` in callMap)
- `name` — TEXT, business name
- `greeting` — TEXT, the opening line the AI speaks at the start of each call
- `agent_instructions` — TEXT, full system prompt / persona for the AI agent
- `services` — JSONB, list of services offered
- `hours` — JSONB, opening hours
- `staff` — JSONB, staff names / roles (if relevant)
- `password_hash` — TEXT, bcrypt hash for portal login
- `created_at` — TIMESTAMPTZ

Exact shape should be decided before building the portal. Consider whether `services`, `hours`, and `staff` should be flattened into `agent_instructions` or kept as structured data for the portal UI to edit independently.

### TODO: API endpoint — initial greeting
```
GET /api/greeting?userNumber=+1...
```
Called by Voiceflow at the very start of a call, before the agent speaks.

Flow: look up `agentNumber` from callMap using `userNumber` → query `clients` table by `agent_number` → return `{ greeting: "..." }`.

Needs to handle: userNumber not in callMap (call.start webhook not yet received), no matching client for the agentNumber.

### TODO: API endpoint — full agent data
```
GET /api/client-data?userNumber=+1...
```
Called by Voiceflow mid-conversation to get the full config for the agent.

Flow: same callMap lookup → return full client record (or a shaped subset) as JSON so Voiceflow can inject it into the agent's context.

Exact response shape TBD — depends on what Voiceflow's "Set Variable" block can consume. Likely a flat JSON object.

### TODO: Webhook signature verification (security)
Voiceflow signs outgoing webhooks with the secret shown in project Settings → Behavior → Session lifecycle webhook (confirmed visible in UI, value is set). The secret needs to be copied out of Voiceflow and added as a Railway environment variable.

**How it works:**
Voiceflow computes an HMAC-SHA256 of the raw request body using the secret and sends the result in a request header. The server recomputes the same HMAC and compares — if they don't match, the request is rejected.

**Header to check:** Likely `x-vf-signature` or `x-voiceflow-signature` — confirm by logging all headers on a real incoming webhook before implementing the check.

**Implementation plan:**
1. Copy the webhook secret from Voiceflow UI → add to Railway as env var `VOICEFLOW_WEBHOOK_SECRET`
2. Use `express.raw({ type: 'application/json' })` on the webhook route (instead of `express.json()`) so we get the raw body bytes needed for HMAC — re-parse to JSON manually after verification
3. Write a middleware function `verifyVoiceflowSignature(req, res, next)` in `src/routes/webhook.js`:
   - Read `VOICEFLOW_WEBHOOK_SECRET` from env
   - Compute `hmac = HMAC-SHA256(rawBody, secret)` using Node's built-in `crypto` module (no extra dependency)
   - Compare against the signature header using `crypto.timingSafeEqual` (prevents timing attacks)
   - If mismatch or header missing → `res.status(401).json({ error: 'invalid signature' })`
4. Apply the middleware only to `POST /webhook/voiceflow` — not the debug GET endpoints
5. If `VOICEFLOW_WEBHOOK_SECRET` is not set in env, log a warning at startup and skip verification (so dev environments without the secret still work)

**No extra dependencies needed** — Node's built-in `crypto` module handles HMAC-SHA256.

### TODO: Portal (client login + data management)
- Auth: session-based login (express-session + bcrypt), one account per client
- `GET|POST /portal/login`
- `GET /portal/dashboard` — view/edit greeting, instructions, services, hours, staff
- All portal UI via EJS + HTMX partial updates

---

## Inspection endpoints (dev/debug)

- `GET /webhook/events/calls` — last 50 call events as JSON
- `GET /webhook/events/sessions` — last 50 session events as JSON
- `GET /webhook/callmap` — current in-memory call map (size + all live entries)
- `GET /health` — confirms Postgres connectivity

### Making test requests from PowerShell
PowerShell's `curl` is an alias for `Invoke-WebRequest`, not real curl. Use this form:
```powershell
Invoke-WebRequest -Method POST -Uri "https://web-xs17-production.up.railway.app/webhook/voiceflow" -ContentType "application/json" -Body '{"type":"test"}'
```

---

## Environment variables

| Variable | Description |
|----------|-------------|
| `DATABASE_URL` | Postgres connection string (set automatically by Railway) |
| `PORT` | Server port (set automatically by Railway) |

---

## Conventions

- `migrate()` in `database.js` runs on every startup using `CREATE TABLE IF NOT EXISTS` — safe to run repeatedly
- All route files go in `src/routes/`
- Express middleware order: `express.json()` → `express.urlencoded()` → routes
- Railway auto-deploys on every push to `master`
