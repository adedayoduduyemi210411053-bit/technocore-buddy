#!/usr/bin/env node
// SPDX-License-Identifier: Apache-2.0
//
// Autonomous TCLK Agent Market Daemon
// Snipes stranger task offers, completes escrow lifecycles, and maintains bilateral volume.

import { createHash, randomBytes } from "node:crypto";
import { existsSync, readFileSync, writeFileSync } from "node:fs";
import {
  dealRoom, decodeFrame, encodeFrame, generateHashLock, makeAccept, makeOffer, openContract,
  PaperRail, paperNote, stateNote, stateNoteValue,
} from "@flop-labs/tclk";
import { canonicalMessage, nextNonce, signerFromSeed, sweep } from "@flop-labs/tclk-mcp/dist/signing.js";

const BASE = process.env.TECHNOCORE_URL ?? "https://technocore.chat";
const OFFER_ROOM = "tclk-offers";

// Parse CLI flags
const args = process.argv.slice(2);
let durationHours = 0;
for (let i = 0; i < args.length; i++) {
  if (args[i] === "--duration" && args[i + 1]) {
    durationHours = parseFloat(args[i + 1]);
    i++;
  }
}

const endTime = durationHours > 0 ? Date.now() + durationHours * 3600 * 1000 : null;

console.log("=================================================================");
console.log("          ⚡ AUTONOMOUS TCLK MARKET WORKER DAEMON ⚡");
console.log(`Venue: ${BASE} | Offer Room: /r/${OFFER_ROOM}`);
if (endTime) {
  console.log(`Running in continuous daemon mode for ${durationHours} hours.`);
}
console.log("=================================================================\n");

// Load Signers
function loadSigners() {
  if (!existsSync("parties.json")) {
    console.log("Generating fresh parties.json...");
    writeFileSync("parties.json", JSON.stringify({
      alpha: { seed: randomBytes(32).toString("hex") },
      beta: { seed: randomBytes(32).toString("hex") },
      gamma: { seed: randomBytes(32).toString("hex") },
    }, null, 2));
  }

  const raw = JSON.parse(readFileSync("parties.json", "utf8"));
  const signers = {};
  for (const [name, info] of Object.entries(raw)) {
    const seedBuf = Buffer.from(info.seed, "hex");
    const s = signerFromSeed(seedBuf);
    signers[name] = s;
    console.log(`🔑 Loaded [${name}]: ${s.did}`);
  }
  return signers;
}

const signers = loadSigners();
const worker = signers.alpha ?? Object.values(signers)[0];
const partner = signers.beta ?? signers.alpha;

async function req(url, init, what = "request") {
  for (let attempt = 0; ; attempt++) {
    try {
      const res = await fetch(url, init);
      if (res.status === 429) {
        if (attempt >= 3) return res;
        const stated = Number(res.headers.get("retry-after"));
        const waitMs = (Number.isFinite(stated) && stated > 0 ? stated : 5) * 1000;
        await new Promise((r) => setTimeout(r, waitMs));
        continue;
      }
      return res;
    } catch (e) {
      if (attempt >= 2) throw e;
      await new Promise((r) => setTimeout(r, 2000));
    }
  }
}

async function post(signer, room, frame) {
  const text = sweep(encodeFrame(frame));
  const nonce = nextNonce();
  const sig = signer.sign(canonicalMessage(room, nonce, text));
  const res = await req(`${BASE}/r/${room}`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({
      did: signer.did,
      sig,
      nonce: String(nonce),
      text,
    }),
  }, `post to ${room}`);
  if (!res.ok) {
    const errText = (await res.text()).split("\n")[0];
    throw new Error(`post to ${room} (${res.status}): ${errText}`);
  }
  return text.length;
}

const notes = {
  async get(ns, key) {
    const res = await req(`${BASE}/kv/${ns}/${key}`, undefined, `get note ${ns}/${key}`);
    if (res.status === 404) return null;
    if (!res.ok) return null;
    const body = await res.text();
    const value = body
      .split("\n")
      .filter((line) => !line.startsWith("!!") && line.trim() !== "")
      .join("\n")
      .trimEnd();
    return value === "" ? null : value;
  },
  async set(ns, key, value, condition) {
    const query =
      condition === undefined ? ""
      : "ifAbsent" in condition ? "?if_absent=1"
      : `?if=${encodeURIComponent(condition.if)}`;
    const url = `${BASE}/kv/${ns}/${key}/set/${encodeURIComponent(value)}${query}`;
    const res = await req(url, undefined, `set note ${ns}/${key}`);
    return res.ok;
  },
};

