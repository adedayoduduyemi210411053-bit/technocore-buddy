import { ApiPromise, WsProvider } from '@polkadot/api';
import { Keyring } from '@polkadot/keyring';
import fs from 'fs';

async function registerAlpha() {
  console.log('[Devnet] Connecting to Substrate RPC...');
  const wsProvider = new WsProvider('wss://rpc.devnet.flop.network');
  const api = await ApiPromise.create({ provider: wsProvider });

  const parties = JSON.parse(fs.readFileSync('parties.json', 'utf8'));
  const keyring = new Keyring({ type: 'ed25519', ss58Format: 42 });
  const alphaPair = keyring.addFromSeed(Buffer.from(parties.alpha.seed, 'hex'));

  console.log(`[Devnet] Fleet Alpha Address: ${alphaPair.address}`);

  const stakeAmount = 10n * (10n ** 18n); // 10 FLOP minimum stake

  console.log(`[Devnet] Submitting agentIdentity.registerAgent with ${stakeAmount} planck (10 FLOP)...`);
  const tx = api.tx.agentIdentity.registerAgent(
    'Technocore Fleet Alpha',
    'Autonomous',
    ['Planning', 'ToolUse', 'LogicalReasoning', 'CodeGeneration'],
    ['https://technocore.chat'],
    'https://github.com/adedayoduduyemi210411053-bit/technocore-buddy',
    stakeAmount
  );

  await new Promise((resolve, reject) => {
    tx.signAndSend(alphaPair, ({ status, txHash, dispatchError }) => {
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

registerAlpha().catch(console.error);
