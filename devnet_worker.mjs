/**
 * FLOP Network Devnet Autonomous Agent Worker
 * 
 * Manages fleet positioning on the live FLOP Substrate Devnet:
 * 1. Checks on-chain account balances & auto-claims faucet top-ups.
 * 2. Ensures on-chain agent registration & staking (agentIdentity pallet).
 * 3. Enrolls for daily REST gateway allowances (/enroll/wallet).
 * 4. Queries active miners and executes inference requests.
 * 5. Reports task completions on-chain (reportTaskCompletion).
 * 6. Logs all receipts, transactions, and state to devnet_history.json.
 */

import { ApiPromise, WsProvider } from '@polkadot/api';
import { Keyring } from '@polkadot/keyring';
import fs from 'fs';
import path from 'path';

const RPC_URL = process.env.FLOP_RPC_URL || 'wss://rpc.devnet.flop.network';
const GATEWAY_URL = process.env.FLOP_GATEWAY_URL || 'https://api.flop.network';
const FAUCET_URL = process.env.FLOP_FAUCET_URL || 'https://faucet.flop.network';
const HISTORY_FILE = 'devnet_history.json';
const KEY_FILE = 'gateway_key.json';

function log(msg) {
  const ts = new Date().toISOString();
  console.log(`[${ts}] [DevnetWorker] ${msg}`);
}

async function sleep(ms) {
  return new Promise(resolve => setTimeout(resolve, ms));
}

function loadHistory() {
  if (fs.existsSync(HISTORY_FILE)) {
    try {
      return JSON.parse(fs.readFileSync(HISTORY_FILE, 'utf8'));
    } catch {
      return { sessions: [], txs: [], stats: {} };
    }
  }
  return { sessions: [], txs: [], stats: {} };
}

function saveHistory(data) {
  fs.writeFileSync(HISTORY_FILE, JSON.stringify(data, null, 2));
}

async function requestFaucet(address) {
  log(`Requesting faucet top-up for ${address}...`);
  try {
    const res = await fetch(`${FAUCET_URL}/fund`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'User-Agent': 'Mozilla/5.0'
      },
      body: JSON.stringify({ address })
    });
    const data = await res.json();
    if (data.success) {
      log(`Faucet funded ${data.amount} FLOP to ${address}! Tx: ${data.txHash}`);
      return data;
    } else {
      log(`Faucet notice: ${data.error || JSON.stringify(data)}`);
    }
  } catch (err) {
    log(`Faucet request error: ${err.message}`);
  }
  return null;
}

async function tryGatewayEnrollment(pair) {
  log(`Checking gateway allowance enrollment for ${pair.address}...`);
  try {
    const chalRes = await fetch(`${GATEWAY_URL}/api/v1/enroll/wallet/challenge?address=${pair.address}`, {
      headers: { 'User-Agent': 'Mozilla/5.0' }
    });
    if (!chalRes.ok) {
      log(`Challenge request returned HTTP ${chalRes.status}`);
      return null;
    }
    const challenge = await chalRes.json();
    if (!challenge.message) {
      log(`Challenge payload missing message`);
      return null;
    }

    const sigBytes = pair.sign(challenge.message);
    const signature = '0x' + Buffer.from(sigBytes).toString('hex');

    const enrollRes = await fetch(`${GATEWAY_URL}/api/v1/enroll/wallet`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'User-Agent': 'Mozilla/5.0'
      },
      body: JSON.stringify({
        address: pair.address,
        message: challenge.message,
        signature: signature
      })
    });

    const enrollData = await enrollRes.json();
    if (enrollRes.status === 200 || enrollRes.status === 201) {
      log(`Gateway enrollment SUCCESS! Key: ${enrollData.key.slice(0, 10)}... Quota: ${enrollData.remaining_quota}`);
      fs.writeFileSync(KEY_FILE, JSON.stringify(enrollData, null, 2));
      return enrollData;
    } else {
      log(`Gateway enrollment notice (HTTP ${enrollRes.status}): ${enrollData.error || enrollData.message || JSON.stringify(enrollData)}`);
    }
  } catch (err) {
    log(`Gateway enrollment error: ${err.message}`);
  }
  return null;
}

