import os
import sys
import time
import json
import urllib.request
import urllib.error
import random
from pathlib import Path
from datetime import datetime, timezone
from technocore_bridge import TechnocoreBridge
from sonnet_validate import read_lexicon, validate_word, word_syllables

if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')

CONTEST_ID = "sonnet-1"
GAME_ID = "lesna"
TEAM_ROOM = f"d-sonnet-1-team-{GAME_ID}"
DISCOVERY_ROOM = "mb-sonnet-1-discovery"
OPENING_UTC_TIMESTAMP = 1789128000  # 2026-09-11 12:00:00 UTC

TEAM_MEMBERS = [
    "did:key:z6Mks3ZJxaPg9ReUjxz5EuKPk12AXtq7kiUXqm8jDNSfK78a",  # Lead (LesnaCrex)
    "did:key:z6MkrWpyheRW44T8NVcVV7Dqz6EPSwXwnpiKGubNn8xdGLyP",  # Member 2 (ed140408)
    "did:key:z6MktSMm5HJuoDCzZNJ5YVKPjHrTw86iEn3YwXKNExe9UsZn",  # Member 3 (krypto)
    "did:key:z6MkvtgvpCXxPZonhTZkrB71nhMbtkAJgvfydFtZn7WwJGCW"   # Member 4 (Us / duduyemiolamc)
]

FALLBACK_GEMINI_MODELS = [
    "gemini-2.5-flash",
    "gemini-2.0-flash",
    "gemini-2.0-flash-lite",
    "gemini-1.5-flash"
]

def log(msg):
    ts = datetime.now().strftime("%H:%M:%S")
    print(f"[{ts}] {msg}", flush=True)

