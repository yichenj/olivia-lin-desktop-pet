"""SQLite history. All access belongs to the backend event-loop thread."""
from datetime import datetime, timezone
import fcntl
from pathlib import Path
import sqlite3
import uuid


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
            version = self.db.execute("PRAGMA user_version").fetchone()[0]
            if version not in (0, 1):
                raise RuntimeError("unsupported_history_version")
            self.db.executescript('''
                CREATE TABLE IF NOT EXISTS chat_runs (
                    id TEXT PRIMARY KEY,
                    status TEXT NOT NULL CHECK(status IN ('running','completed','failed','interrupted')),
                    generation INTEGER NOT NULL,
                    model TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    finished_at TEXT,
                    error_code TEXT,
                    usage_json TEXT
                );
                CREATE TABLE IF NOT EXISTS messages (
                    seq INTEGER PRIMARY KEY AUTOINCREMENT,
                    id TEXT NOT NULL UNIQUE,
                    chat_id TEXT NOT NULL REFERENCES chat_runs(id),
                    generation INTEGER NOT NULL,
                    role TEXT NOT NULL CHECK(role IN ('user','assistant')),
                    content TEXT NOT NULL,
                    status TEXT NOT NULL CHECK(status IN
                        ('completed','streaming','superseded','failed','interrupted')),
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS messages_chat ON messages(chat_id, seq);
                PRAGMA user_version=1;
            ''')
            with self.db:
                self.db.execute("UPDATE messages SET status='interrupted', updated_at=? WHERE status='streaming'", (now(),))
                self.db.execute("UPDATE chat_runs SET status='interrupted', error_code='process_exit', updated_at=?, finished_at=? WHERE status='running'", (now(), now()))
        except Exception:
            if hasattr(self, "db"):
                self.db.close()
            self.lock.close()
            raise

    def _message(self, chat_id, generation, role, text, status):
        message_id = uid()
        self.db.execute("INSERT INTO messages(id,chat_id,generation,role,content,status,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?)",
                        (message_id, chat_id, generation, role, text, status, now(), now()))
        return message_id

    def begin(self, text, model):
        chat_id = uid()
        with self.db:
            self.db.execute("INSERT INTO chat_runs(id,status,generation,model,created_at,updated_at) VALUES(?,'running',1,?,?,?)",
                            (chat_id, model, now(), now()))
            self._message(chat_id, 1, "user", text, "completed")
            message_id = self._message(chat_id, 1, "assistant", "", "streaming")
        return chat_id, message_id

    def steer(self, chat_id, generation, old_message_id, old_text, text):
        with self.db:
            self.db.execute("UPDATE messages SET content=?, status='superseded',updated_at=? WHERE id=?", (old_text, now(), old_message_id))
            self.db.execute("UPDATE chat_runs SET generation=?, updated_at=? WHERE id=?", (generation, now(), chat_id))
            self._message(chat_id, generation, "user", text, "completed")
            return self._message(chat_id, generation, "assistant", "", "streaming")

    def checkpoint(self, message_id, text):
        with self.db:
            self.db.execute("UPDATE messages SET content=?,updated_at=? WHERE id=? AND status='streaming'", (text, now(), message_id))

    def finish(self, chat_id, message_id, text, status, error_code=None, usage=None):
        import json
        with self.db:
            self.db.execute("UPDATE messages SET content=?,status=?,updated_at=? WHERE id=?", (text, status, now(), message_id))
            self.db.execute("UPDATE chat_runs SET status=?,updated_at=?,finished_at=?,error_code=?,usage_json=? WHERE id=?",
                            (status, now(), now(), error_code, json.dumps(usage) if usage else None, chat_id))

    def all_messages(self):
        return [dict(row) for row in self.db.execute('''SELECT m.*, r.status AS run_status
            FROM messages m JOIN chat_runs r ON r.id=m.chat_id ORDER BY m.seq''')]

    def list_messages(self, before=None, limit=50):
        rows = self.db.execute("SELECT * FROM messages WHERE seq < ? ORDER BY seq DESC LIMIT ?",
                               (before if before is not None else 9223372036854775807, limit + 1)).fetchall()
        # Bound each page by bytes too: long model replies must not overflow the
        # client's frame buffer even when the caller requests 200 records.
        import json
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
