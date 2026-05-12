const express = require("express");
const { pool } = require("../database");

const router = express.Router();

const CALL_TYPES    = new Set(["runtime.call.start", "runtime.call.end"]);
const SESSION_TYPES = new Set(["runtime.session.start", "runtime.session.end"]);

// POST /webhook/voiceflow
// Receives all Voiceflow session lifecycle events and routes them to the correct table.
router.post("/voiceflow", async (req, res) => {
  const payload = req.body;

  const type         = payload?.type ?? null;
  const call_sid     = payload?.data?.metadata?.callSid ?? null;
  const user_number  = payload?.data?.metadata?.userNumber ?? payload?.data?.userID ?? null;
  const agent_number = payload?.data?.metadata?.agentNumber ?? null;

  console.log(`[webhook] received event: ${type} | caller: ${user_number} | agent: ${agent_number}`);

  try {
    if (CALL_TYPES.has(type)) {
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

module.exports = router;
