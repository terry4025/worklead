"""Cafe routes share the application's authentication and DB boundaries."""
from fastapi import APIRouter, Depends, Request
from . import schemas as S
from ..services import cafe as svc


def router(session_dependency):
    routes = APIRouter(prefix="/v1/cafe", tags=["cafe"])

    @routes.get("/status", response_model=S.CafeSnapshot)
    def status(request: Request, s=Depends(session_dependency)):
        return svc.get_status(s, request.app.state.ctx.clock())

    @routes.post("/consent", response_model=S.CafeConsentOut)
    def consent(body: S.CafeConsentIn, request: Request, s=Depends(session_dependency)):
        return svc.accept_consent(s, body.version, request.app.state.ctx.clock())

    @routes.get("/settings", response_model=S.CafeSettings)
    def settings(s=Depends(session_dependency)):
        return svc.get_settings(s)

    @routes.patch("/settings", response_model=S.CafeSettings)
    def update_settings(body: S.CafeSettingsPatch, s=Depends(session_dependency)):
        return svc.update_settings(s, body.model_dump(exclude_unset=True))

    @routes.get("/cafes", response_model=S.CafeList)
    def cafes(s=Depends(session_dependency)):
        return svc.list_cafes(s)

    @routes.post("/cafes", response_model=S.CafeEntry)
    def add(body: S.CafeUrlIn, request: Request, s=Depends(session_dependency)):
        return svc.entry_out(s, svc.add_cafe(s, body.url, request.app.state.ctx.clock()))

    @routes.patch("/cafes/{id_}", response_model=S.CafeEntry)
    def update(id_: str, body: S.CafeStatePatch, request: Request, s=Depends(session_dependency)):
        return svc.update_cafe(s, id_, body.status, request.app.state.ctx.clock())

    @routes.patch("/boards/{id_}", response_model=S.CafeBoardOut)
    def board(id_: str, body: S.CafeBoardPatch, s=Depends(session_dependency)):
        return svc.approve_board(s, id_, body.approved)

    @routes.get("/jobs", response_model=S.CafeJobList)
    def jobs(s=Depends(session_dependency)):
        return svc.list_jobs(s)

    @routes.get('/template', response_model=S.CafeTemplate)
    def template(s=Depends(session_dependency)):
        from ..cafe import posting
        return posting.template(s)

    @routes.get('/comments', response_model=S.CafeCommentList)
    def comments_list(s=Depends(session_dependency)):
        from ..cafe import comments
        return comments.listing(s)

    @routes.patch('/comments/{id_}', response_model=S.CafeCommentOut)
    def comments_update(id_: str, body: S.CafeCommentPatch, request: Request, s=Depends(session_dependency)):
        from ..cafe import comments
        return comments.update(s, id_, body, request.app.state.ctx.clock())

    @routes.post('/comments/{id_}/lead', response_model=S.LeadDetail)
    def comments_lead(id_: str, request: Request, s=Depends(session_dependency)):
        from ..cafe import comments
        from ..services.leads import detail
        ctx = request.app.state.ctx
        return detail(s, comments.make_lead(s, id_, ctx.clock(), ctx.provider))

    @routes.post('/comments/{id_}/draft', response_model=S.CafeCommentDraft)
    def comments_draft(id_: str, request: Request, s=Depends(session_dependency)):
        from ..cafe import comments
        ctx = request.app.state.ctx
        return comments.draft(s, id_, ctx.clock(), ctx.provider)

    @routes.patch('/template', response_model=S.CafeTemplate)
    def template_patch(body: S.CafeTemplatePatch, s=Depends(session_dependency)):
        from ..cafe import posting
        return posting.update_template(s, body)

    @routes.get('/attempts', response_model=S.CafeAttemptList)
    def attempts(s=Depends(session_dependency)):
        from ..cafe import posting
        return posting.list_attempts(s)

    @routes.post('/attempts/{id_}/repost', response_model=S.CafeJob, status_code=202)
    def repost(id_: str, body: S.CafeRepostIn, request: Request, s=Depends(session_dependency)):
        from ..models import CafePostAttempt
        row = s.get(CafePostAttempt, id_)
        if row is None or row.status != 'removed' or not row.post_url:
            raise svc.CafeError(409, 'cafe_repost_unverified', '삭제·숨김이 확인된 게시 기록만 직접 재게시할 수 있습니다')
        return svc.job_out(svc.enqueue_job(s, 'post', request.app.state.ctx.clock(), {'cafe_id': row.cafe_id, 'repost': True}))

    @routes.post('/cafes/{id_}/post', response_model=S.CafeJob, status_code=202)
    def post(id_: str, request: Request, s=Depends(session_dependency)):
        return svc.job_out(svc.enqueue_job(s, 'post', request.app.state.ctx.clock(), {'cafe_id': id_}))

    @routes.post("/actions/{kind}", response_model=S.CafeJob, status_code=202)
    def action(kind: str, request: Request, s=Depends(session_dependency)):
        return svc.job_out(svc.enqueue_job(s, kind, request.app.state.ctx.clock()))

    return routes