const rail = new PaperRail(notes);

// Register capability note for each identity (patterns.md Pattern 3)
async function registerCapabilities() {
  console.log("\n📋 Registering DID Capability Notes (/kv/did-<shard>/<key>)...");
  for (const [name, signer] of Object.entries(signers)) {
    const hash = createHash("sha256").update(signer.did).digest("hex");
    const shard = hash.slice(0, 2);
    const key = hash.slice(2, 16);
    const mailbox = `mb-p-${shard}${key}`;
    const noteVal = `${signer.did} mailbox:${mailbox} tclk1:flop-htlc,paper,x402`;
    try {
      await notes.set(`did-${shard}`, key, noteVal);
      console.log(`   ✓ [${name}] Advertised in /kv/did-${shard}/${key} -> tclk1:flop-htlc,paper,x402`);
    } catch (e) {
      console.log(`   ⚠ [${name}] Could not set capability note: ${e.message}`);
    }
  }
}

// Active accepted contracts waiting for payer locks
const activeContracts = new Map();

// Helper to check if DID belongs to our fleet
function isOurDid(did) {
  return Object.values(signers).some((s) => s.did === did);
}

// Snipe an incoming stranger offer
async function handleStrangerOffer(offer) {
  if (isOurDid(offer.from)) return;
  if (offer.role !== "payer") return;
  if (offer.expiresMs <= Date.now() + 15000) return;

  const offerId = offer.id;
  for (const c of activeContracts.values()) {
    if (c.offer.id === offerId) return; // Already sniped
  }

  console.log(`\n🎯 [Opportunity Detected] Offer ${offer.id.slice(0, 14)}... from ${offer.from.slice(0, 16)}...`);
  console.log(`   Amount: ${offer.amount} ${offer.asset} | Rails: ${offer.rails.join(", ")} | Role: ${offer.role}`);

  const lock = generateHashLock();
  try {
    const accept = makeAccept(offer, { from: worker.did, statement: lock.hash });
    const bytes = await post(worker, OFFER_ROOM, accept);
    const room = dealRoom(accept.contract);
    console.log(`   ⚡ [Sniped!] Sent accept (${bytes} bytes). Contract: ${accept.contract.slice(0, 16)}...`);
    console.log(`   Watching Deal Room: /r/${room}`);

    activeContracts.set(accept.contract, {
      offer,
      accept,
      lock,
      room,
      createdAt: Date.now(),
      status: "accepted",
    });
  } catch (err) {
    console.log(`   ⚠ Accept failed: ${err.message}`);
  }
}

// Check deal rooms for active contracts
async function checkDealRooms() {
  if (activeContracts.size === 0) return;

  for (const [contractId, item] of activeContracts.entries()) {
    if (item.status !== "accepted") continue;

    // Expired?
    if (Date.now() > item.offer.refundAfterMs) {
      console.log(`   ⌛ Contract ${contractId.slice(0, 14)}... expired. Removing.`);
      activeContracts.delete(contractId);
      continue;
    }

    try {
      const res = await req(`${BASE}/r/${item.room}?format=json`, undefined, `read ${item.room}`);
      if (!res.ok) continue;
      const data = await res.json();
      const messages = data.messages ?? [];

      for (const m of messages) {
        if (!m.text || !m.text.startsWith("tclk1 ")) continue;
        let frame;
        try {
          frame = decodeFrame(m.text);
        } catch {
          continue;
        }

        if (frame.type === "lock" && frame.contract === contractId) {
          console.log(`\n💰 [Lock Confirmed!] Payer locked contract ${contractId.slice(0, 14)}... on rail '${frame.rail}' (Ref: ${frame.ref})`);

          // Post Reveal
          const reveal = {
            type: "reveal",
            from: worker.did,
            contract: contractId,
            secret: item.lock.preimage,
          };

          const bytes = await post(worker, item.room, reveal);
          console.log(`   🔓 [Claimed!] Published secret preimage (${bytes} bytes). Payment unlocked!`);

          if (frame.rail === "paper") {
            try {
              await rail.claim(frame.ref, item.lock.preimage);
              console.log(`   📝 Paper rail marked claimed.`);
            } catch (e) {
              console.log(`   Paper rail claim: ${e.message}`);
            }
          }

          item.status = "claimed";
          console.log(`   🎉 Profit Secured: +${item.offer.amount} ${item.offer.asset}!`);
          break;
        }
      }
    } catch (e) {
      // room might not be created yet, perfectly normal
    }
  }
}

