"""Durable cost reservation/cache outside the restorable source database."""
from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

from .provider import AIFailure

KST = timezone(timedelta(hours=9), "Asia/Seoul")


@dataclass(frozen=True)
class Claim:
    kind: str  # reserved | complete | failed | busy
    attempt_id: str | None = None
    payload: dict | None = None
    error_code: str | None = None
    reserved_krw: int = 0


class CostLedger:
    def __init__(self, path: Path):
        self.path = path
        self._initialized = False

    @contextmanager
    def _transaction(self):
        conn = None
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            if self.path.is_symlink() or (self.path.exists() and self.path.is_dir()):
                raise ValueError()
            if self.path.exists() and getattr(self.path.stat(), "st_file_attributes", 0) & 0x400:
                raise ValueError()
            conn = sqlite3.connect(self.path, timeout=5)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA busy_timeout=5000")
            conn.execute("PRAGMA synchronous=FULL")
            conn.execute("BEGIN IMMEDIATE")
            if not self._initialized:
                conn.execute("CREATE TABLE IF NOT EXISTS ledger_meta(version INTEGER NOT NULL)")
                row = conn.execute("SELECT version FROM ledger_meta").fetchall()
                if not row:
                    conn.execute("INSERT INTO ledger_meta VALUES(1)")
                elif len(row) != 1 or row[0][0] != 1:
                    raise ValueError()
                conn.execute("""CREATE TABLE IF NOT EXISTS ai_attempts(
                    id TEXT PRIMARY KEY, cache_key TEXT NOT NULL, guard_hash TEXT NOT NULL,
                    day TEXT NOT NULL, month TEXT NOT NULL, state TEXT NOT NULL,
                    reserved_krw INTEGER NOT NULL CHECK(reserved_krw>=0), estimated_krw INTEGER,
                    usd_micros INTEGER, input_tokens INTEGER, output_tokens INTEGER, cached_tokens INTEGER,
                    payload TEXT, error_code TEXT, created_at TEXT NOT NULL)""")
                conn.execute("CREATE INDEX IF NOT EXISTS ix_ai_cache ON ai_attempts(cache_key,created_at)")
                conn.execute("CREATE INDEX IF NOT EXISTS ix_ai_day ON ai_attempts(day)")
                conn.execute("CREATE INDEX IF NOT EXISTS ix_ai_month ON ai_attempts(month)")
                self._initialized = True
            invalid = conn.execute("""SELECT 1 FROM ai_attempts WHERE state NOT IN ('reserved','uncertain','estimated','released')
                OR reserved_krw<0 OR (state='estimated' AND (estimated_krw IS NULL OR estimated_krw<0)) LIMIT 1""").fetchone()
            if invalid is not None:
                raise ValueError()
            yield conn
            conn.commit()
        except AIFailure:
            if conn is not None:
                conn.rollback()
            raise
        except Exception:
            if conn is not None:
                conn.rollback()
            self._initialized = False
            raise AIFailure("ai_ledger_unavailable", "AI 비용 기록을 확인할 수 없어 전송을 중지했습니다") from None
        finally:
            if conn is not None:
                conn.close()

    @staticmethod
    def periods(now: datetime) -> tuple[str, str]:
        local = now.astimezone(KST)
        return local.strftime("%Y-%m-%d"), local.strftime("%Y-%m")

    @staticmethod
    def _period(conn, key: str, period: str) -> dict:
        row = conn.execute(f"""SELECT
            COALESCE(SUM(CASE WHEN state='estimated' THEN estimated_krw ELSE 0 END),0),
            COALESCE(SUM(CASE WHEN state='reserved' THEN reserved_krw ELSE 0 END),0),
            COALESCE(SUM(CASE WHEN state='uncertain' THEN reserved_krw ELSE 0 END),0),
            SUM(CASE WHEN state!='released' THEN 1 ELSE 0 END) FROM ai_attempts WHERE {key}=?""", (period,)).fetchone()
        return {"period": period, "estimated_krw": row[0], "reserved_krw": row[1], "uncertain_krw": row[2], "attempts": row[3] or 0}

    def status(self, now: datetime) -> dict:
        day, month = self.periods(now)
        with self._transaction() as conn:
            return {"day": self._period(conn, "day", day), "month": self._period(conn, "month", month)}

    @staticmethod
    def _cached(conn, cache_key: str) -> Claim | None:
        row = conn.execute("SELECT * FROM ai_attempts WHERE cache_key=? AND state!='released' ORDER BY created_at DESC,rowid DESC LIMIT 1", (cache_key,)).fetchone()
        if row is None:
            return None
        if row["state"] == "estimated" and row["payload"] and not row["error_code"]:
            return Claim("complete", row["id"], json.loads(row["payload"]), reserved_krw=row["reserved_krw"])
        kind = "busy" if row["state"] == "reserved" else "failed"
        return Claim(kind, row["id"], error_code=row["error_code"] or "billing_uncertain", reserved_krw=row["reserved_krw"])

    def lookup(self, cache_key: str) -> Claim | None:
        with self._transaction() as conn:
            return self._cached(conn, cache_key)

    def reserve(self, cache_key: str, guard_hash: str, amount: int, *, day_cap: int, month_cap: int, now: datetime, retry: bool = False) -> Claim:
        if any(type(v) is not int or v <= 0 for v in (amount, day_cap, month_cap)):
            raise AIFailure("budget_required", "양수 일일·월간 비용 상한이 필요합니다")
        day, month = self.periods(now)
        with self._transaction() as conn:
            cached = self._cached(conn, cache_key)
            if cached:
                if cached.kind == "complete":
                    return cached
                if not retry:
                    return cached
                # Explicit retry retains previous unknown cost and reserves anew.
                if cached.kind == "busy":
                    conn.execute("UPDATE ai_attempts SET state='uncertain',error_code='billing_uncertain' WHERE id=?", (cached.attempt_id,))
            for key, period, cap in (("day", day, day_cap), ("month", month, month_cap)):
                totals = self._period(conn, key, period)
                if sum(totals[k] for k in ("estimated_krw", "reserved_krw", "uncertain_krw")) + amount > cap:
                    raise AIFailure("budget_exhausted", "AI 비용 상한으로 전송을 중지했습니다")
            attempt = uuid4().hex
            conn.execute("INSERT INTO ai_attempts(id,cache_key,guard_hash,day,month,state,reserved_krw,created_at) VALUES(?,?,?,?,?,'reserved',?,?)",
                         (attempt, cache_key, guard_hash, day, month, amount, now.isoformat()))
            return Claim("reserved", attempt, reserved_krw=amount)

    def sent(self, attempt: str) -> None:
        # Written *before* HTTP; a crash at this point remains uncertain.
        with self._transaction() as conn:
            changed = conn.execute("UPDATE ai_attempts SET state='uncertain',error_code='billing_uncertain' WHERE id=? AND state='reserved'", (attempt,)).rowcount
            if changed != 1:
                raise AIFailure("ai_ledger_unavailable", "AI 비용 예약을 확인할 수 없어 전송을 중지했습니다")

    def release(self, attempt: str) -> None:
        with self._transaction() as conn:
            conn.execute("UPDATE ai_attempts SET state='released',error_code='not_sent' WHERE id=? AND state='reserved'", (attempt,))

    def settle(self, attempt: str, *, payload: dict | None, error_code: str | None,
               usage: tuple[int,int,int] | None, estimate: tuple[int,int] | None) -> None:
        with self._transaction() as conn:
            if usage is not None and estimate is not None:
                changed = conn.execute("""UPDATE ai_attempts SET state='estimated', payload=?,error_code=?,
                    input_tokens=?,output_tokens=?,cached_tokens=?,usd_micros=?,estimated_krw=? WHERE id=? AND state='uncertain'""",
                    (json.dumps(payload, ensure_ascii=False) if payload else None, error_code, *usage, *estimate, attempt)).rowcount
            else:
                observed = usage or (None, None, None)
                changed = conn.execute("UPDATE ai_attempts SET error_code=?,input_tokens=?,output_tokens=?,cached_tokens=? WHERE id=? AND state='uncertain'", (error_code or "billing_uncertain", *observed, attempt)).rowcount
            if changed != 1:
                raise AIFailure("ai_ledger_unavailable", "AI 비용 정산을 기록할 수 없습니다")
