const express = require("express");
const { Webhook } = require("svix");
const { pool } = require("../database");
const callMap  = require("../callMap");

const router = express.Router();

const CALL_TYPES    = new Set(["runtime.call.start", "runtime.call.end"]);
const SESSION_TYPES = new Set(["runtime.session.start", "runtime.session.end"]);

/**
 * Svix signature verification middleware.
 * Applied only to POST /webhook/voiceflow.
 *
 * If VOICEFLOW_WEBHOOK_SECRET is not set, logs a warning and passes through
 * (allows local dev without the secret).
 *
 * Rejects with 401 if the signature is missing or invalid.
 */
function verifyVoiceflowSignature(req, res, next) {
  const secret = process.env.VOICEFLOW_WEBHOOK_SECRET;

  if (!secret) {
    console.warn("[webhook] VOICEFLOW_WEBHOOK_SECRET not set — skipping signature verification");
    return next();
  }

  const wh = new Webhook(secret);

  try {
    // wh.verify() needs the raw body (Buffer) and the svix headers.
    // req.rawBody is populated by the verify callback in express.json() (see index.js).
    wh.verify(req.rawBody, req.headers);
    next();
  } catch (err) {
    console.warn(`[webhook] signature verification failed: ${err.message}`);
    return res.status(401).json({ error: "invalid signature" });
  }
}

// POST /webhook/voiceflow
// Receives all Voiceflow session lifecycle events and routes them to the correct table.
router.post("/voiceflow", verifyVoiceflowSignature, async (req, res) => {
  const payload = req.body;

  const type         = payload?.type ?? null;
  const call_sid     = payload?.data?.metadata?.callSid ?? null;
  const user_number  = payload?.data?.metadata?.userNumber ?? payload?.data?.userID ?? null;
  const agent_number = payload?.data?.metadata?.agentNumber ?? null;

  console.log(`[webhook] received event: ${type} | caller: ${user_number} | agent: ${agent_number}`);

  try {
    if (CALL_TYPES.has(type)) {
      // Update in-memory call map
      if (type === "runtime.call.start" && user_number && agent_number) {
        callMap.set(user_number, agent_number);
      } else if (type === "runtime.call.end" && user_number) {
        callMap.del(user_number);
      }

      await pool.query(
        `INSERT INTO call_events (type, call_sid, user_number, agent_number, raw_payload)
         VALUES ($1, $2, $3, $4, $5)`,
        [type, call_sid, user_number, agent_number, JSON.stringify(payload)]
      );
    } else if (SESSION_TYPES.has(type)) {
      await pool.query(
        `INSERT INTO session_events (type, user_number, raw_payload)
         VALUES ($1, $2, $3)`,
        [type, user_number, JSON.stringify(payload)]
      );
    } else {
      console.warn(`[webhook] unknown event type: ${type} — not stored`);
    }

    res.status(200).json({ received: true });
  } catch (err) {
    console.error("[webhook] DB write failed:", err);
    res.status(500).json({ received: false, error: err.message });
  }
});

// GET /webhook/events/calls — last 50 call events
router.get("/events/calls", async (req, res) => {
  try {
    const { rows } = await pool.query(
      `SELECT id, type, call_sid, user_number, agent_number, raw_payload, received_at
       FROM call_events
       ORDER BY received_at DESC
       LIMIT 50`
    );
    res.json(rows);
  } catch (err) {
    console.error("[webhook] Failed to fetch call events:", err);
    res.status(500).json({ error: err.message });
  }
});

// GET /webhook/events/sessions — last 50 session events
router.get("/events/sessions", async (req, res) => {
  try {
    const { rows } = await pool.query(
      `SELECT id, type, user_number, raw_payload, received_at
       FROM session_events
       ORDER BY received_at DESC
       LIMIT 50`
    );
    res.json(rows);
  } catch (err) {
    console.error("[webhook] Failed to fetch session events:", err);
    res.status(500).json({ error: err.message });
  }
});

// GET /webhook/callmap — debug: show current in-memory call map
router.get("/callmap", (req, res) => {
  res.json({ size: callMap.size(), entries: callMap.snapshot() });
});

module.exports = router;
