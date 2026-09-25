import sys
import time
import json
import urllib.request
import os
from pathlib import Path
import technocore_agent
import logging

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger('close_call_bot')

FLEET = ['alpha', 'beta', 'gamma']
PASSPHRASE = os.environ.get('FLEET_PASSWORD', '12345678abcd').encode('utf-8')
MAIN_PASSPHRASE = os.environ.get('IDENTITY_PASSWORD', '').encode('utf-8')

# The logic: 
# Alpha: delta-neutral market maker (sells above ref, buys below ref)
# Beta: long-biased
# Gamma: short-biased

def load_fleet():
    agents = {}
    for member in FLEET:
        path = Path(f'identity_{member}.pem')
        if path.exists():
            priv_key = technocore_agent.load_identity(path, PASSPHRASE)
            did = technocore_agent.did_from_private_key(priv_key)
            agents[member] = {'priv': priv_key, 'did': did}
            logger.info(f"Loaded {member} ({did[:16]}...)")
    
    # Try loading main identity
    main_path = Path('identity.pem')
    if main_path.exists() and MAIN_PASSPHRASE:
        try:
            priv_key = technocore_agent.load_identity(main_path, MAIN_PASSPHRASE)
            did = technocore_agent.did_from_private_key(priv_key)
            agents['main'] = {'priv': priv_key, 'did': did}
            logger.info(f"Loaded main ({did[:16]}...)")
        except Exception as e:
            logger.error(f"Could not load main identity: {e}")
            
    return agents

def get_latest_ref():
    try:
        req = urllib.request.Request('https://technocore.chat/r/d-close1-price?format=json&limit=2', headers={'User-Agent': 'Mozilla/5.0'})
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read().decode('utf-8'))
            for msg in reversed(data.get('messages', [])):
                try:
                    text_data = json.loads(msg.get('text', '{}'))
                    if text_data.get('t') == 'price':
                        return text_data
                except:
                    pass
    except Exception as e:
        logger.error(f"Error fetching reference: {e}")
    return None

def submit_trade(priv_key, did, side, qty, px, until):
    import uuid
    trade_id = uuid.uuid4().hex[:16]
    terms = {
        "id": trade_id,
        "maker": did,
        "px": f"{px:.2f}",
        "qty": f"{qty:.2f}",
        "side": side,
        "taker": "any",
        "until": until
    }
    # terms must be sorted by key, no spaces
    text = json.dumps(terms, separators=(',', ':'), sort_keys=True)
    msg = f"close-1|terms|{text}"
    
    try:
        resp = technocore_agent.post_signed_message(priv_key, 'close1', text)
        seq = resp.get('posted', {}).get('seq')
        logger.info(f"Submitted {side} {qty:.2f} @ {px:.2f} -> seq {seq}")
        return seq
    except Exception as e:
        logger.error(f"Trade submission failed: {e}")
        return None

def main():
    logger.info("Starting Close Call Trading Fleet")
    agents = load_fleet()
    if not agents:
        logger.error("No identities loaded. Exiting.")
        return
        
    ref_data = get_latest_ref()
    if not ref_data:
        logger.error("Could not fetch latest reference data.")
        return
        
    n = ref_data.get('n', 0)
    ref_px = float(ref_data.get('ref', {}).get('px', 0))
    if not ref_px:
        logger.error("Invalid reference price.")
        return
        
    logger.info(f"Current Sweep: {n}, Ref Price: {ref_px:.2f}")
    
    # We will submit trades valid for the next sweep
    until = n + 1
    
    # Execute strategy
    if 'alpha' in agents:
        # Market maker: tight spread around ref
        submit_trade(agents['alpha']['priv'], agents['alpha']['did'], "buy", 0.5, ref_px * 0.99, until)
        submit_trade(agents['alpha']['priv'], agents['alpha']['did'], "sell", 0.5, ref_px * 1.01, until)
        
    if 'beta' in agents:
        # Long biased
        submit_trade(agents['beta']['priv'], agents['beta']['did'], "buy", 1.0, ref_px * 0.995, until)
        
    if 'gamma' in agents:
        # Short biased
        submit_trade(agents['gamma']['priv'], agents['gamma']['did'], "sell", 1.0, ref_px * 1.005, until)
        
    if 'main' in agents:
        # Opportunistic long
        submit_trade(agents['main']['priv'], agents['main']['did'], "buy", 0.5, ref_px * 0.98, until)
        
    logger.info("Execution round complete.")

if __name__ == "__main__":
    main()
