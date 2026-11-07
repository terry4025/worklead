"""Public control contract and narrowly scoped extension messages."""
from datetime import datetime
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, StrictInt


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class AutoApplyStartIn(StrictModel):
    confirm: Literal[True]
    remember_connection: bool = False
    min_score: StrictInt = Field(default=50, ge=0, le=100)
    daily_limit: StrictInt = Field(default=20, ge=1, le=200)


class AutoAttemptOut(BaseModel):
    id: str
    lead_id: str
    title: str
    state: Literal["claimed", "ready", "submitting", "submitted", "already_applied", "held", "uncertain"]
    reason_code: str | None
    created_at: datetime
    finished_at: datetime | None
    retry_count: int = 0
    retry_after: datetime | None = None


class AutoCandidateOut(BaseModel):
    lead_id: str
    title: str
    stage: Literal["queued", "source_refresh", "rejected", "form_review", "permit_issued", "submitted",
        "already_applied", "retry_wait", "held", "uncertain"]
    reason_code: str | None = None
    retry_count: int = 0
    retry_after: datetime | None = None
    updated_at: datetime | None = None
    refresh_state: str | None = None
    refresh_error_code: str | None = None
    last_checked_at: datetime | None = None


class AutoApplyStatus(BaseModel):
    provider: Literal["chrome", "managed_edge"] = "chrome"
    setup_stage: str | None = None
    browser_open: bool = False
    enabled: bool = False
    remembers_connection: bool = False
    paired: bool = False
    state: Literal["stopped", "waiting", "connected", "paused"] = "stopped"
    min_score: int = 50
    daily_limit: int = 20
    today_submitted: int = 0
    today_reserved: int = 0
    held: int = 0
    uncertain: int = 0
    last_connected_at: datetime | None = None
    error_code: str | None = None
    message: str = "자동 지원이 꺼져 있습니다"
    attempts: list[AutoAttemptOut] = Field(default_factory=list)
    queue_reason: str | None = None
    needs_user_action: bool = False
    retry_waiting: int = 0
    candidates: list[AutoCandidateOut] = Field(default_factory=list)


class AutoConnection(StrictModel):
    url: str
    token: str = Field(repr=False)
    purpose: Literal["auto_apply"] = "auto_apply"
    version: Literal[1] = 1


class AutoApplyStartOut(BaseModel):
    connection: AutoConnection | None
    status: AutoApplyStatus
    extension_directory: str


class NextIn(StrictModel):
    account_hash: str = Field(pattern=r"^[a-f0-9]{64}$")


class IdentityIn(NextIn):
    attempt_id: str = Field(min_length=1, max_length=40)
    lease_token: str = Field(min_length=32, max_length=128, repr=False)
    profile_hash: str = Field(pattern=r"^[a-f0-9]{64}$")


ReasonCode = Literal["ready", "already_applied", "questions_required", "profile_changed", "condition_conflict",
    "login_required", "account_changed", "closed", "ui_unrecognized", "navigation_failed",
    "submission_unconfirmed", "submitted", "cancelled", "browser_timeout", "connection_ended"]


class ReviewIn(IdentityIn):
    outcome: Literal["ready", "already_applied", "held"]
    reason_code: ReasonCode
    evidence: Literal["application_form"] | None = None


class ResultIn(IdentityIn):
    outcome: Literal["submitted", "uncertain", "held"]
    reason_code: ReasonCode
    evidence: Literal["cancel_button"] | None = None


class ReconcileIn(NextIn):
    attempt_id: str = Field(min_length=1, max_length=40)
    receipt_token: str = Field(min_length=32, max_length=128, repr=False)
    profile_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    evidence: Literal["cancel_button"]


class PauseIn(StrictModel):
    reason: Literal["login_required", "account_changed"]
