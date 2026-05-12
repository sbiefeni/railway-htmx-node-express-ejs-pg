/**
 * callMap.js — In-memory caller → agent mapping
 *
 * Stores the pair (userNumber → agentNumber) for the duration of a live call.
 * Entries auto-expire after TTL_MS to prevent leaks if call.end is never received.
 *
 * Designed for single-instance Railway deployment. To swap to Redis, replace
 * the Map operations below with ioredis get/set/del calls — interface stays the same.
 */

const TTL_MS = 120_000; // 2 minutes

/** @type {Map<string, { agentNumber: string, timer: NodeJS.Timeout }>} */
const _map = new Map();

/**
 * Store a userNumber → agentNumber mapping for TTL_MS milliseconds.
 * Calling set() again for the same userNumber resets the TTL.
 *
 * @param {string} userNumber
 * @param {string} agentNumber
 */
function set(userNumber, agentNumber) {
  // Clear any existing timer for this key before overwriting
  if (_map.has(userNumber)) {
    clearTimeout(_map.get(userNumber).timer);
  }

  const timer = setTimeout(() => {
    _map.delete(userNumber);
    console.log(`[callMap] TTL expired for ${userNumber}`);
  }, TTL_MS);

  // Allow the process to exit even if this timer is still pending
  if (timer.unref) timer.unref();

  _map.set(userNumber, { agentNumber, timer });
  console.log(`[callMap] set ${userNumber} → ${agentNumber} (TTL ${TTL_MS}ms)`);
}

/**
 * Retrieve the agentNumber for a caller, or null if not found.
 *
 * @param {string} userNumber
 * @returns {string|null}
 */
function get(userNumber) {
  return _map.get(userNumber)?.agentNumber ?? null;
}

/**
 * Remove the mapping (call ended or TTL fired).
 *
 * @param {string} userNumber
 */
function del(userNumber) {
  const entry = _map.get(userNumber);
  if (entry) {
    clearTimeout(entry.timer);
    _map.delete(userNumber);
    console.log(`[callMap] deleted ${userNumber}`);
  }
}

/**
 * Return a plain snapshot of the current map (for debug endpoints).
 *
 * @returns {Record<string, string>}
 */
function snapshot() {
  const out = {};
  for (const [k, v] of _map.entries()) {
    out[k] = v.agentNumber;
  }
  return out;
}

/** How many entries are currently live (for tests / health checks). */
function size() {
  return _map.size;
}

module.exports = { set, get, del, snapshot, size };
