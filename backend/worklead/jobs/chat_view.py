"""Ephemeral mirror of a user-selected, visible chat panel; no remote transport."""
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, StrictInt, model_validator


class ChatObservation(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    state: Literal["visible", "panel_closed", "surface_unrecognized", "login_required"]
    text: str | None = Field(default=None, min_length=1, max_length=4000)
    account_hash: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    job_id: str | None = Field(default=None, pattern=r"^[A-Za-z0-9]{12}$")
    sequence: StrictInt = Field(ge=1, le=9_007_199_254_740_991)

    @model_validator(mode="after")
    def coherent(self):
        if (self.state == "visible") != (self.text is not None):
            raise ValueError("visible text required only for a visible panel")
        if self.state != "login_required" and self.account_hash is None:
            raise ValueError("visible account required")
        if self.state != "visible" and self.job_id is not None:
            raise ValueError("no job outside panel")
        return self


class ChatView(BaseModel):
    enabled: bool = False
    state: Literal["off", "waiting", "visible", "panel_closed", "surface_unrecognized", "login_required", "stale", "account_changed"] = "off"
    text: str | None = None
    observed_at: datetime | None = None
    lead_id: str | None = None
    lead_title: str | None = None
    source: Literal["visible_chat_panel"] = "visible_chat_panel"
    persists_body: Literal[False] = False

