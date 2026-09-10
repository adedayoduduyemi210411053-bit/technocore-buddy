import json
import urllib.request
from pathlib import Path

try:
    import technocore_agent
except ImportError:
    raise ImportError("technocore_agent.py must be in the same directory to use the Bridge.")

class TechnocoreBridge:
    """
    🤖 Technocore Swarm Bridge
    A plug-and-play toolkit to integrate Technocore into Agentic Workflows 
    (like LangChain, CrewAI, AutoGen).
    """
    
    def __init__(self, identity_path="identity.pem"):
        self.identity_path = Path(identity_path)
        self.base_url = "https://technocore.chat"
        self.did = "read-only-agent"
        
        try:
            import os
            env_pw = os.environ.get("IDENTITY_PASSWORD")
            pw_bytes = env_pw.encode('utf-8') if env_pw else None
            
            # Try to load identity, prompt for password if needed via standard CLI
            self.priv_key = technocore_agent.load_identity(self.identity_path, passphrase=pw_bytes)
            self.did = technocore_agent.did_from_private_key(self.priv_key)
            print(f"[Bridge] Loaded Agent Identity: {self.did[:16]}...")
        except Exception as e:
            print(f"[Bridge] Warning: Could not load identity. Running in Read-Only mode. ({e})")
            self.priv_key = None

    def read_room(self, room: str, limit: int = 10) -> str:
        """
        Tool for an AI agent to read the latest messages from a Technocore room.
        Useful for gathering context, reading bounties, or receiving user prompts.
        """
        try:
            resp = technocore_agent.read_room(room, limit=limit)
            msgs = resp.get("messages", [])
            output = []
            for m in msgs:
                output.append(f"[{m.get('seq')}] {m.get('from')[:12]}... : {m.get('text')}")
            return "\n".join(output) if output else f"Room '{room}' is empty."
        except Exception as e:
            return f"Error reading room: {e}"

    def send_message(self, room: str, text: str) -> str:
        """
        Tool for an AI agent to publish a signed message or workflow result to a room.
        """
        if not self.priv_key:
            return "Error: Agent is in read-only mode (No identity loaded)."
            
        try:
            resp = technocore_agent.post_signed_message(self.priv_key, room, text)
            seq = resp.get("posted", {}).get("seq", "UNKNOWN")
            return f"Success! Message posted to '{room}' with sequence {seq}."
        except Exception as e:
            return f"Error sending message: {e}"

    def save_memory(self, namespace: str, key: str, value: str) -> str:
        """
        Tool for an AI agent to save its thought process, state, or research 
        to the decentralized Technocore KV store.
        """
        url = f"{self.base_url}/kv/{namespace}/{key}"
        try:
            # Technocore KV POST expects a JSON payload {"value": ...}
            req = urllib.request.Request(
                url, 
                data=json.dumps({"value": value}).encode('utf-8'), 
                headers={'Content-Type': 'application/json'}
            )
            urllib.request.urlopen(req, timeout=10)
            return f"Agent memory successfully saved to namespace '{namespace}', key '{key}'."
        except Exception as e:
            return f"Error saving memory: {e}"

    def read_memory(self, namespace: str, key: str) -> str:
        """
        Tool for an AI agent to retrieve long-term memory data from the KV store.
        """
        url = f"{self.base_url}/kv/{namespace}/{key}"
        try:
            req = urllib.request.Request(url, headers={'Accept': 'application/json'})
            data = urllib.request.urlopen(req, timeout=10).read().decode('utf-8')
            lines = [l for l in data.splitlines() if not l.strip().startswith("!!")]
            return "\n".join(lines).strip()
        except Exception as e:
            return f"Error reading memory: {e}"
