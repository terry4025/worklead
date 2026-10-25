"""Public control contract and narrowly scoped extension messages."""
from datetime import datetime
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, StrictInt


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class AutoApplyStartIn(StrictModel):
    confirm: Literal[True]
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


class AutoApplyStatus(BaseModel):
    enabled: bool = False
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


class AutoConnection(StrictModel):
    url: str
    token: str = Field(repr=False)


class AutoApplyStartOut(BaseModel):
    connection: AutoConnection
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
    "submission_unconfirmed", "submitted", "cancelled"]


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
