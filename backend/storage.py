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
                    turn INTEGER NOT NULL DEFAULT 0 CHECK(turn >= 0),
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    CHECK ((id = 1 AND parent_agent_id IS NULL) OR
                           (id > 1 AND parent_agent_id IS NOT NULL AND parent_agent_id < id))
                );
                CREATE TABLE IF NOT EXISTS messages (
                    seq INTEGER PRIMARY KEY AUTOINCREMENT,
                    id TEXT NOT NULL UNIQUE,
                    agent_id INTEGER NOT NULL REFERENCES agents(id),
                    turn INTEGER,
                    role TEXT NOT NULL CHECK(role IN ('user','assistant','tool')),
                    content TEXT NOT NULL,
                    status TEXT NOT NULL CHECK(status IN
                        ('completed','streaming','superseded','failed','interrupted')),
                    source_message_id TEXT REFERENCES messages(id),
                    operation_key TEXT UNIQUE,
                    tool_call_message_id TEXT UNIQUE REFERENCES messages(id),
                    model TEXT,
                    error_code TEXT,
                    usage_json TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    CHECK (operation_key IS NULL OR role = 'assistant'),
                    CHECK (tool_call_message_id IS NULL OR role = 'tool'),
                    CHECK ((role = 'user' AND turn IS NULL) OR
                           (role = 'assistant' AND turn IS NOT NULL AND turn > 0) OR
                           (role = 'tool' AND (turn IS NULL OR turn > 0)))
                );
                CREATE INDEX IF NOT EXISTS messages_agent ON messages(agent_id, seq);
                CREATE TABLE IF NOT EXISTS message_payloads (
                    message_id TEXT PRIMARY KEY REFERENCES messages(id),
                    payload TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS result_events (
                    message_id TEXT PRIMARY KEY REFERENCES messages(id),
                    agent_id INTEGER NOT NULL REFERENCES agents(id),
                    turn INTEGER NOT NULL,
                    status TEXT NOT NULL DEFAULT 'pending',
                    available_at REAL NOT NULL DEFAULT 0,
                    response_message_id TEXT REFERENCES messages(id)
                );
                COMMIT;
            ''')
            with self.db:
                self.db.execute("INSERT OR IGNORE INTO agents(id,created_at,updated_at) VALUES(1,?,?)", (now(), now()))
                self.db.execute("UPDATE messages SET status='interrupted', error_code='process_exit', updated_at=? WHERE status='streaming'", (now(),))
                self.db.execute("UPDATE agents SET status='interrupted', updated_at=? WHERE status='running'", (now(),))
                self.db.execute("UPDATE agents SET status='interrupted', updated_at=? WHERE status IN ('queued','cancelling')", (now(),))
                for row in self.db.execute("SELECT * FROM agents WHERE id>1 AND status='interrupted'").fetchall():
                    if not self.db.execute("SELECT 1 FROM result_events WHERE agent_id=? AND turn=?", (row['id'], row['turn'])).fetchone():
                        message = self._message(row['id'], row['turn'] or None, 'tool',
                                                '进程退出，执行已中断。未确认的文件或命令操作不得自动重试；先核对实际状态。', 'completed')
                        self._event(row['id'], row['turn'], message)
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

    def _message(self, agent_id, turn, role, text, status, model=None):
        message_id = uid()
        self.db.execute("INSERT INTO messages(id,agent_id,turn,role,content,status,model,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?)",
                        (message_id, agent_id, turn, role, text, status, model, now(), now()))
        return message_id

    def _begin(self, agent_id, text, model):
        agent = self.get_agent(agent_id)
        turn = agent["turn"] + 1
        self.db.execute("UPDATE agents SET turn=?,status='running',updated_at=? WHERE id=?",
                        (turn, now(), agent_id))
        self._message(agent_id, None, "user", text, "completed")
        message_id = self._message(agent_id, turn, "assistant", "", "streaming", model)
        return turn, message_id

    def begin(self, text, model, agent_id=MAIN_AGENT_ID):
        with self.db:
            if self.get_agent(agent_id)["status"] in ("running", "cancelling", "cancelled"):
                raise ValueError("agent_unavailable")
            return self._begin(agent_id, text, model)

    def steer(self, agent_id, turn, old_message_id, old_text, text):
        with self.db:
            if self.get_agent(agent_id)["turn"] != turn:
                raise ValueError("stale_turn")
            row = self.db.execute("SELECT model FROM messages WHERE id=? AND agent_id=? AND turn=? AND status='streaming'",
                                  (old_message_id, agent_id, turn)).fetchone()
            if row is None:
                raise ValueError("stale_turn")
            self.db.execute("UPDATE messages SET content=?,status='superseded',updated_at=? WHERE id=?",
                            (old_text, now(), old_message_id))
            return self._begin(agent_id, text, row["model"])

    def checkpoint(self, message_id, text):
        with self.db:
            self.db.execute("UPDATE messages SET content=?,updated_at=? WHERE id=? AND status='streaming'", (text, now(), message_id))

    def finish(self, agent_id, turn, message_id, text, status, error_code=None, usage=None):
        if status not in ("completed", "failed", "interrupted"):
            raise ValueError("invalid_completion_status")
        with self.db:
            if self.get_agent(agent_id)["turn"] != turn:
                return False
            changed = self.db.execute(
                "UPDATE messages SET content=?,status=?,error_code=?,usage_json=?,updated_at=? WHERE id=? AND agent_id=? AND turn=? AND status='streaming'",
                (text, status, error_code, json.dumps(usage) if usage is not None else None, now(), message_id, agent_id, turn))
            if not changed.rowcount:
                return False
            self.db.execute("UPDATE agents SET status=?,updated_at=? WHERE id=?",
                            ("idle" if agent_id == MAIN_AGENT_ID else status, now(), agent_id))
        return True

    def all_messages(self):
        return [dict(row) for row in self.db.execute("SELECT * FROM messages ORDER BY seq")]

    def agent_history(self, agent_id):
        """All persisted history, including audit drafts and complete tool payloads."""
        self.get_agent(agent_id)
        rows = self.db.execute('''SELECT m.*, p.payload FROM messages m
                                  LEFT JOIN message_payloads p ON p.message_id=m.id
                                  WHERE m.agent_id=? ORDER BY m.seq''', (agent_id,))
        history = []
        for row in rows:
            message = dict(row)
            payload = message.pop('payload')
            message['payload'] = json.loads(payload) if payload is not None else None
            history.append(message)
        return history

    def payloads(self):
        return {r['message_id']: json.loads(r['payload']) for r in self.db.execute('SELECT * FROM message_payloads')}

    def children(self):
        return [dict(r) for r in self.db.execute('SELECT * FROM agents WHERE parent_agent_id=1 ORDER BY id')]

    def latest_user_id(self):
        row = self.db.execute("SELECT id FROM messages WHERE agent_id=1 AND role='user' ORDER BY seq DESC LIMIT 1").fetchone()
        return row['id'] if row else None

    def start_reply(self, model):
        with self.db:
            agent = self.get_agent(1)
            if agent['status'] not in ('idle', 'interrupted'):
                raise ValueError('agent_unavailable')
            turn = agent['turn'] + 1
            self.db.execute("UPDATE agents SET turn=?,status='running',updated_at=? WHERE id=1", (turn, now()))
            return turn, self._message(1, turn, 'assistant', '', 'streaming', model)

    def _event(self, agent_id, turn, message_id):
        self.db.execute('INSERT OR IGNORE INTO result_events(message_id,agent_id,turn) VALUES(?,?,?)',
                        (message_id, agent_id, turn))

    def events(self, due_only=False):
        import time
        sql = """SELECT e.*,m.content FROM result_events e JOIN messages m ON m.id=e.message_id
                 JOIN agents a ON a.id=e.agent_id WHERE e.status='pending'
                 AND e.turn=a.turn AND a.status != 'cancelling'"""
        params = ()
        if due_only:
            sql += ' AND e.available_at<=?'
            params = (time.time(),)
        return [dict(r) for r in self.db.execute(sql + ' ORDER BY m.seq', params)]

    def event_decisions(self, decisions, response_message_id):
        import time
        # Called inside the same transaction as the completed parent reply.
        valid = {e['message_id'] for e in self.events()}
        for event_id, (action, delay) in decisions.items():
            if event_id not in valid:
                continue
            self.db.execute('UPDATE result_events SET status=?,available_at=?,response_message_id=? WHERE message_id=?',
                            ('pending' if action == 'defer' else action, time.time() + delay,
                             response_message_id, event_id))

    def complete_with_events(self, agent_id, turn, message_id, text, status, code=None, usage=None, decisions=None):
        # finish() owns a transaction; keep both writes atomic with an outer savepoint-free transaction.
        with self.db:
            if self.get_agent(agent_id)['turn'] != turn:
                return False
            changed = self.db.execute("UPDATE messages SET content=?,status=?,error_code=?,usage_json=?,updated_at=? WHERE id=? AND status='streaming' AND agent_id=? AND turn=?",
                                     (text, status, code, json.dumps(usage) if usage else None, now(), message_id, agent_id, turn))
            if not changed.rowcount:
                return False
            self.db.execute('UPDATE agents SET status=?,updated_at=? WHERE id=?', ('idle' if agent_id == 1 else status, now(), agent_id))
            if agent_id != 1:
                self._event(agent_id, turn, message_id)
            elif status == 'completed':
                self.event_decisions(decisions or {}, message_id)
            return True

    def queue_child(self, text, source, agent_id=None):
        # The caller owns the operation + queue transaction.
        # Allocate the turn on acceptance; acquiring the worker slot does not advance it.
        if agent_id is None:
            cursor = self.db.execute('INSERT INTO agents(parent_agent_id,source_message_id,created_at,updated_at) VALUES(1,?,?,?)', (source, now(), now()))
            agent_id = cursor.lastrowid
        agent = self.get_agent(agent_id)
        if agent['parent_agent_id'] != 1 or agent['status'] in ('cancelled', 'cancelling'):
            raise ValueError('agent_unavailable')
        self.db.execute("UPDATE messages SET status='superseded',updated_at=? WHERE agent_id=? AND status='streaming'", (now(), agent_id))
        self.db.execute("UPDATE result_events SET status='suppressed' WHERE agent_id=? AND status='pending'", (agent_id,))
        turn = agent['turn'] + 1
        self.db.execute("UPDATE agents SET turn=?,status='queued',updated_at=? WHERE id=?", (turn, now(), agent_id))
        message = self._message(agent_id, None, 'user', text, 'completed')
        self.db.execute('UPDATE messages SET source_message_id=? WHERE id=?', (source, message))
        return agent_id

    def cancel_child(self, agent_id, source):
        agent = self.get_agent(agent_id)
        if agent['parent_agent_id'] != 1:
            raise ValueError('unknown_child')
        self.db.execute("UPDATE result_events SET status='suppressed' WHERE agent_id=? AND status='pending'", (agent_id,))
        if agent['status'] in ('completed', 'failed', 'interrupted', 'cancelled'):
            return agent
        self.db.execute("UPDATE agents SET status=?,updated_at=? WHERE id=?", ('cancelling' if agent['status'] == 'running' else 'cancelled', now(), agent_id))
        message = self._message(agent_id, None, 'tool', '主 agent 已受理撤回；停止后续执行，不回滚外部动作。', 'completed')
        self.db.execute('UPDATE messages SET source_message_id=? WHERE id=?', (source, message))
        return self.get_agent(agent_id)

    def start_child(self, agent_id, model):
        with self.db:
            agent = self.get_agent(agent_id)
            if agent['status'] != 'queued':
                raise ValueError('agent_unavailable')
            self.db.execute("UPDATE agents SET status='running',updated_at=? WHERE id=?", (now(), agent_id))
            return agent['turn'], self._message(agent_id, agent['turn'], 'assistant', '', 'streaming', model)

    def stop_child(self, agent_id, turn, message_id, text):
        with self.db:
            agent = self.get_agent(agent_id)
            if agent['turn'] != turn:
                return
            self.db.execute("UPDATE messages SET content=?,status='interrupted',updated_at=? WHERE id=? AND status='streaming'", (text, now(), message_id))
            cancelled = agent['status'] == 'cancelling'
            self.db.execute('UPDATE agents SET status=?,updated_at=? WHERE id=?', ('cancelled' if cancelled else 'interrupted', now(), agent_id))
            if cancelled:
                message = self._message(agent_id, None, 'tool', '执行已停止；已发生的文件修改或命令效果未回滚。', 'completed')
                self._event(agent_id, turn, message)

    def keyed_tool_calls(self, agent_id):
        """Derive deduplication evidence from original call/result messages."""
        rows = self.db.execute("""SELECT c.id, c.turn, c.operation_key, p.payload,
                   r.id AS result_message_id, r.content AS result
            FROM messages c JOIN message_payloads p ON p.message_id=c.id
            LEFT JOIN messages r ON r.tool_call_message_id=c.id
            WHERE c.agent_id=? AND c.operation_key IS NOT NULL ORDER BY c.seq""", (agent_id,))
        return [self._tool_call_info(row) for row in rows]

    def find_tool_call(self, key):
        row = self.db.execute("""SELECT c.id, c.turn, c.operation_key, p.payload,
                   r.id AS result_message_id, r.content AS result
            FROM messages c JOIN message_payloads p ON p.message_id=c.id
            LEFT JOIN messages r ON r.tool_call_message_id=c.id
            WHERE c.operation_key=?""", (key,)).fetchone()
        return self._tool_call_info(row) if row else None

    @staticmethod
    def _tool_call_info(row):
        call = json.loads(row['payload'])['tool_calls'][0]
        return {'message_id': row['id'], 'turn': row['turn'], 'operation_key': row['operation_key'],
                'name': call['function']['name'], 'arguments': json.loads(call['function']['arguments']),
                'result_message_id': row['result_message_id'],
                'result': json.loads(row['result']) if row['result_message_id'] else None}

    def interrupt_queued(self):
        with self.db:
            self.db.execute("UPDATE agents SET status='interrupted',updated_at=? WHERE parent_agent_id=1 AND status='queued'", (now(),))

    def record_tool_call(self, agent_id, turn, call, operation_key=None):
        # The caller commits this message before starting an external action.
        mid = self._message(agent_id, turn, 'assistant', '', 'completed')
        self.db.execute('UPDATE messages SET operation_key=? WHERE id=?', (operation_key, mid))
        payload = {'role': 'assistant', 'content': None, 'tool_calls': [call]}
        self.db.execute('INSERT INTO message_payloads VALUES(?,?)', (mid, json.dumps(payload, ensure_ascii=False)))
        return mid

    def record_tool_result(self, call_message_id, result):
        row = self.db.execute("""SELECT m.agent_id, m.turn, p.payload FROM messages m
            JOIN message_payloads p ON p.message_id=m.id WHERE m.id=?""", (call_message_id,)).fetchone()
        call = json.loads(row['payload'])['tool_calls'][0]
        content = json.dumps(result, ensure_ascii=False)
        mid = self._message(row['agent_id'], row['turn'], 'tool', content, 'completed')
        self.db.execute('UPDATE messages SET tool_call_message_id=? WHERE id=?', (call_message_id, mid))
        payload = {'role': 'tool', 'tool_call_id': call['id'], 'content': content}
        self.db.execute('INSERT INTO message_payloads VALUES(?,?)', (mid, json.dumps(payload, ensure_ascii=False)))
        return mid

    def record_exchange(self, agent_id, turn, call, result):
        # Synchronous replies (including replay/refusal) still record the actual new call.
        mid = self.record_tool_call(agent_id, turn, call)
        self.record_tool_result(mid, result)

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
