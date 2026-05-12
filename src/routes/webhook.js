const express = require("express");
const { pool } = require("../database");

const router = express.Router();

// POST /webhook/voiceflow
// Receives all Voiceflow session lifecycle events and writes them to the DB.
router.post("/voiceflow", async (req, res) => {
  const payload = req.body;

  const type        = payload?.type ?? null;
  const call_sid    = payload?.data?.metadata?.callSid ?? null;
  const user_number = payload?.data?.metadata?.userNumber ?? payload?.data?.userID ?? null;
  const agent_number = payload?.data?.metadata?.agentNumber ?? null;

  console.log(`[webhook] received event: ${type} | caller: ${user_number} | agent: ${agent_number}`);

  try {
    await pool.query(
      `INSERT INTO call_events (type, call_sid, user_number, agent_number, raw_payload)
       VALUES ($1, $2, $3, $4, $5)`,
      [type, call_sid, user_number, agent_number, JSON.stringify(payload)]
    );
    res.status(200).json({ received: true });
  } catch (err) {
    console.error("[webhook] DB write failed:", err);
    res.status(500).json({ received: false, error: err.message });
  }
});

// GET /webhook/events
// Returns the 50 most recent events — useful for confirming what Voiceflow is sending.
router.get("/events", async (req, res) => {
  try {
    const { rows } = await pool.query(
      `SELECT id, type, call_sid, user_number, agent_number, raw_payload, received_at
       FROM call_events
       ORDER BY received_at DESC
       LIMIT 50`
    );
    res.json(rows);
  } catch (err) {
    console.error("[webhook] Failed to fetch events:", err);
    res.status(500).json({ error: err.message });
  }
});

module.exports = router;
