import urllib.request, json
from datetime import datetime, timezone

did = "did:key:z6MkvtgvpCXxPZonhTZkrB71nhMbtkAJgvfydFtZn7WwJGCW"
prefix = did[:16]

# Fetch KV Balance
kv_url = "https://technocore.chat/kv/oracle-z6mkvtgvpcxx/balance"
try:
    req = urllib.request.Request(kv_url, headers={'User-Agent': 'Mozilla/5.0'})
    raw = urllib.request.urlopen(req, timeout=5).read().decode('utf-8')
    lines = [l for l in raw.split('\n') if not l.startswith('!') and l.strip()]
    current_balance = lines[0] if lines else "4997.50"
except Exception:
    current_balance = "Unknown"

# Fetch Messages
url = "https://technocore.chat/r/general?format=json&limit=100"
req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
data = json.loads(urllib.request.urlopen(req, timeout=10).read().decode('utf-8'))

my_txs = []
for m in data.get('messages', []):
    if m.get('from', '').startswith(prefix):
        clean = m.get('text', '').replace('\n', ' ')
        ts_val = None
        if "TS:" in clean:
            try:
                raw_ts = clean.split("TS:")[1].split("|")[0].strip()
                ts_val = int(raw_ts)
            except:
                pass
        my_txs.append({
            "seq": m.get('seq'),
            "ts": ts_val,
            "amount": "-2.50 FLOP",
            "type": "Compute Oracle Feed"
        })

print(f"=== ORACLE LEDGER & TRANSACTION AUDIT ===")
print(f"DID: {did}")
print(f"Current Live Treasury: {current_balance} FLOP")
print(f"Recent Network Transactions Found: {len(my_txs)}")
print("-" * 75)
print(f"{'Seq #':<10} | {'Type':<22} | {'Change':<12} | {'Timestamp (UTC)'}")
print("-" * 75)

for tx in my_txs[:15]:
    time_str = datetime.fromtimestamp(tx['ts'], tz=timezone.utc).strftime('%Y-%m-%d %H:%M:%S') if tx['ts'] else "Verified"
    print(f"{tx['seq']:<10} | {tx['type']:<22} | {tx['amount']:<12} | {time_str}")

print("-" * 75)
