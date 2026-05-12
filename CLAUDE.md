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
  routes/
    webhook.js      — Voiceflow webhook receiver + inspection endpoints
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

## Planned architecture (not yet built)

### Call flow
1. Call arrives → Voiceflow fires `runtime.call.start` webhook
2. Server stores short-lived mapping: `userNumber → agentNumber` (in-memory Map, TTL 120s)
3. Voiceflow hits `GET /api/greeting?userNumber=...` → server resolves client via agentNumber, returns greeting text
4. Voiceflow hits `GET /api/client-data?userNumber=...` → returns full client config
5. Call ends → `runtime.call.end` webhook → server cleans up Map entry

### TODO: in-memory call mapping (next thing to build)
When the `runtime.call.start` webhook arrives, we know both `userNumber` (caller) and `agentNumber` (which Twilio number was called — identifies the business). We store this pair in a short-lived in-memory Map:

```
callMap.set(userNumber, agentNumber)  // TTL 120s
```

When Voiceflow makes mid-conversation API calls (`/api/greeting`, `/api/client-data`), it only passes `userNumber`. The server uses the Map to resolve `agentNumber`, then looks up the matching client in the `clients` table to return the right business data.

**Why in-memory Map (not Redis):**
- We run a single Railway container — no cross-instance sharing needed
- The webhook → API call gap is only 1–3 seconds — no persistence needed
- Zero extra cost or infrastructure
- Redis is a future upgrade if we ever need zero-downtime deploys or horizontal scaling
- The Map module should be written so swapping to Redis is a one-file change

**Anonymous/blocked caller edge case:**
When caller ID is blocked, `userNumber` arrives as `anonymous` or empty. Multiple blocked callers could collide on the same Map key. Handle with a FIFO queue keyed on `anonymous` — webhook pushes to the queue, API call pops the oldest entry. Low priority until call volume warrants it.

### Anonymous caller edge case
When caller ID is blocked, `userNumber` is `anonymous` or empty. Handle with a FIFO queue keyed on `anonymous` to avoid collisions between concurrent blocked callers.

### Tables to add
- `clients` — name, agent_number (Twilio number), greeting, services (JSONB), hours (JSONB), staff (JSONB), custom_prompt, password_hash
- Auth: session-based (express-session + bcrypt), one login per client

### Routes to add
- `GET /api/greeting?userNumber=...` — returns personalized greeting for the matched client
- `GET /api/client-data?userNumber=...` — returns full client config
- `GET|POST /portal/*` — client-facing web UI (EJS + HTMX) for managing their own data

---

## Inspection endpoints (dev/debug)

- `GET /webhook/events/calls` — last 50 call events as JSON
- `GET /webhook/events/sessions` — last 50 session events as JSON
- `GET /health` — confirms Postgres connectivity

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
