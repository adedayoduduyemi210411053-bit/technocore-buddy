import os
import sys
import time
import json
import random
import urllib.request
import urllib.error
import re
from pathlib import Path
from datetime import datetime, timezone
from technocore_bridge import TechnocoreBridge
from sonnet_validate import read_lexicon

if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')

TEAM_MEMBERS = [
    "did:key:z6Mks3ZJxaPg9ReUjxz5EuKPk12AXtq7kiUXqm8jDNSfK78a",  # Lead (LesnaCrex)
    "did:key:z6MkrWpyheRW44T8NVcVV7Dqz6EPSwXwnpiKGubNn8xdGLyP",  # ed140408
    "did:key:z6MktSMm5HJuoDCzZNJ5YVKPjHrTw86iEn3YwXKNExe9UsZn",  # krypto
    "did:key:z6MkvtgvpCXxPZonhTZkrB71nhMbtkAJgvfydFtZn7WwJGCW"   # Us (duduyemiolamc)
]

FALLBACK_GEMINI_MODELS = [
    "gemini-2.5-flash",
    "gemini-2.0-flash",
    "gemini-2.0-flash-lite",
    "gemini-1.5-flash"
]

def log(msg):
    ts = datetime.now().strftime("%H:%M:%S")
    print(f"[{ts}] [Supervisor] {msg}", flush=True)

def fetch_json(url, timeout=8):
    req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode('utf-8'))
    except Exception as e:
        return {"error": str(e)}

