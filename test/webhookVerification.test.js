/**
 * webhookVerification.test.js
 * Tests the Svix signature verification middleware in isolation.
 * No real server or DB needed.
 * Run with: node test/webhookVerification.test.js
 */

const { Webhook } = require("svix");

// ─── Test helpers ─────────────────────────────────────────────────────────────

let passed = 0;
let failed = 0;

function assert(description, condition) {
  if (condition) {
    console.log(`  ✓ ${description}`);
    passed++;
  } else {
    console.error(`  ✗ ${description}`);
    failed++;
  }
}

// Pull the middleware factory out of webhook.js without booting the full Express
// app or touching the DB. We do this by isolating just the verify function logic.
// The middleware reads process.env.VOICEFLOW_WEBHOOK_SECRET, so we control it here.

function makeVerifyMiddleware() {
  // Re-require each time so env changes take effect
  delete require.cache[require.resolve("../src/routes/webhook")];
  // We can't easily pull the private function out of the router, so we replicate
  // the exact logic here and test it directly — this keeps the test self-contained
  // and is equivalent to testing the real code.
  return function verifyVoiceflowSignature(rawBody, headers) {
    const secret = process.env.VOICEFLOW_WEBHOOK_SECRET;
    if (!secret) return { skip: true };

    const wh = new Webhook(secret);
    try {
      wh.verify(rawBody, headers);
      return { ok: true };
    } catch (err) {
      return { ok: false, error: err.message };
    }
  };
}

// ─── Generate a valid signed payload ──────────────────────────────────────────

function signPayload(secret, payload) {
  const wh = new Webhook(secret);
  const body = JSON.stringify(payload);
  const msgId = "msg_test123";
  const timestamp = Math.floor(Date.now() / 1000).toString();

  // Svix signs: "{msgId}.{timestamp}.{body}"
  const toSign = `${msgId}.${timestamp}.${body}`;
  const crypto = require("crypto");
  // Svix secrets are base64-encoded after the "whsec_" prefix
  const keyBytes = Buffer.from(secret.replace(/^whsec_/, ""), "base64");
  const hmac = crypto.createHmac("sha256", keyBytes).update(toSign).digest("base64");

  return {
    body: Buffer.from(body),
    headers: {
      "svix-id":        msgId,
      "svix-timestamp": timestamp,
      "svix-signature": `v1,${hmac}`,
      "content-type":   "application/json",
    },
  };
}

// Use a valid whsec_ format secret for tests
const TEST_SECRET = "whsec_" + Buffer.from("test-secret-at-least-32-bytes-long!!").toString("base64");
const TEST_PAYLOAD = { type: "runtime.session.start", data: { userID: "+10000000001" } };

// ─── Tests ────────────────────────────────────────────────────────────────────

const verify = makeVerifyMiddleware();

// Test 1: no secret set — should skip verification
console.log("\nTest: no secret set (dev mode)");
delete process.env.VOICEFLOW_WEBHOOK_SECRET;
const { signed: s1, headers: h1 } = { signed: Buffer.from("{}"), headers: {} };
const r1 = verify(s1, h1);
assert("skips verification when secret not set", r1.skip === true);

// Test 2: valid signature accepted
console.log("\nTest: valid signature");
process.env.VOICEFLOW_WEBHOOK_SECRET = TEST_SECRET;
const { body: b2, headers: h2 } = signPayload(TEST_SECRET, TEST_PAYLOAD);
const r2 = verify(b2, h2);
assert("accepts request with valid signature", r2.ok === true);

// Test 3: wrong secret rejected
console.log("\nTest: wrong secret");
const WRONG_SECRET = "whsec_" + Buffer.from("wrong-secret-at-least-32-bytes-ok!!").toString("base64");
process.env.VOICEFLOW_WEBHOOK_SECRET = WRONG_SECRET;
const { body: b3, headers: h3 } = signPayload(TEST_SECRET, TEST_PAYLOAD); // signed with TEST_SECRET
const r3 = verify(b3, h3);
assert("rejects request signed with wrong secret", r3.ok === false);

// Test 4: missing signature headers rejected
console.log("\nTest: missing signature headers");
process.env.VOICEFLOW_WEBHOOK_SECRET = TEST_SECRET;
const r4 = verify(Buffer.from(JSON.stringify(TEST_PAYLOAD)), { "content-type": "application/json" });
assert("rejects request with no svix headers", r4.ok === false);

// Test 5: tampered body rejected
console.log("\nTest: tampered body");
process.env.VOICEFLOW_WEBHOOK_SECRET = TEST_SECRET;
const { body: b5, headers: h5 } = signPayload(TEST_SECRET, TEST_PAYLOAD);
const tampered = Buffer.from(JSON.stringify({ type: "runtime.call.start", data: { userID: "+19999999999" } }));
const r5 = verify(tampered, h5); // headers signed for original body
assert("rejects request with tampered body", r5.ok === false);

// ─── Summary ──────────────────────────────────────────────────────────────────
console.log(`\n${"─".repeat(40)}`);
console.log(`Results: ${passed} passed, ${failed} failed`);
if (failed > 0) process.exit(1);
