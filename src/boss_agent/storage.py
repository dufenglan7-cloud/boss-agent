# SQLite 持久化：会话、用户画像、计划和运行审计；版本检查与写入共用事务。
import json
import sqlite3
from pathlib import Path
from .models import Plan

# 会话或画像版本已变化，需要读取新状态后由调用方决定是否重试。
class SessionConflict(RuntimeError):
    pass

# 使用短生命周期连接；SQLite 上下文内出错时回滚整笔写入。
class Store:
    # 创建必要表并兼容旧计划的用户字段；不删除已有业务数据。
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        with sqlite3.connect(path) as db:
            db.execute("CREATE TABLE IF NOT EXISTS user_portraits (user_id TEXT NOT NULL, mode TEXT NOT NULL, revision INTEGER NOT NULL, payload TEXT NOT NULL, PRIMARY KEY(user_id, mode))")
            db.execute("CREATE TABLE IF NOT EXISTS executions (run_id TEXT PRIMARY KEY, session_id TEXT NOT NULL, payload TEXT NOT NULL)")
            db.execute("CREATE TABLE IF NOT EXISTS chat_sessions (id TEXT PRIMARY KEY, version INTEGER NOT NULL, payload TEXT NOT NULL, updated_at TEXT DEFAULT CURRENT_TIMESTAMP)")
            db.execute("CREATE TABLE IF NOT EXISTS plans (run_id TEXT PRIMARY KEY, mode TEXT NOT NULL, payload TEXT NOT NULL, created_at TEXT DEFAULT CURRENT_TIMESTAMP)")
            # 只补齐缺失字段并将旧数据归入默认用户，不重建或清空已有计划。
            if "user_id" not in {row[1] for row in db.execute("PRAGMA table_info(plans)")}:
                db.execute("ALTER TABLE plans ADD COLUMN user_id TEXT NOT NULL DEFAULT 'local-user'")

    # 保存一份完整计划及所属用户，调用方负责保证它不是已投递凭证。
    def save(self, run_id: str, mode: str, plan: Plan, user_id: str = "local-user"):
        with sqlite3.connect(self.path) as db:
            db.execute("INSERT INTO plans(run_id, mode, payload, user_id) VALUES (?, ?, ?, ?)",
                       (run_id, mode, plan.model_dump_json(), user_id))

    # 参数化查询单个计划，并反序列化为受校验的业务模型。
    def get(self, run_id: str) -> dict | None:
        with sqlite3.connect(self.path) as db:
            row = db.execute("SELECT run_id, mode, payload, user_id FROM plans WHERE run_id = ?", (run_id,)).fetchone()
        if row is None:
            return None
        return {"run_id": row[0], "mode": row[1], "plan": Plan.model_validate_json(row[2]), "user_id": row[3]}

    # 写入会话 JSON 和初始版本，长期画像存放在独立表中。
    def create_session(self, session_id: str, payload: dict):
        with sqlite3.connect(self.path) as db:
            db.execute("INSERT INTO chat_sessions(id, version, payload) VALUES (?, ?, ?)",
                       (session_id, payload["version"], json.dumps(payload, ensure_ascii=False)))

    # 仅负责原始会话读取，外部接口还必须执行用户归属检查。
    def get_session(self, session_id: str) -> dict | None:
        with sqlite3.connect(self.path) as db:
            row = db.execute("SELECT payload FROM chat_sessions WHERE id = ?", (session_id,)).fetchone()
        return json.loads(row[0]) if row else None

    # 只返回当前标识的会话摘要，不在列表中展开私人资料。
    def list_sessions(self, user_id="local-user") -> list[dict]:
        with sqlite3.connect(self.path) as db:
            rows = db.execute("SELECT payload FROM chat_sessions WHERE COALESCE(json_extract(payload, '$.user_id'), 'local-user') = ? ORDER BY updated_at DESC, rowid DESC LIMIT 50", (user_id,)).fetchall()
        return [{key: value[key] for key in ("id", "title", "mode", "version")}
                for row in rows for value in [json.loads(row[0])]]

    # 原子提交会话、画像、新计划和成功审计；任一版本冲突回滚全部写入。
    def commit_session(self, session_id: str, expected: int, payload: dict, plan_record=None, execution_record=None, portrait_record=None):
        with sqlite3.connect(self.path) as db:
            # 以旧 version 做条件更新；行数为零意味着读取后已经有人写入。
            cursor = db.execute("UPDATE chat_sessions SET payload = ?, version = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ? AND version = ?",
                                (json.dumps(payload, ensure_ascii=False), payload["version"], session_id, expected))
            if cursor.rowcount != 1:
                raise SessionConflict("Session changed")
            # 跨会话也可能同时修改同一画像，因此画像 revision 需与会话版本分别核对。
            if portrait_record:
                user_id, mode, expected_revision, portrait = portrait_record
                row = db.execute("SELECT revision FROM user_portraits WHERE user_id=? AND mode=?", (user_id, mode)).fetchone()
                if row is None or row[0] != expected_revision:
                    raise SessionConflict("User portrait changed")
                # 普通问答没有改变画像时只检查并发状态，不额外制造新版本。
                if portrait["revision"] != expected_revision:
                    db.execute("UPDATE user_portraits SET revision=?, payload=? WHERE user_id=? AND mode=?",
                               (portrait["revision"], json.dumps(portrait, ensure_ascii=False), user_id, mode))
            # 以下写入仍处于同一连接事务内，后续报错会连同会话更新一起回滚。
            if plan_record:
                run_id, mode, plan = plan_record
                db.execute("INSERT INTO plans(run_id, mode, payload, user_id) VALUES (?, ?, ?, ?)",
                           (run_id, mode, plan.model_dump_json(), payload.get("user_id", "local-user")))

            if execution_record:
                db.execute("INSERT INTO executions(run_id, session_id, payload) VALUES (?, ?, ?)",
                           (execution_record["run_id"], session_id, json.dumps(execution_record, ensure_ascii=False)))

    # 失败运行也单独留存审计，但不把半条对话提交到会话历史。
    def save_execution(self, execution: dict):
        with sqlite3.connect(self.path) as db:
            db.execute("INSERT OR REPLACE INTO executions(run_id, session_id, payload) VALUES (?, ?, ?)",
                       (execution["run_id"], execution["session_id"], json.dumps(execution, ensure_ascii=False)))

    # 同时匹配会话 ID 与运行 ID，防止拿错其他会话的执行记录。
    def get_execution(self, session_id: str, run_id: str):
        with sqlite3.connect(self.path) as db:
            row = db.execute("SELECT payload FROM executions WHERE run_id=? AND session_id=?", (run_id, session_id)).fetchone()
        return json.loads(row[0]) if row else None

    # 按用户及模式惰性建立空画像，随后读取当前快照。
    def get_portrait(self, user_id, mode):
        from .portraits.models import UserPortrait
        empty = UserPortrait(user_id=user_id, mode=mode).model_dump()
        with sqlite3.connect(self.path) as db:
            db.execute("INSERT OR IGNORE INTO user_portraits(user_id, mode, revision, payload) VALUES (?, ?, 0, ?)",
                       (user_id, mode, json.dumps(empty, ensure_ascii=False)))
            row = db.execute("SELECT payload FROM user_portraits WHERE user_id=? AND mode=?", (user_id, mode)).fetchone()
        return json.loads(row[0])

    # 以旧 revision 为条件更新画像，受影响行数用于判断并发冲突。
    def replace_portrait(self, user_id, mode, expected_revision, payload):
        with sqlite3.connect(self.path) as db:
            cursor = db.execute("UPDATE user_portraits SET revision=?, payload=? WHERE user_id=? AND mode=? AND revision=?",
                               (payload["revision"], json.dumps(payload, ensure_ascii=False), user_id, mode, expected_revision))
            if cursor.rowcount != 1:
                raise SessionConflict("User portrait changed")