class SonnetSupervisor:
    def __init__(self, key_file="identity.pem"):
        log("Initializing Sonnet Autonomous Supervisor...")
        self.agent = TechnocoreBridge(key_file)
        if not self.agent.priv_key:
            raise RuntimeError("Cannot operate Sonnet Supervisor without private key.")
            
        self.did = self.agent.did
        log(f"Supervisor DID: {self.did}")
        
        # Load lexicon
        dict_path = Path("cmudict.dict")
        if not dict_path.exists():
            dict_path = Path(__file__).parent / "cmudict.dict"
        self.lexicon = read_lexicon(dict_path)
        allowed_chars = {ch for ch in self.did.lower() if "a" <= ch <= "z"}
        self.my_vocab = {
            w: syl for w, syl in self.lexicon.items() 
            if not (set(w) - allowed_chars)
        }
        log(f"Loaded lexicon: {len(self.lexicon):,} words. Permitted vocabulary: {len(self.my_vocab):,} words.")

        # Dynamic State Variables
        self.contest_id = "sonnet-2"
        self.game_id = "lesna-2"
        self.team_room = "d-sonnet-2-team-lesna-2"
        self.rooms = {
            "registration": "mb-sonnet-2-registration",
            "discovery": "mb-sonnet-2-discovery",
            "results": "d-sonnet-2-results",
            "rules": "d-sonnet-2-rules"
        }
        self.room_generation = 1
        self.is_registered = False
        self.last_signed_generation = -1
        self.last_discovery_mention_seq = 0
        self.last_processed_team_seq = 0
        self.status = "INITIALIZING"

    def discover_active_contest(self):
        """Scans the network to identify the active contest ID and canonical rooms."""
        try:
            # Check candidate contest indices starting with the newest
            for idx in range(5, 1, -1):
                c_id = f"sonnet-{idx}"
                rules_url = f"https://technocore.chat/r/d-{c_id}-rules?format=json&limit=5"
                data = fetch_json(rules_url, timeout=5)
                messages = data.get("messages", [])
                for m in messages:
                    txt = m.get("text", "")
                    if "sonnet.launch.v1" in txt:
                        try:
                            p = json.loads(txt)
                            cfg = p.get("configuration", {})
                            if cfg.get("contest_id") == c_id and p.get("status") == "open":
                                if self.contest_id != c_id:
                                    log(f"🚨 [CONTEST MIGRATION DETECTED] Moving from {self.contest_id} -> {c_id}!")
                                    self.contest_id = c_id
                                    self.is_registered = False
                                    self.last_signed_generation = -1
                                self.rooms = cfg.get("rooms", self.rooms)
                                return True
                        except Exception:
                            pass
        except Exception as e:
            log(f"Notice during contest discovery: {e}")
        return False

    def sync_team_room_state(self):
        """Discovers the active team room and authoritative room_generation from referee results."""
        results_room = self.rooms.get("results", f"d-{self.contest_id}-results")
        url = f"https://technocore.chat/r/{results_room}?format=json&limit=80"
        data = fetch_json(url, timeout=6)
        messages = data.get("messages", [])
        
        highest_gen = self.room_generation
        found_team_room = self.team_room
        found_game_id = self.game_id

        for m in messages:
            txt = m.get("text", "")
            if "lesna" in txt.lower():
                try:
                    p = json.loads(txt)
                    # Check for room provisioning or resetup
                    if "poem_room" in p and "lesna" in p.get("poem_room", "").lower():
                        found_team_room = p.get("poem_room")
                        found_game_id = p.get("game_id", found_game_id)
                    if "room_generation" in p:
                        gen = int(p.get("room_generation"))
                        if gen > highest_gen:
                            highest_gen = gen
                except Exception:
                    pass

        if highest_gen != self.room_generation:
            log(f"🔄 [GENERATION UPDATE] Room generation updated: {self.room_generation} -> {highest_gen}")
            self.room_generation = highest_gen
            
        self.team_room = found_team_room
        self.game_id = found_game_id

    def ensure_registration(self):
        """Verifies writer registration in the active contest registration room."""
        if self.is_registered:
            return True

        reg_room = self.rooms.get("registration", f"mb-{self.contest_id}-registration")
        url = f"https://technocore.chat/r/{reg_room}?format=json&limit=100"
        data = fetch_json(url, timeout=6)
        messages = data.get("messages", [])

        # Check if referee already accepted us
        for m in messages:
            txt = m.get("text", "")
            if self.did in txt and "accepted" in txt and "writer" in txt:
                self.is_registered = True
                log(f"✅ Verified Writer registration in {reg_room} at seq {m.get('seq')}.")
                return True

        # If not registered, post pre-start proof and registration
        log(f"📝 Registering as Writer in {reg_room}...")
        proof_text = (
            f"{self.contest_id} pre-start identity evidence. Signed by {self.did}. "
            f"This Ed25519 key has verified Technocore server receipts strictly before S=2026-09-11T12:00:00Z. "
            f"Registered as writer with X https://x.com/duduyemiolamc."
        )
        self.agent.send_message(reg_room, proof_text)
        time.sleep(2)

        req_id = f"reg-writer-duduyemiolamc-{int(time.time())}"
        reg_payload = {
            "type": "sonnet.register.v1",
            "contest_id": self.contest_id,
            "role": "writer",
            "x_account_url": "https://x.com/duduyemiolamc",
            "request_id": req_id
        }
        res = self.agent.send_message(reg_room, json.dumps(reg_payload, separators=(',', ':')))
        log(f"Registration post response: {res}")
        time.sleep(2)
        return False

    def ensure_roster_signed(self):
        """Ensures the canonical roster is signed for the current room_generation."""
        if self.last_signed_generation == self.room_generation:
            return True

        disc_room = self.rooms.get("discovery", f"mb-{self.contest_id}-discovery")
        url = f"https://technocore.chat/r/{disc_room}?format=json&limit=80"
        data = fetch_json(url, timeout=6)
        messages = data.get("messages", [])

        for m in messages:
            if m.get("from") == self.did:
                txt = m.get("text", "")
                try:
                    p = json.loads(txt)
                    if p.get("type") == "sonnet.roster.v1" and p.get("game_id") == self.game_id:
                        if p.get("room_generation") == self.room_generation:
                            log(f"✅ Canonical roster verified for gen {self.room_generation} in discovery seq {m.get('seq')}.")
                            self.last_signed_generation = self.room_generation
                            return True
                except Exception:
                    pass

        # Broadcast signature for current generation
        log(f"🖋️ Co-signing canonical roster for {self.game_id} at room_generation {self.room_generation}...")
        req_id = f"roster-{self.game_id}-gen{self.room_generation}-{int(time.time())}"
        payload = {
            "type": "sonnet.roster.v1",
            "contest_id": self.contest_id,
            "game_id": self.game_id,
            "poem_room": self.team_room,
            "room_generation": self.room_generation,
            "members": TEAM_MEMBERS,
            "request_id": req_id
        }
        res = self.agent.send_message(disc_room, json.dumps(payload, separators=(',', ':')))
        log(f"Roster broadcast result: {res}")
        self.last_signed_generation = self.room_generation
        return True

    def check_teammate_mentions(self):
        """Scans discovery room for direct pings from team lead or teammates."""
        disc_room = self.rooms.get("discovery", f"mb-{self.contest_id}-discovery")
        url = f"https://technocore.chat/r/{disc_room}?format=json&limit=25"
        data = fetch_json(url, timeout=5)
        messages = data.get("messages", [])

        for m in messages:
            seq = m.get("seq", 0)
            if seq <= self.last_discovery_mention_seq:
                continue
            txt = m.get("text", "")
            sender = m.get("from", "")
            
            # Check if mentioned by someone else
            if sender != self.did and ("duduyemiolamc" in txt or self.did in txt or "@JGCW" in txt):
                self.last_discovery_mention_seq = max(self.last_discovery_mention_seq, seq)
                log(f"📣 [TEAM PING DETECTED] From {sender[:16]} at seq {seq}: {txt[:120]}")
                if "re-sign" in txt.lower() or "resign" in txt.lower() or "countersign" in txt.lower():
                    log("Teammate requested re-sign. Forcing roster re-broadcast...")
                    self.last_signed_generation = -1
                    self.ensure_roster_signed()

    def query_ai_word(self, poem_context: str, max_syllables: int) -> str | None:
        """Queries Gemini for a Shakespearean poetic word fitting syllable and letter constraints."""
        api_key = os.getenv("GEMINI_API_KEY")
        if not api_key:
            return None
            
        prompt = (
            f"You are a master Shakespearean sonnet poet writing in iambic pentameter. "
            f"Current poem lines so far:\n{poem_context}\n\n"
            f"Propose a single poetic English word (at most {max_syllables} syllables) that naturally continues this line. "
            f"Reply with ONLY the single word."
        )
        payload = {
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {"maxOutputTokens": 30, "temperature": 0.3}
        }
        
        for model in FALLBACK_GEMINI_MODELS:
            try:
                url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={api_key}"
                req = urllib.request.Request(
                    url, 
                    data=json.dumps(payload).encode("utf-8"), 
                    headers={"Content-Type": "application/json"}
                )
                res = json.loads(urllib.request.urlopen(req, timeout=4).read().decode("utf-8"))
                candidates = res.get("candidates", [])
                if candidates:
                    parts = candidates[0].get("content", {}).get("parts", [])
                    if parts and "text" in parts[0]:
                        raw = parts[0]["text"].strip().lower().strip(".,;:!?\"'")
                        words = raw.split()
                        if words:
                            w = words[0]
                            if w in self.my_vocab and self.my_vocab[w] <= max_syllables:
                                log(f"✨ [AI Word Choice] '{w}' ({self.my_vocab[w]} syl) via {model}")
                                return w
            except Exception:
                continue
        return None

    def pick_fallback_word(self, max_syllables: int) -> str:
        """Picks a valid poetic word from local lexicon."""
        poetic_favorites = [
            "the", "and", "a", "of", "in", "to", "with", "from", "on", "by", "for",
            "night", "day", "heart", "time", "dream", "bright", "fair", "gold", "deep",
            "voice", "tear", "weep", "glow", "breath", "path", "light", "star", "dark",
            "morning", "shadow", "winter", "forever", "divine", "eternity", "beauty",
            "silent", "broken", "gentle", "golden", "sacred", "wonder", "heaven"
        ]
        candidates = [w for w in poetic_favorites if w in self.my_vocab and self.my_vocab[w] <= max_syllables]
        if candidates:
            return random.choice(candidates)
        valid = [w for w, syl in self.my_vocab.items() if syl <= max_syllables and len(w) > 1]
        return random.choice(valid)

    def play_turn_if_ready(self):
        """Reads poem room, parses referee receipts, and submits next word if it is our turn."""
        url = f"https://technocore.chat/r/{self.team_room}?format=json&limit=100"
        data = fetch_json(url, timeout=6)
        messages = data.get("messages", [])

        words_history = []
        last_author = None
        last_state_hash = ""
        current_version = 0

        proposed_words = {}
        for m in messages:
            sender = m.get("from", "")
            text = m.get("text", "")
            try:
                packet = json.loads(text)
                pkt_type = packet.get("type")
                if pkt_type == "sonnet.word.v1" and "word" in packet:
                    req_id = packet.get("request_id", "")
                    if req_id:
                        proposed_words[req_id] = {
                            "word": packet.get("word", ""),
                            "sender": sender
                        }
                elif pkt_type == "sonnet.receipt.v1" and packet.get("status") == "accepted":
                    req_id = packet.get("request_id", "")
                    if req_id in proposed_words:
                        pw = proposed_words[req_id]
                        words_history.append(pw["word"])
                        last_author = packet.get("sender_did", pw["sender"])
                        last_state_hash = packet.get("state_hash", last_state_hash)
                        current_version = packet.get("version", current_version + 1)
                    elif packet.get("state_hash"):
                        last_state_hash = packet.get("state_hash", last_state_hash)
                        if "version" in packet:
                            current_version = packet.get("version", current_version)
            except Exception:
                pass

        # If room has no words, await opening word from team lead
        if not words_history:
            log(f"[{self.team_room}] Awaiting opening word from Team Lead...")
            return

        # Consecutive contributor check: Cannot go if we were the previous contributor
        if last_author == self.did:
            return

        total_syllables = sum(self.lexicon.get(w.lower().strip(".,;:!?"), 1) for w in words_history)
        current_line_syllables = total_syllables % 10
        syllables_needed = 10 - current_line_syllables if current_line_syllables > 0 else 10
        current_line_idx = (total_syllables // 10) + 1

        if current_line_idx > 14:
            log(f"🎉 [POEM COMPLETE] 14 lines reached ({total_syllables} syllables)!")
            return

        # Contribution count for Zero-Burden Guard
        my_contributions_count = sum(
            1 for m in messages 
            if m.get("from") == self.did and ("sonnet.word.v1" in m.get("text", "") or "accepted_word" in m.get("text", ""))
        )

        # Zero-Burden Guard:
        # If Line 14 is underway and we have fulfilled our qualifying turn, yield final words to Team Lead
        if current_line_idx == 14 and current_line_syllables >= 4 and my_contributions_count >= 1:
            log(f"🛡️ [Zero-Burden Guard Active] Line 14 at {current_line_syllables}/10 syl. Yielding closing words to Team Lead (@LesnaCrex) for X submission.")
            return

        log(f"🎯 [OUR TURN!] Line {current_line_idx}/14 | Line syllables: {current_line_syllables}/10 | Budget: {syllables_needed}")

        poem_text = " ".join(words_history)
        chosen = self.query_ai_word(poem_text, syllables_needed)
        if not chosen:
            chosen = self.pick_fallback_word(syllables_needed)

        req_id = f"word-{self.game_id}-{current_version}-{int(time.time())}"
        payload = {
            "type": "sonnet.word.v1",
            "contest_id": self.contest_id,
            "game_id": self.game_id,
            "room_generation": self.room_generation,
            "version": current_version,
            "previous_state_hash": last_state_hash,
            "word": chosen,
            "request_id": req_id
        }
        res = self.agent.send_message(self.team_room, json.dumps(payload, separators=(',', ':')))
        log(f"Word submission result: {res}")

    def run_supervisor_cycle(self):
        """Executes a single end-to-end supervisor reconciliation cycle."""
        self.discover_active_contest()
        self.sync_team_room_state()
        self.ensure_registration()
        self.ensure_roster_signed()
        self.check_teammate_mentions()
        self.play_turn_if_ready()
        self.status = "HEALTHY_ACTIVE"

    def run_supervisor_loop(self, poll_interval=10):
        log(f"🚀 Outer Supervisor loop started (interval: {poll_interval}s)...")
        while True:
            try:
                self.run_supervisor_cycle()
            except Exception as e:
                log(f"Supervisor transient notice: {e}")
            time.sleep(poll_interval)

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Sonnet Autonomous Outer Supervisor")
    parser.add_argument("--once", action="store_true", help="Run single cycle and exit")
    parser.add_argument("--interval", type=int, default=10, help="Loop interval in seconds")
    args = parser.parse_args()

    if "IDENTITY_PASSWORD" not in os.environ:
        os.environ["IDENTITY_PASSWORD"] = "Olamileye1315"

    supervisor = SonnetSupervisor()
    if args.once:
        supervisor.run_supervisor_cycle()
        print("\nSupervisor dry run completed successfully.")
    else:
        supervisor.run_supervisor_loop(args.interval)
