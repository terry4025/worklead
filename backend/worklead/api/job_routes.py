"""Authenticated local draft-only application queue; no remote submission."""
from fastapi import APIRouter, Depends, Query, Request
from . import schemas as S
from ..jobs import applications as svc


def router(session_dependency):
    routes = APIRouter(prefix="/v1/jobs/applications", tags=["job-applications"])

    @routes.get("", response_model=S.JobApplicationList)
    def listing(request: Request, limit: int = Query(default=50, ge=1, le=100), s=Depends(session_dependency)):
        return svc.listing(s, request.app.state.ctx.clock(), limit)

    @routes.get("/{lead_id}", response_model=S.JobApplicationCandidate)
    def get(lead_id: str, request: Request, s=Depends(session_dependency)):
        return svc.get(s, lead_id, request.app.state.ctx.clock())

    @routes.post("/{lead_id}/prepare", response_model=S.JobApplicationCandidate)
    def prepare(lead_id: str, request: Request, s=Depends(session_dependency)):
        return svc.prepare(s, lead_id, request.app.state.ctx.clock())

    @routes.patch("/{lead_id}/draft", response_model=S.JobApplicationCandidate)
    def save(lead_id: str, body: S.JobApplicationDraftPatch, request: Request, s=Depends(session_dependency)):
        return svc.save(s, lead_id, body, request.app.state.ctx.clock())

    @routes.post("/{lead_id}/submit", response_model=S.JobApplicationCandidate)
    def submit(lead_id: str, request: Request, s=Depends(session_dependency)):
        return svc.submit(s, lead_id, request.app.state.ctx.clock())

    return routes
