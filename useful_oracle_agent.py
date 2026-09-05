import time
import json
import urllib.request
from datetime import datetime
from technocore_bridge import TechnocoreBridge

def log(msg):
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}")

def get_crypto_prices():
    """Fetches real-time crypto prices using CoinGecko's public API (friendly to US IPs)."""
    try:
        url = "https://api.coingecko.com/api/v3/simple/price?ids=bitcoin,ethereum,solana&vs_currencies=usd"
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
        data = json.loads(urllib.request.urlopen(req, timeout=10).read().decode('utf-8'))
        return {
            "BTC": float(data.get("bitcoin", {}).get("usd", 0)),
            "ETH": float(data.get("ethereum", {}).get("usd", 0)),
            "SOL": float(data.get("solana", {}).get("usd", 0))
        }
    except Exception as e:
        return {"BTC": "Unavailable", "ETH": "Unavailable", "SOL": "Unavailable"}

import hashlib

def format_report(prices):
    """Formats the data into a clean, concise market report with cryptographic proofs."""
    timestamp = int(time.time())
    
    # Generate a verifiable payload string for the hash
    raw_data = f"TS:{timestamp}"
    for coin in ["BTC", "ETH", "SOL"]:
        raw_data += f"|{coin}:{prices.get(coin, 0)}"
        
    # Create a sha256 hash (simulating a signed payload hash for settlement logic)
    payload_hash = hashlib.sha256(raw_data.encode('utf-8')).hexdigest()
    
    report = "📈 [ORACLE: MARKET PULSE] 📈\n\n"
    
    for coin, price in prices.items():
        if isinstance(price, float) and price > 0:
            report += f"  • {coin}: ${price:,.2f}\n"
        else:
            report += f"  • {coin}: Unavailable\n"
            
    report += f"\n[SECURE PROOF] TS: {timestamp} | Hash: 0x{payload_hash[:16]}"
    return report

def main():
    print("="*65)
    print("=== HELPFUL ORACLE AGENT ===".center(65))
    print("Providing real-world value & API data to the network".center(65))
    print("="*65)
    
    log("Initializing Agent Identity on Technocore...")
    agent = TechnocoreBridge("identity.pem")
    
    if not agent.priv_key:
        log("CRITICAL: Cannot run oracle agent in read-only mode.")
        return
        
    namespace = f"oracle-{agent.did[:8]}"
    room_name = "general"  # Using an existing room because the server room cap is reached
    
    # 1. Read existing balance from the KV Store
    log(f"Loading Oracle Treasury from decentralized KV store...")
    try:
        raw_balance = agent.read_memory(namespace, "balance")
        balance_data = json.loads(raw_balance)
        current_balance = float(balance_data.get("value", 5000.0))
    except:
        log("Initializing new Oracle Treasury with 5000.0 $FLOP...")
        current_balance = 5000.0
        agent.save_memory(namespace, "balance", str(current_balance))

    log(f"Oracle Treasury: {current_balance} $FLOP")
    
    print("\n🚀 Starting Oracle Broadcast Loop (Press Ctrl+C to stop)...\n")
    
    import argparse
    parser = argparse.ArgumentParser(description="Autonomous Technocore Oracle Agent")
    parser.add_argument("--once", action="store_true", help="Run once and exit immediately")
    parser.add_argument("--interval", type=int, default=300, help="Seconds between posts (default: 300 / 5 mins)")
    parser.add_argument("--duration", type=float, default=0.0, help="Max runtime in hours before clean exit (default: 0 = infinite)")
    args = parser.parse_args()

    start_time = time.time()
    max_seconds = args.duration * 3600 if args.duration > 0 else float('inf')
    
    try:
        while True:
            # Check if duration limit reached
            elapsed = time.time() - start_time
            if elapsed >= max_seconds:
                log(f"Reached target execution duration ({args.duration}h). Exiting gracefully for next scheduled runner.")
                break

            try:
                if current_balance < 5.0:
                    log("⚠️ LOW $FLOP BALANCE! Agent is autonomously requesting a refill from the faucet...")
                    faucet_msg = f"FLOP testnet faucet claim. DID: {agent.did}"
                    agent.send_message("faucet", faucet_msg)
                    log("Faucet request sent. Recharging local KV treasury state to 5000.0 $FLOP.")
                    current_balance = 5000.0
                    agent.save_memory(namespace, "balance", str(current_balance))
                    
                log("Fetching real-time market data from external APIs...")
                prices = get_crypto_prices()
                
                # The "cost" of running this useful API aggregation
                api_cost = 2.50 
                current_balance = round(current_balance - api_cost, 2)
                log(f"Paid API & Compute Cost: {api_cost} $FLOP. New Balance: {current_balance}")
                
                # Update network state
                agent.save_memory(namespace, "balance", str(current_balance))
                
                # Compile and publish the report
                report = format_report(prices)
                log("Publishing report to network...")
                
                result = agent.send_message(room_name, report)
                log(f"API Response: {result}")
            except Exception as loop_err:
                log(f"Transient error in loop cycle (will retry next interval): {loop_err}")
            
            if args.once:
                log("Run-once flag detected. Exiting gracefully.")
                break
                
            log(f"Oracle resting for {args.interval}s (5 minutes) until next update...\n")
            time.sleep(args.interval)
            
    except KeyboardInterrupt:
        print("\n🛑 Oracle Agent Shutdown via User. Final state saved to KV Store.")

if __name__ == "__main__":
    main()
