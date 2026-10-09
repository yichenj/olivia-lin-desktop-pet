"""Agent-owned SQLite history; accessed only by the backend event-loop thread."""
from datetime import datetime, timezone
import fcntl
import json
from pathlib import Path
import sqlite3
import uuid

MAIN_AGENT_ID = 1


def now():
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def uid():
    return uuid.uuid4().hex


class HistoryStore:
    def __init__(self, path):
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        self.lock = open(str(path) + ".lock", "a")
        try:
            fcntl.flock(self.lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            self.lock.close()
            raise RuntimeError("history_in_use") from None
        try:
            self.db = sqlite3.connect(path)
            self.db.row_factory = sqlite3.Row
            self.db.execute("PRAGMA foreign_keys=ON")
            self.db.execute("PRAGMA journal_mode=WAL")
            self.db.executescript('''BEGIN IMMEDIATE;
                CREATE TABLE IF NOT EXISTS agents (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    parent_agent_id INTEGER REFERENCES agents(id),
                    source_message_id TEXT REFERENCES messages(id),
                    status TEXT NOT NULL DEFAULT 'idle' CHECK(status IN
                        ('idle','queued','running','waiting','cancelling','cancelled','completed','failed','interrupted')),
                    generation INTEGER NOT NULL DEFAULT 0 CHECK(generation >= 0),
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    CHECK ((id = 1 AND parent_agent_id IS NULL) OR
                           (id > 1 AND parent_agent_id IS NOT NULL AND parent_agent_id < id))
                );
                CREATE TABLE IF NOT EXISTS messages (
                    seq INTEGER PRIMARY KEY AUTOINCREMENT,
                    id TEXT NOT NULL UNIQUE,
                    agent_id INTEGER NOT NULL REFERENCES agents(id),
                    generation INTEGER,
                    role TEXT NOT NULL CHECK(role IN ('user','assistant','tool')),
                    content TEXT NOT NULL,
                    status TEXT NOT NULL CHECK(status IN
                        ('completed','streaming','superseded','failed','interrupted')),
                    source_message_id TEXT REFERENCES messages(id),
                    model TEXT,
                    error_code TEXT,
                    usage_json TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    CHECK ((role = 'user' AND generation IS NULL) OR
                           (role = 'assistant' AND generation IS NOT NULL AND generation > 0) OR
                           (role = 'tool' AND (generation IS NULL OR generation > 0)))
                );
                CREATE INDEX IF NOT EXISTS messages_agent ON messages(agent_id, seq);
                COMMIT;
            ''')
            with self.db:
                self.db.execute("INSERT OR IGNORE INTO agents(id,created_at,updated_at) VALUES(1,?,?)", (now(), now()))
                self.db.execute("UPDATE messages SET status='interrupted', error_code='process_exit', updated_at=? WHERE status='streaming'", (now(),))
                self.db.execute("UPDATE agents SET status='interrupted', updated_at=? WHERE status='running'", (now(),))
        except Exception:
            if hasattr(self, "db"):
                self.db.close()
            self.lock.close()
            raise

    def get_agent(self, agent_id=MAIN_AGENT_ID):
        row = self.db.execute("SELECT * FROM agents WHERE id=?", (agent_id,)).fetchone()
        if row is None:
            raise ValueError("unknown_agent")
        return dict(row)

    def create_agent(self, parent_agent_id=MAIN_AGENT_ID, source_message_id=None):
        """Create a history owner, not a running subagent or scheduler."""
        with self.db:
            self.get_agent(parent_agent_id)
            cursor = self.db.execute(
                "INSERT INTO agents(parent_agent_id,source_message_id,created_at,updated_at) VALUES(?,?,?,?)",
                (parent_agent_id, source_message_id, now(), now()))
        return cursor.lastrowid

    def _message(self, agent_id, generation, role, text, status, model=None):
        message_id = uid()
        self.db.execute("INSERT INTO messages(id,agent_id,generation,role,content,status,model,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?)",
                        (message_id, agent_id, generation, role, text, status, model, now(), now()))
        return message_id

    def _begin(self, agent_id, text, model):
        agent = self.get_agent(agent_id)
        generation = agent["generation"] + 1
        self.db.execute("UPDATE agents SET generation=?,status='running',updated_at=? WHERE id=?",
                        (generation, now(), agent_id))
        self._message(agent_id, None, "user", text, "completed")
        message_id = self._message(agent_id, generation, "assistant", "", "streaming", model)
        return generation, message_id

    def begin(self, text, model, agent_id=MAIN_AGENT_ID):
        with self.db:
            if self.get_agent(agent_id)["status"] in ("running", "cancelling", "cancelled"):
                raise ValueError("agent_unavailable")
            return self._begin(agent_id, text, model)

    def steer(self, agent_id, generation, old_message_id, old_text, text):
        with self.db:
            if self.get_agent(agent_id)["generation"] != generation:
                raise ValueError("stale_generation")
            row = self.db.execute("SELECT model FROM messages WHERE id=? AND agent_id=? AND generation=? AND status='streaming'",
                                  (old_message_id, agent_id, generation)).fetchone()
            if row is None:
                raise ValueError("stale_generation")
            self.db.execute("UPDATE messages SET content=?,status='superseded',updated_at=? WHERE id=?",
                            (old_text, now(), old_message_id))
            return self._begin(agent_id, text, row["model"])

    def checkpoint(self, message_id, text):
        with self.db:
            self.db.execute("UPDATE messages SET content=?,updated_at=? WHERE id=? AND status='streaming'", (text, now(), message_id))

    def finish(self, agent_id, generation, message_id, text, status, error_code=None, usage=None):
        if status not in ("completed", "failed", "interrupted"):
            raise ValueError("invalid_completion_status")
        with self.db:
            if self.get_agent(agent_id)["generation"] != generation:
                return False
            changed = self.db.execute(
                "UPDATE messages SET content=?,status=?,error_code=?,usage_json=?,updated_at=? WHERE id=? AND agent_id=? AND generation=? AND status='streaming'",
                (text, status, error_code, json.dumps(usage) if usage is not None else None, now(), message_id, agent_id, generation))
            if not changed.rowcount:
                return False
            self.db.execute("UPDATE agents SET status=?,updated_at=? WHERE id=?",
                            ("idle" if agent_id == MAIN_AGENT_ID else status, now(), agent_id))
        return True

    def all_messages(self):
        return [dict(row) for row in self.db.execute("SELECT * FROM messages ORDER BY seq")]

    def list_messages(self, before=None, limit=50, agent_id=MAIN_AGENT_ID):
        self.get_agent(agent_id)
        rows = self.db.execute("SELECT * FROM messages WHERE agent_id=? AND seq < ? ORDER BY seq DESC LIMIT ?",
                               (agent_id, before if before is not None else 9223372036854775807, limit + 1)).fetchall()
        # Keep each page below the client's frame budget, including long replies.
        selected, size = [], 0
        for row in rows[:limit]:
            item = dict(row)
            item_size = len(json.dumps(item, ensure_ascii=False).encode("utf-8"))
            if selected and size + item_size > 1024 * 1024:
                break
            selected.append(item)
            size += item_size
        more = len(rows) > len(selected)
        items = list(reversed(selected))
        return {"messages": items, "nextCursor": items[0]["seq"] if more else None}

    def close(self):
        self.db.close()
        self.lock.close()