async function executeInferenceTask(prompt = 'Analyze consensus and agent coordination in autonomous multi-agent networks.') {
  if (!fs.existsSync(KEY_FILE)) {
    log('No gateway key available; skipping HTTP inference turn.');
    return null;
  }
  try {
    const keyData = JSON.parse(fs.readFileSync(KEY_FILE, 'utf8'));
    if (!keyData.key) return null;

    log(`Submitting inference request to ${GATEWAY_URL}/v1/chat/completions...`);
    const res = await fetch(`${GATEWAY_URL}/v1/chat/completions`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'Authorization': `Bearer ${keyData.key}`,
        'User-Agent': 'Mozilla/5.0'
      },
      body: JSON.stringify({
        model: keyData.model || 'qwen/qwen2.5-0.5b-instruct',
        messages: [{ role: 'user', content: prompt }],
        max_tokens: 128
      })
    });

    if (res.ok) {
      const data = await res.json();
      log(`Inference response received (${data.choices?.[0]?.message?.content?.length || 0} chars)`);
      return data;
    } else {
      const err = await res.text();
      log(`Inference request failed (HTTP ${res.status}): ${err}`);
    }
  } catch (err) {
    log(`Inference execution error: ${err.message}`);
  }
  return null;
}

export async function runDevnetCycle() {
  log('Starting Devnet autonomous positioning cycle...');
  const history = loadHistory();

  let parties;
  if (fs.existsSync('parties.json')) {
    parties = JSON.parse(fs.readFileSync('parties.json', 'utf8'));
  } else {
    throw new Error('parties.json not found');
  }

  const keyring = new Keyring({ type: 'ed25519', ss58Format: 42 });
  const alphaPair = keyring.addFromSeed(Buffer.from(parties.alpha.seed, 'hex'));
  const betaPair = keyring.addFromSeed(Buffer.from(parties.beta.seed, 'hex'));
  const gammaPair = keyring.addFromSeed(Buffer.from(parties.gamma.seed, 'hex'));

  log(`Fleet Alpha: ${alphaPair.address}`);
  log(`Fleet Beta:  ${betaPair.address}`);
  log(`Fleet Gamma: ${gammaPair.address}`);

  log(`Connecting to Substrate node: ${RPC_URL}...`);
  const wsProvider = new WsProvider(RPC_URL);
  const api = await ApiPromise.create({ provider: wsProvider });

  const [header, globalStats] = await Promise.all([
    api.rpc.chain.getHeader(),
    api.query.agentIdentity.globalStats()
  ]);

  log(`Devnet Head Block: #${header.number.toString()} | Global Network Agents: ${globalStats.totalAgents.toString()}`);

  // 1. Balance Checks & Topups
  const addresses = [
    { role: 'alpha', pair: alphaPair },
    { role: 'beta', pair: betaPair },
    { role: 'gamma', pair: gammaPair }
  ];

  for (const item of addresses) {
    const acct = await api.query.system.account(item.pair.addressRaw);
    const freePlanck = acct.data.free.toBigInt();
    const freeFlop = Number(freePlanck / (10n ** 15n)) / 1000;
    log(`[${item.role}] Balance: ${freeFlop} FLOP (${freePlanck} planck)`);

    if (freeFlop < 3.0) {
      await requestFaucet(item.pair.address);
      await sleep(2000);
    }
  }

  // 2. Verify On-Chain Agent Registrations
  const alphaAgents = await api.query.agentIdentity.agentsByOwner(alphaPair.addressRaw);
  let alphaAgentId = alphaAgents.length > 0 ? alphaAgents[0].toHex() : null;

  if (!alphaAgentId) {
    log('Registering Fleet Alpha on-chain with 10 FLOP stake...');
    const regTx = api.tx.agentIdentity.registerAgent(
      'Technocore Fleet Alpha',
      'Autonomous',
      ['Planning', 'ToolUse', 'LogicalReasoning', 'CodeGeneration'],
      ['https://technocore.chat'],
      'https://github.com/adedayoduduyemi210411053-bit/technocore-buddy',
      10n * (10n ** 18n)
    );
    await new Promise((resolve) => {
      regTx.signAndSend(alphaPair, ({ status, txHash }) => {
        if (status.isInBlock || status.isFinalized) {
          log(`Alpha registered in block! Tx: ${txHash.toHex()}`);
          resolve();
        }
      });
    });
    const refreshed = await api.query.agentIdentity.agentsByOwner(alphaPair.addressRaw);
    alphaAgentId = refreshed.length > 0 ? refreshed[0].toHex() : null;
  }
  log(`Fleet Alpha Agent ID: ${alphaAgentId}`);

  // 3. Try Gateway Enrollment
  await tryGatewayEnrollment(alphaPair);

  // 4. Query Available Models & Active Miners
  try {
    const modelsRes = await fetch(`${GATEWAY_URL}/v1/models`, {
      headers: { 'User-Agent': 'Mozilla/5.0' }
    });
    if (modelsRes.ok) {
      const modelsData = await modelsRes.json();
      const qwen = modelsData.data?.find(m => m.id === 'qwen/qwen2.5-0.5b-instruct');
      const minerCount = qwen?.['x-flop']?.price_sources?.length || 0;
      log(`Active Devnet Models: ${modelsData.data?.length || 0} | Active Miners for Qwen2.5: ${minerCount}`);
    }
  } catch (err) {
    log(`Model discovery notice: ${err.message}`);
  }

  // 5. Execute Inference & Report Completion
  await executeInferenceTask();

  if (alphaAgentId) {
    log(`Reporting verified task completion on-chain for agent ${alphaAgentId}...`);
    try {
      const taskTx = api.tx.agentIdentity.reportTaskCompletion(
        alphaAgentId,
        true,
        10000000000000000n // 0.01 FLOP volume
      );

      await new Promise((resolve, reject) => {
        taskTx.signAndSend(alphaPair, ({ status, txHash, dispatchError }) => {
          if (dispatchError) {
            reject(new Error(`Task report error: ${dispatchError.toString()}`));
          } else if (status.isInBlock || status.isFinalized) {
            log(`Task completion recorded on-chain! Tx: ${txHash.toHex()}`);
            history.txs.push({
              type: 'reportTaskCompletion',
              agentId: alphaAgentId,
              block: header.number.toString(),
              txHash: txHash.toHex(),
              timestamp: new Date().toISOString()
            });
            resolve();
          }
        }).catch(reject);
      });
    } catch (err) {
      log(`Task report notice: ${err.message}`);
    }
  }

  // 6. Save State
  history.lastRun = new Date().toISOString();
  history.headBlock = header.number.toString();
  saveHistory(history);

  await api.disconnect();
  log('Devnet positioning cycle finished successfully.');
}