class SonnetPlayer:
    def __init__(self, key_file="identity.pem"):
        log("Initializing Sonnet Player identity...")
        self.agent = TechnocoreBridge(key_file)
        if not self.agent.priv_key:
            raise RuntimeError("Cannot play Sonnet Challenge without private signing key.")
            
        self.did = self.agent.did
        log(f"Authenticated DID: {self.did}")
        
        dict_path = Path("cmudict.dict")
        if not dict_path.exists():
            dict_path = Path(__file__).parent / "cmudict.dict"
        log("Loading CMUdict dictionary...")
        self.lexicon = read_lexicon(dict_path)
        log(f"Loaded {len(self.lexicon):,} dictionary words.")
        
        # Build our allowed vocabulary (case-insensitive letter match against our DID)
        allowed_chars = {ch for ch in self.did.lower() if "a" <= ch <= "z"}
        self.my_vocab = {}
        for w, syl in self.lexicon.items():
            if not (set(w) - allowed_chars):
                self.my_vocab[w] = syl
        log(f"Player vocabulary: {len(self.my_vocab):,} words allowed by our DID letters.")
        
        self.roster_signed = False
        self.room_generation = 0
        self.last_discovery_seq = 0
        self.last_team_seq = 0

    def query_ai_word(self, poem_context: str, max_syllables: int) -> str | None:
        """Asks Gemini Flash for the best Shakespearean word fitting our syllable budget and allowed vocabulary."""
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
            "generationConfig": {"maxOutputTokens": 50, "temperature": 0.3}
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
                                log(f"✨ [AI Poetic Word Choice] '{w}' ({self.my_vocab[w]} syl) via {model}")
                                return w
            except Exception:
                continue
        return None

    def pick_fallback_word(self, max_syllables: int) -> str:
        """Picks a high-quality poetic word from our allowed vocabulary fitting the syllable limit."""
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
        # Random pick fitting syllables
        valid = [w for w, syl in self.my_vocab.items() if syl <= max_syllables and len(w) > 1]
        return random.choice(valid)

    def check_and_sign_roster(self):
        """Checks if 12:00:00 UTC has arrived and signs sonnet.roster.v1 once."""
        now_utc = datetime.now(timezone.utc).timestamp()
        if self.roster_signed:
            return True
            
        # Check if already signed in discovery history
        try:
            url = f"https://technocore.chat/r/{DISCOVERY_ROOM}?format=json&limit=50"
            req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
            data = json.loads(urllib.request.urlopen(req, timeout=8).read().decode('utf-8'))
            for m in data.get("messages", []):
                t = m.get("text", "")
                if m.get("from") == self.did:
                    try:
                        pkt = json.loads(t)
                        if pkt.get("type") == "sonnet.roster.v1" and pkt.get("game_id") == GAME_ID:
                            log(f"✅ Formal sonnet.roster.v1 verified in discovery at seq {m.get('seq')}.")
                            self.roster_signed = True
                            return True
                    except Exception:
                        pass
                # Detect any room generation update from lead or referee
                if GAME_ID in t and "generation" in t:
                    try:
                        p = json.loads(t)
                        if "room_generation" in p:
                            self.room_generation = int(p["room_generation"])
                    except Exception:
                        pass
        except Exception as e:
            log(f"Notice checking discovery: {e}")

        # If 12:00:00 UTC has arrived (or within 60s of start), sign!
        if now_utc >= (OPENING_UTC_TIMESTAMP - 60):
            req_id = f"roster-{GAME_ID}-{int(time.time())}"
            payload = {
                "type": "sonnet.roster.v1",
                "contest_id": CONTEST_ID,
                "game_id": GAME_ID,
                "poem_room": TEAM_ROOM,
                "room_generation": self.room_generation,
                "members": TEAM_MEMBERS,
                "request_id": req_id
            }
            msg = json.dumps(payload, separators=(',', ':'))
            log(f"🖋️ [Co-Signing Roster] Broadcasting sonnet.roster.v1 to {DISCOVERY_ROOM}...")
            res = self.agent.send_message(DISCOVERY_ROOM, msg)
            log(f"Roster broadcast response: {res}")
            self.roster_signed = True
            return True
        else:
            remaining = int(OPENING_UTC_TIMESTAMP - now_utc)
            log(f"⏳ Waiting for Opening S (12:00:00 UTC) in {remaining} seconds...")
            return False

    def play_team_room_turn(self):
        """Reads d-sonnet-1-team-lesna and plays word if it's our turn."""
        url = f"https://technocore.chat/r/{TEAM_ROOM}?format=json&limit=100"
        try:
            req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
            data = json.loads(urllib.request.urlopen(req, timeout=8).read().decode('utf-8'))
            messages = data.get("messages", [])
        except Exception as e:
            return

        words_history = []
        last_author = None
        last_state_hash = ""
        current_version = 0

        for m in messages:
            sender = m.get("from", "")
            text = m.get("text", "")
            # Check for referee receipts or word packets
            try:
                packet = json.loads(text)
                if packet.get("type") == "sonnet.word.v1":
                    words_history.append(packet.get("word", ""))
                    last_author = sender
                    current_version = packet.get("version", current_version + 1)
                elif "accepted_word" in packet:
                    words_history.append(packet.get("accepted_word", ""))
                    last_author = packet.get("contributor", sender)
                    last_state_hash = packet.get("state_hash", last_state_hash)
                    current_version = packet.get("version", current_version + 1)
            except Exception:
                pass

        # If no words yet, lead or any member can start Line 1 Word 1
        if not words_history:
            log(f"[{TEAM_ROOM}] Awaiting opening word from team...")
            return

        # Check turn: Any member except previous contributor may go next
        if last_author == self.did:
            # We just played the previous turn; waiting for another team member
            return

        # Calculate current line syllables
        # Form: 14 lines, 10 syllables per line
        total_syllables = sum(self.lexicon.get(w.lower().strip(".,;:!?"), 1) for w in words_history)
        current_line_syllables = total_syllables % 10
        syllables_needed = 10 - current_line_syllables if current_line_syllables > 0 else 10
        current_line_idx = (total_syllables // 10) + 1

        if current_line_idx > 14:
            log(f"🎉 [TEAM COMPLETE] 14 lines reached ({total_syllables} syllables)!")
            return

        # Check our historical contribution count in this poem
        my_contributions_count = sum(
            1 for m in messages 
            if m.get("from") == self.did and ("sonnet.word.v1" in m.get("text", "") or "accepted_word" in m.get("text", ""))
        )

        # Zero-Burden Guard:
        # If we are on Line 14 and have already contributed our mandatory word(s),
        # yield the closing words of Line 14 so Team Lead / teammates become the final contributor responsible for publishing to X.
        if current_line_idx == 14 and current_line_syllables >= 4 and my_contributions_count >= 1:
            log(f"🛡️ [Zero-Burden Guard Active] Line 14 at {current_line_syllables}/10 syllables. Yielding closing word to Team Lead (@LesnaCrex) for X submission.")
            return

        log(f"🎯 [OUR TURN!] Line {current_line_idx}/14 | Line syllables: {current_line_syllables}/10 | Budget: {syllables_needed}")
        
        # Select best word
        poem_text = " ".join(words_history)
        chosen = self.query_ai_word(poem_text, syllables_needed)
        if not chosen:
            chosen = self.pick_fallback_word(syllables_needed)

        # Propose word
        next_version = current_version + 1
        req_id = f"word-{GAME_ID}-{next_version}-{int(time.time())}"
        payload = {
            "type": "sonnet.word.v1",
            "contest_id": CONTEST_ID,
            "game_id": GAME_ID,
            "room_generation": self.room_generation,
            "version": next_version,
            "previous_state_hash": last_state_hash,
            "word": chosen,
            "request_id": req_id
        }
        compact = json.dumps(payload, separators=(',', ':'))
        log(f"✍️ Submitting word '{chosen}' ({self.my_vocab.get(chosen)} syl) to {TEAM_ROOM}...")
        res = self.agent.send_message(TEAM_ROOM, compact)
        log(f"Word submission result: {res}")

    def run_loop(self):
        log("🚀 Sonnet Player autonomous coordinator loop started (Press Ctrl+C to stop)...")
        while True:
            try:
                self.check_and_sign_roster()
                if self.roster_signed:
                    self.play_team_room_turn()
            except Exception as e:
                log(f"Loop notice: {e}")
            time.sleep(6)

if __name__ == "__main__":
    if "IDENTITY_PASSWORD" not in os.environ:
        os.environ["IDENTITY_PASSWORD"] = "Olamileye1315"
    player = SonnetPlayer()
    player.run_loop()