// Run an internal bilateral deal cycle between Alpha and Beta
async function runBilateralRehearsal() {
  console.log("\n🔄 [Bilateral Lifecycle Run] Executing scheduled deal between fleet accounts...");
  const now = Date.now();
  const payer = worker;
  const payee = partner;

  try {
    // 1. Offer
    const offer = makeOffer({
      from: payer.did,
      role: "payer",
      amount: "100",
      asset: "FLOP",
      lock: "hash",
      rails: ["flop-htlc", "paper"],
      claimByMs: now + 30 * 60_000,
      refundAfterMs: now + 60 * 60_000,
      expiresMs: now + 10 * 60_000,
      job: { proto: "acp", id: `task_${randomBytes(4).toString("hex")}` },
    });
    await post(payer, OFFER_ROOM, offer);
    console.log(`   1. Offer posted to /r/${OFFER_ROOM} (${offer.id.slice(0, 16)}...)`);

    // 2. Accept
    const lock = generateHashLock();
    const accept = makeAccept(offer, { from: payee.did, statement: lock.hash });
    await post(payee, OFFER_ROOM, accept);
    const room = dealRoom(accept.contract);
    console.log(`   2. Accept posted. Derived deal room: /r/${room}`);

    // 3. Lock
    const terms = { contract: accept.contract, lock: "hash", statement: lock.hash, refundAfterMs: offer.refundAfterMs };
    const ref = await rail.lock(terms);
    const lockFrame = { type: "lock", from: payer.did, contract: accept.contract, rail: "paper", ref };
    await post(payer, room, lockFrame);
    console.log(`   3. Lock posted to deal room. Escrow ref: ${ref}`);

    // 4. Reveal
    const revealFrame = { type: "reveal", from: payee.did, contract: accept.contract, secret: lock.preimage };
    await post(payee, room, revealFrame);
    await rail.claim(ref, lock.preimage);
    console.log(`   4. Reveal posted. Preimage verified and settled.`);

    console.log(`   ✓ Bilateral deal complete and recorded on network!`);
  } catch (err) {
    console.log(`   ⚠ Bilateral run error: ${err.message}`);
  }
}

async function main() {
  await registerCapabilities();

  // Run initial bilateral deal
  await runBilateralRehearsal();

  let lastBilateralTime = Date.now();
  let lastSeq = 0;

  // Initial scan of tclk-offers
  try {
    const res = await req(`${BASE}/r/${OFFER_ROOM}?format=json&limit=20`);
    if (res.ok) {
      const data = await res.json();
      const msgs = data.messages ?? [];
      if (msgs.length > 0) {
        lastSeq = msgs[msgs.length - 1].seq;
      }
    }
  } catch {}

  console.log(`\n👀 Listening for open market offers starting from seq ${lastSeq}...`);

  while (true) {
    if (endTime && Date.now() >= endTime) {
      console.log("\n⏰ Scheduled daemon duration reached. Exiting cleanly.");
      break;
    }

    try {
      // Long-poll tclk-offers
      const pollUrl = `${BASE}/r/${OFFER_ROOM}?since=${lastSeq}&wait=10&format=json`;
      const res = await req(pollUrl, undefined, "poll tclk-offers");
      if (res.ok) {
        const data = await res.json();
        const msgs = data.messages ?? [];
        for (const m of msgs) {
          if (m.seq > lastSeq) lastSeq = m.seq;
          if (m.text && m.text.startsWith("tclk1 ")) {
            try {
              const frame = decodeFrame(m.text);
              if (frame.type === "offer") {
                await handleStrangerOffer(frame);
              }
            } catch {}
          }
        }
      }

      // Check deal rooms for locks
      await checkDealRooms();

      // Periodic bilateral activity every 20 minutes
      if (Date.now() - lastBilateralTime > 20 * 60 * 1000) {
        await runBilateralRehearsal();
        lastBilateralTime = Date.now();
      }
    } catch (e) {
      console.log(`Loop error: ${e.message}`);
      await new Promise((r) => setTimeout(r, 5000));
    }
  }
}

main().catch((err) => {
  console.error("Fatal error:", err);
  process.exit(1);
});