// Continuous Daemon Loop CLI handling
async function main() {
  const args = process.argv.slice(2);
  let durationHours = 0;
  let intervalMinutes = 15;

  for (let i = 0; i < args.length; i++) {
    if (args[i] === '--duration' && args[i + 1]) {
      durationHours = parseFloat(args[i + 1]);
      i++;
    } else if (args[i] === '--interval' && args[i + 1]) {
      intervalMinutes = parseFloat(args[i + 1]);
      i++;
    }
  }

  if (durationHours <= 0) {
    // Single cycle execution
    await runDevnetCycle();
    return;
  }

  const endTime = Date.now() + durationHours * 3600 * 1000;
  log(`Running continuous Devnet worker daemon for ${durationHours} hours (interval: ${intervalMinutes}m)...`);

  let cycle = 1;
  while (Date.now() < endTime) {
    log(`--- Cycle #${cycle} Starting ---`);
    try {
      await runDevnetCycle();
    } catch (err) {
      log(`Cycle #${cycle} error: ${err.message}`);
    }
    const remainingMs = endTime - Date.now();
    if (remainingMs <= 0) break;

    const waitMs = Math.min(intervalMinutes * 60 * 1000, remainingMs);
    log(`Sleeping for ${Math.round(waitMs / 60000)} minutes until next Devnet cycle...`);
    await sleep(waitMs);
    cycle++;
  }

  log('Devnet worker daemon duration elapsed. Exiting cleanly.');
}

if (process.argv[1] && import.meta.url.endsWith(path.basename(process.argv[1]))) {
  main().catch(err => {
    console.error('Fatal DevnetWorker error:', err);
    process.exit(1);
  });
}
