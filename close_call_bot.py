import sys
import time
import json
import urllib.request
import os
from pathlib import Path
import technocore_agent
import logging
import argparse
from datetime import datetime, timedelta

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger('close_call_bot')

FLEET = ['alpha', 'beta', 'gamma']
PASSPHRASE = os.environ.get('FLEET_PASSWORD', '12345678abcd').encode('utf-8')
MAIN_PASSPHRASE = os.environ.get('IDENTITY_PASSWORD', '').encode('utf-8')

def load_fleet():
    agents = {}
    for member in FLEET:
        path = Path(f'identity_{member}.pem')
        if path.exists():
            priv_key = technocore_agent.load_identity(path, PASSPHRASE)
            did = technocore_agent.did_from_private_key(priv_key)
            agents[member] = {'priv': priv_key, 'did': did}
            logger.info(f"Loaded {member} ({did[:16]}...)")
    
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

def execute_cross_trade(maker, taker, qty, px, until):
    import uuid
    import base64
    from cryptography.exceptions import InvalidSignature
    
    trade_id = uuid.uuid4().hex[:16]
    terms = {
        "id": trade_id,
        "maker": maker['did'],
        "px": f"{px:.2f}",
        "qty": f"{qty:.2f}",
        "side": "sell",  # maker is selling to taker
        "taker": taker['did'],
        "until": until
    }
    
    terms_text = json.dumps(terms, separators=(',', ':'), sort_keys=True)
    
    # 1. Maker signs `close-1|terms|<terms>`
    maker_payload = f"close-1|terms|{terms_text}".encode('utf-8')
    maker_sig_bytes = maker['priv'].sign(maker_payload)
    maker_sig = base64.urlsafe_b64encode(maker_sig_bytes).decode('ascii').rstrip('=')
    
    # 2. Taker signs `close-1|accept|<terms>|<taker did:key>`
    taker_payload = f"close-1|accept|{terms_text}|{taker['did']}".encode('utf-8')
    taker_sig_bytes = taker['priv'].sign(taker_payload)
    taker_sig = base64.urlsafe_b64encode(taker_sig_bytes).decode('ascii').rstrip('=')
    
    # 3. Construct t:trade message
    trade_msg = {
        "t": "trade",
        "season": "close-1",
        "terms": terms,
        "taker": taker['did'],
        "maker_sig": maker_sig,
        "taker_sig": taker_sig
    }
    
    text = json.dumps(trade_msg, separators=(',', ':'))
    
    # Post using either key (using maker here)
    try:
        resp = technocore_agent.post_signed_message(maker['priv'], 'close1', text)
        seq = resp.get('posted', {}).get('seq')
        logger.info(f"Cross-trade {qty:.2f} @ {px:.2f} (Maker: {maker['did'][:8]} Taker: {taker['did'][:8]}) -> seq {seq}")
        return seq
    except Exception as e:
        logger.error(f"Cross-trade submission failed: {e}")
        return None

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--duration', type=float, default=0, help='Duration to run in hours')
    args = parser.parse_args()

    logger.info("Starting Close Call Trading Fleet")
    agents = load_fleet()
    if not agents:
        logger.error("No identities loaded. Exiting.")
        return

    end_time = datetime.now() + timedelta(hours=args.duration) if args.duration > 0 else None
    last_processed_sweep = -1

    while True:
        try:
            ref_data = get_latest_ref()
            if ref_data:
                n = ref_data.get('n', 0)
                ref_px = float(ref_data.get('ref', {}).get('px', 0))
                
                if n > last_processed_sweep and ref_px > 0:
                    logger.info(f"--- Processing New Sweep: {n}, Ref Price: {ref_px:.2f} ---")
                    until = n + 1
                    
                    # Execute cross trades:
                    # Beta buys from Alpha (Beta is long, Alpha is short)
                    if 'alpha' in agents and 'beta' in agents:
                        execute_cross_trade(maker=agents['alpha'], taker=agents['beta'], qty=1.0, px=ref_px, until=until)
                        
                    # Main buys from Gamma (Main is long, Gamma is short)
                    if 'main' in agents and 'gamma' in agents:
                        execute_cross_trade(maker=agents['gamma'], taker=agents['main'], qty=1.0, px=ref_px, until=until)
                        
                    last_processed_sweep = n
        except Exception as e:
            logger.error(f"Main loop error: {e}")

        if end_time and datetime.now() >= end_time:
            logger.info("Reached maximum duration, exiting.")
            break
            
        if args.duration == 0:
            break
            
        time.sleep(30)

if __name__ == "__main__":
    main()
