import { ApiPromise, WsProvider } from '@polkadot/api';
import { Keyring } from '@polkadot/keyring';
import fs from 'fs';

async function registerBeta() {
  console.log('[Devnet] Connecting to Substrate RPC...');
  const wsProvider = new WsProvider('wss://rpc.devnet.flop.network');
  const api = await ApiPromise.create({ provider: wsProvider });

  const parties = JSON.parse(fs.readFileSync('parties.json', 'utf8'));
  const keyring = new Keyring({ type: 'ed25519', ss58Format: 42 });
  const betaPair = keyring.addFromSeed(Buffer.from(parties.beta.seed, 'hex'));

  console.log(`[Devnet] Fleet Beta Address: ${betaPair.address}`);

  const stakeAmount = 10n * (10n ** 18n); // 10 FLOP stake

  console.log(`[Devnet] Submitting agentIdentity.registerAgent for Beta with 10 FLOP...`);
  const tx = api.tx.agentIdentity.registerAgent(
    'Technocore Fleet Beta',
    'Autonomous',
    ['Planning', 'ToolUse', 'LogicalReasoning', 'Summarization'],
    ['https://technocore.chat'],
    'https://github.com/adedayoduduyemi210411053-bit/technocore-buddy',
    stakeAmount
  );

  await new Promise((resolve, reject) => {
    tx.signAndSend(betaPair, ({ status, txHash, dispatchError }) => {
      console.log(`[Devnet] Tx status: ${status.type}`);
      if (dispatchError) {
        if (dispatchError.isModule) {
          const decoded = api.registry.findMetaError(dispatchError.asModule);
          reject(new Error(`Registration failed: ${decoded.section}.${decoded.name}: ${decoded.docs.join(' ')}`));
        } else {
          reject(new Error(`Registration failed: ${dispatchError.toString()}`));
        }
      } else if (status.isInBlock || status.isFinalized) {
        const blockHash = status.isInBlock ? status.asInBlock : status.asFinalized;
        console.log(`[Devnet] SUCCESS! Included in block: ${blockHash.toHex()}`);
        console.log(`[Devnet] Transaction Hash: ${txHash.toHex()}`);
        resolve();
      }
    }).catch(reject);
  });

  await api.disconnect();
}

registerBeta().catch(console.error);
