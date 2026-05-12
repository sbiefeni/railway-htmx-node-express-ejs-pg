const { Pool } = require("pg");

const pool = new Pool({
  connectionString: process.env.DATABASE_URL,
  ssl: process.env.DATABASE_URL?.includes("localhost")
    ? false
    : { rejectUnauthorized: false },
});

async function migrate() {
  await pool.query(`
    CREATE TABLE IF NOT EXISTS call_events (
      id          SERIAL PRIMARY KEY,
      type        TEXT NOT NULL,
      call_sid    TEXT,
      user_number TEXT,
      agent_number TEXT,
      raw_payload JSONB NOT NULL,
      received_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
    )
  `);
}

module.exports = { pool, migrate };
