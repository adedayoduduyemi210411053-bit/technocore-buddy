import json
from pathlib import Path
from cryptography.hazmat.primitives import serialization
import technocore_agent

import os

passphrase = os.environ.get("FLEET_PASSWORD", "12345678abcd").encode("utf-8")
keys = {}

for name in ['alpha', 'beta', 'gamma']:
    p = Path(f"identity_{name}.pem")
    if p.exists():
        key = technocore_agent.load_identity(p, passphrase)
        raw = key.private_bytes(serialization.Encoding.Raw, serialization.PrivateFormat.Raw, serialization.NoEncryption())
        did = technocore_agent.did_from_private_key(key)
        keys[name] = {
            "seed": raw.hex(),
            "did": did
        }
        print(f"Loaded {name}: {did}")

with open("parties.json", "w") as f:
    json.dump(keys, f, indent=2)

print("Saved parties.json successfully.")
