"""Website builder API — AI-generated SEO marketing pages.

  POST   /site/pages                      create + queue generation
  GET    /site/pages                      list (filterable)
  GET    /site/pages/{id}                 full record, html_content included
  POST   /site/pages/{id}/regenerate      rewrite it
  PATCH  /site/pages/{id}/publish         make it live + re-export
  PATCH  /site/pages/{id}/unpublish       archive it
  GET    /site/pages/{id}/seo             score it, persist the score
  POST   /site/export                     ADMIN: write our marketing pages to disk
  GET    /site/pages/{id}/download        the HTML as a file
  POST   /site/pages/bulk                 up to 10 at once, all-or-nothing
  GET    /site/sitemap.xml                PUBLIC
  GET    /site/robots.txt                 PUBLIC

THE TWO PUBLIC ROUTES are the only unauthenticated endpoints here, and they
have to be: Googlebot does not carry a bearer token. Neither reveals anything
an anonymous visitor could not get by crawling the site itself — published
slugs and their dates. Draft and archived pages appear in neither.

OWNERSHIP. Pages are scoped by `workspace_id`, and workspace_id NULL means a
global page: our own marketing site, which belongs to no customer. A user
reaches their own workspace's pages; only an admin also reaches the global
ones. Anything else is 404, never 403, matching every other router here. A
non-admin with no workspace has nowhere to put a page, so creating one is
refused (409 workspace_required) instead of landing in the global bucket,
where every other workspaceless user and the marketing site would share it.
The export, the public sitemap and robots.txt cover the global pages only:
they describe the marketing site, and a customer's page is not on it.

GENERATION IS ALWAYS ASYNC. Writing a page is the largest model call in this
system. It never happens on the request thread -- create and regenerate both
return immediately with the page in `draft` and the work queued.
"""

# NOTE: deliberately NO `from __future__ import annotations` -- with postponed
# evaluation FastAPI resolves a `-> None` return annotation to the NoneType
# class, which is truthy, and every 204 route in a module then fails at import.

import re
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import false, func, or_, select
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, require_admin
from app.api.metering import refuse_if_daily_limit_reached
from app.core.plans import PlanLimitExceeded, check_feature, check_plan_limit, plan_limit_response
from app.core.rate_limiting import enforce_rate_limit
from app.db.base import get_db
from app.db.models import PageStatus, PageType, SitePage, User, Workspace
from app.services import sitemap as sitemap_service
from app.services import website_builder

router = APIRouter(prefix="/site", tags=["website-builder"])

_AI_LIMIT = "RATE_LIMIT_AI_ACTION"
MAX_BULK_PAGES = 10

# A slug is a URL path: lowercase, digits, hyphens and internal slashes. The
# empty string is legal and means the homepage. No leading slash (the path is
# joined on), no trailing slash, no uppercase (two casings of one URL is a
# duplicate-content penalty), no spaces, no dots.
SLUG_PATTERN = re.compile(r"^[a-z0-9][a-z0-9\-/]*$")


def _validate_slug(value: str) -> str:
    value = (value or "").strip()
    if value == "":
        return value          # the homepage
    if len(value) > 200:
        raise ValueError("slug must be at most 200 characters")
    if not SLUG_PATTERN.match(value):
        raise ValueError(
            "slug must be lowercase letters, digits, hyphens and slashes only, "
            'with no leading slash (e.g. "blog/agency-pipeline")')
    if value.endswith("/") or "//" in value:
        raise ValueError("slug must not end with a slash or contain an empty segment")
    return value


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------


class PageCreateIn(BaseModel):
    page_type: PageType
    slug: str = Field(max_length=200)
    title: str = Field(min_length=1, max_length=200)
    meta_title: str = Field(min_length=1, max_length=70)
    meta_description: str = Field(min_length=1, max_length=165)
    target_keyword: str = Field(min_length=1, max_length=200)
    secondary_keywords: list[str] = Field(default_factory=list, max_length=20)
    # 50 characters is the floor at which a brief says anything the generator
    # can act on. Below that every page comes back generic.
    brief: str = Field(min_length=50, max_length=8000)
    auto_generate: bool = True

    @field_validator("slug")
    @classmethod
    def _slug(cls, value):
        return _validate_slug(value)


class BulkPageCreateIn(BaseModel):
    pages: list[PageCreateIn] = Field(min_length=1, max_length=MAX_BULK_PAGES)


class PageOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    slug: str
    page_type: PageType
    title: str
    meta_title: str
    meta_description: str
    target_keyword: str
    status: PageStatus
    seo_score: int | None
    word_count: int | None
    generation_version: int
    published_at: datetime | None
    created_at: datetime
    # "queued" until the first generation lands, then "complete". "failed" is
    # not inferable from the row alone -- a page that was queued and failed
    # looks exactly like one still waiting -- so it is reported as queued and
    # the worker log is the source of truth for failures.
    generation_status: str


class PageDetailOut(PageOut):
    brief: str
    secondary_keywords: list
    html_content: str
    reading_time_mins: int | None
    schema_markup_json: dict | None
    internal_links_json: list
    seo_issues_json: list
    last_generated_at: datetime | None


class SeoScoreOut(BaseModel):
    score: int
    issues: list[str]
    target_keyword: str
    word_count: int | None


def _generation_status(page: SitePage) -> str:
    return "complete" if (page.html_content or "").strip() else "queued"


def _out(page: SitePage) -> PageOut:
    return PageOut(
        id=page.id, slug=page.slug, page_type=page.page_type, title=page.title,
        meta_title=page.meta_title, meta_description=page.meta_description,
        target_keyword=page.target_keyword, status=page.status,
        seo_score=page.seo_score, word_count=page.word_count,
        generation_version=page.generation_version, published_at=page.published_at,
        created_at=page.created_at, generation_status=_generation_status(page),
    )


def _detail_out(page: SitePage) -> PageDetailOut:
    return PageDetailOut(
        **_out(page).model_dump(),
        brief=page.brief or "",
        secondary_keywords=list(page.secondary_keywords or []),
        html_content=page.html_content or "",
        reading_time_mins=page.reading_time_mins,
        schema_markup_json=page.schema_markup_json,
        internal_links_json=list(page.internal_links_json or []),
        seo_issues_json=list(page.seo_issues_json or []),
        last_generated_at=page.last_generated_at,
    )


# ---------------------------------------------------------------------------
# Ownership
# ---------------------------------------------------------------------------


def _workspace_id_for(db: Session, current_user: User):
    """The workspace this user's pages belong to, or None when they own none."""
    return db.execute(
        select(Workspace.id).where(Workspace.owner_user_id == current_user.id)
    ).scalars().first()


def _is_admin(request: Request, current_user: User) -> bool:
    """Whether the person signed in may manage our own marketing pages.

    The same test as every admin route (require_admin): the signed-in actor,
    not the workspace owner they act for, and never through a personal API key.
    """
    try:
        require_admin(request, current_user)
    except HTTPException:
        return False
    return True


def _visible_pages_filter(workspace_id, admin: bool):
    """Pages this user may see: their workspace's, plus the global ones for an
    admin. A non-admin with no workspace sees none."""
    clauses = []
    if workspace_id is not None:
        clauses.append(SitePage.workspace_id == workspace_id)
    if admin:
        clauses.append(SitePage.workspace_id.is_(None))
    return or_(*clauses) if clauses else false()


def _target_workspace_id(db: Session, request: Request, current_user: User):
    """Where this user's new pages go: their workspace, or the global bucket
    for an admin with none. Anyone else is refused before anything is written."""
    workspace_id = _workspace_id_for(db, current_user)
    if workspace_id is None and not _is_admin(request, current_user):
        raise HTTPException(
            status_code=409,
            detail={"error": "workspace_required",
                    "message": "Create a workspace first. Website pages belong "
                               "to a workspace."})
    return workspace_id


def _owned_page(db: Session, page_id: uuid.UUID, current_user: User,
                request: Request) -> SitePage:
    """A page this user may act on, else 404 (never 403)."""
    workspace_id = _workspace_id_for(db, current_user)
    page = db.execute(
        select(SitePage).where(
            SitePage.id == page_id,
            _visible_pages_filter(workspace_id, _is_admin(request, current_user)))
    ).scalars().first()
    if page is None:
        raise HTTPException(status_code=404, detail="page not found")
    return page


def _gate(current_user: User) -> None:
    try:
        check_feature(current_user, "website_builder")
    except PlanLimitExceeded as exc:
        raise HTTPException(status_code=402, detail=plan_limit_response(exc)) from exc


def _gate_count(db: Session, current_user: User, workspace_id, adding: int = 1) -> None:
    """The plan's page limit, counted over the bucket the new pages go into."""
    bucket = (SitePage.workspace_id.is_(None) if workspace_id is None
              else SitePage.workspace_id == workspace_id)
    current = db.execute(
        select(func.count(SitePage.id)).where(bucket)
    ).scalar_one() or 0
    try:
        # `adding - 1` so a bulk request for 3 pages against a 10-page plan
        # with 8 used is refused up front, not after creating two of them.
        check_plan_limit(current_user, "max_site_pages", current + adding - 1)
    except PlanLimitExceeded as exc:
        raise HTTPException(status_code=402, detail=plan_limit_response(exc)) from exc


def _taken_slugs(db: Session, slugs: list[str]) -> list[str]:
    """Which of `slugs` already exist. Slug is globally unique -- it is a URL."""
    if not slugs:
        return []
    return list(db.execute(
        select(SitePage.slug).where(SitePage.slug.in_(slugs))
    ).scalars().all())


def _new_page(body: PageCreateIn, workspace_id) -> SitePage:
    return SitePage(
        workspace_id=workspace_id,
        page_type=body.page_type,
        slug=body.slug,
        title=body.title,
        meta_title=body.meta_title,
        meta_description=body.meta_description,
        target_keyword=body.target_keyword,
        brief=body.brief,
        secondary_keywords=list(body.secondary_keywords or []),
        html_content="",
        status=PageStatus.DRAFT,
    )


# ---------------------------------------------------------------------------
# 1 — Create
# ---------------------------------------------------------------------------


@router.post("/pages", response_model=PageOut, status_code=201)
def create_page(body: PageCreateIn, request: Request, db: Session = Depends(get_db),
                current_user: User = Depends(get_current_user)) -> PageOut:
    """Create a page and queue its generation.

    Returns immediately with the page in `draft` and empty html_content;
    `generation_status` is "queued" until the worker fills it in. Writing a
    page is the largest model call in this system and never runs on the
    request thread.
    """
    enforce_rate_limit(str(current_user.id), "ai_action", _AI_LIMIT)
    _gate(current_user)
    workspace_id = _target_workspace_id(db, request, current_user)
    _gate_count(db, current_user, workspace_id)

    if _taken_slugs(db, [body.slug]):
        raise HTTPException(status_code=409, detail="slug already exists")
    if body.auto_generate:
        refuse_if_daily_limit_reached(db, current_user.id)  # P0-5b

    page = _new_page(body, workspace_id)
    db.add(page)
    db.commit()

    if body.auto_generate:
        from app.workers import site_tasks  # noqa: PLC0415

        site_tasks.enqueue_generation(page.id, current_user.id)
    return _out(page)


# ---------------------------------------------------------------------------
# 2 — List
# ---------------------------------------------------------------------------


@router.get("/pages", response_model=list[PageOut])
def list_pages(request: Request,
               page_type: PageType | None = None,
               status: PageStatus | None = None,
               limit: int = Query(default=20, ge=1, le=100),
               offset: int = Query(default=0, ge=0),
               db: Session = Depends(get_db),
               current_user: User = Depends(get_current_user)) -> list[PageOut]:
    """This user's pages, newest first."""
    where = [_visible_pages_filter(_workspace_id_for(db, current_user),
                                   _is_admin(request, current_user))]
    if page_type is not None:
        where.append(SitePage.page_type == page_type)
    if status is not None:
        where.append(SitePage.status == status)
    pages = db.execute(
        select(SitePage).where(*where)
        .order_by(SitePage.created_at.desc()).limit(limit).offset(offset)
    ).scalars().all()
    return [_out(page) for page in pages]


# ---------------------------------------------------------------------------
# 3 — Detail
# ---------------------------------------------------------------------------


@router.get("/pages/{page_id}", response_model=PageDetailOut)
def get_page(page_id: uuid.UUID, request: Request,
             db: Session = Depends(get_db),
             current_user: User = Depends(get_current_user)) -> PageDetailOut:
    """The whole record, html_content included."""
    return _detail_out(_owned_page(db, page_id, current_user, request))


# ---------------------------------------------------------------------------
# 4 — Regenerate
# ---------------------------------------------------------------------------


@router.post("/pages/{page_id}/regenerate")
def regenerate_page(page_id: uuid.UUID, request: Request,
                    db: Session = Depends(get_db),
                    current_user: User = Depends(get_current_user)) -> dict:
    """Rewrite the page from its stored brief.

    The CURRENT html_content is left in place while the new one is written.
    A published page therefore keeps serving the version that worked until
    the replacement has been generated in full, and a failed regeneration
    costs nothing (app/workers/site_tasks.py writes only on success).
    """
    enforce_rate_limit(str(current_user.id), "ai_action", _AI_LIMIT)
    _gate(current_user)
    page = _owned_page(db, page_id, current_user, request)
    refuse_if_daily_limit_reached(db, current_user.id)  # P0-5b

    page.generation_version = (page.generation_version or 1) + 1
    db.commit()

    from app.workers import site_tasks  # noqa: PLC0415

    site_tasks.enqueue_generation(page.id, current_user.id)
    return {"status": "queued", "generation_version": page.generation_version}


# ---------------------------------------------------------------------------
# 5 / 6 — Publish, unpublish
# ---------------------------------------------------------------------------


@router.patch("/pages/{page_id}/publish", response_model=PageOut)
def publish_page(page_id: uuid.UUID, request: Request,
                 db: Session = Depends(get_db),
                 current_user: User = Depends(get_current_user)) -> PageOut:
    """Make the page live, and re-export the static site if it is one of ours.

    A page with no html_content is refused: publishing an empty document
    would put a blank page on a public URL and into the sitemap, and the
    generation that fills it may simply not have run yet.
    """
    page = _owned_page(db, page_id, current_user, request)
    if not (page.html_content or "").strip():
        raise HTTPException(
            status_code=409,
            detail="this page has not been generated yet — nothing to publish")

    page.status = PageStatus.PUBLISHED
    page.published_at = page.published_at or datetime.now(timezone.utc)
    db.commit()

    if page.workspace_id is None:  # only our own pages are in the export
        from app.workers import site_tasks  # noqa: PLC0415

        site_tasks.enqueue_export()
    return _out(page)


@router.patch("/pages/{page_id}/unpublish", response_model=PageOut)
def unpublish_page(page_id: uuid.UUID, request: Request,
                   db: Session = Depends(get_db),
                   current_user: User = Depends(get_current_user)) -> PageOut:
    """Archive the page. It leaves the sitemap and the next static export."""
    page = _owned_page(db, page_id, current_user, request)
    page.status = PageStatus.ARCHIVED
    db.commit()
    return _out(page)


# ---------------------------------------------------------------------------
# 7 — SEO score
# ---------------------------------------------------------------------------


@router.get("/pages/{page_id}/seo", response_model=SeoScoreOut)
def get_page_seo(page_id: uuid.UUID, request: Request,
                 db: Session = Depends(get_db),
                 current_user: User = Depends(get_current_user)) -> SeoScoreOut:
    """Score the page's HTML and persist the result.

    Recomputed on every call rather than read from the stored column: the
    scorer is pure Python over a string, it costs nothing, and a score that
    silently describes an older version of the page is worse than no score.
    """
    page = _owned_page(db, page_id, current_user, request)
    result = website_builder.score_page_seo(page.html_content or "",
                                            page.target_keyword)
    page.seo_score = result["score"]
    page.seo_issues_json = result["issues"]
    db.commit()
    return SeoScoreOut(score=result["score"], issues=result["issues"],
                       target_keyword=page.target_keyword,
                       word_count=page.word_count)


# ---------------------------------------------------------------------------
# 8 — Export
# ---------------------------------------------------------------------------


@router.post("/export")
def export_site(current_user: User = Depends(require_admin)) -> dict:
    """Queue a static export of our marketing site (admins only)."""
    _gate(current_user)
    from app.workers import site_tasks  # noqa: PLC0415

    site_tasks.enqueue_export()
    return {"status": "queued", "message": "Export started"}


# ---------------------------------------------------------------------------
# 9 — Download
# ---------------------------------------------------------------------------


@router.get("/pages/{page_id}/download", response_class=Response,
            responses={200: {"content": {"text/html": {}},
                             "description": "The page as an HTML file."}})
def download_page(page_id: uuid.UUID, request: Request,
                  db: Session = Depends(get_db),
                  current_user: User = Depends(get_current_user)) -> Response:
    """The page's HTML as a file download."""
    page = _owned_page(db, page_id, current_user, request)
    # The slug is a path ("blog/x") and the homepage's is empty; neither is a
    # filename. Flatten and fall back so Content-Disposition is always valid.
    filename = (page.slug or "index").strip("/").replace("/", "-") or "index"
    return Response(
        content=(page.html_content or "").encode("utf-8"),
        media_type="text/html",
        headers={"Content-Disposition": f'attachment; filename="{filename}.html"'},
    )


# ---------------------------------------------------------------------------
# 10 — Bulk create
# ---------------------------------------------------------------------------


@router.post("/pages/bulk", response_model=list[PageOut], status_code=201)
def bulk_create_pages(body: BulkPageCreateIn, request: Request,
                      db: Session = Depends(get_db),
                      current_user: User = Depends(get_current_user)) -> list[PageOut]:
    """Create up to 10 pages at once. All or nothing.

    Every slug is checked -- against the database AND against the rest of the
    request -- before a single row is written. A partial bulk create would
    leave the caller unable to retry: the same payload would then fail on the
    pages that succeeded the first time.
    """
    enforce_rate_limit(str(current_user.id), "ai_action", _AI_LIMIT)
    _gate(current_user)
    workspace_id = _target_workspace_id(db, request, current_user)
    _gate_count(db, current_user, workspace_id, adding=len(body.pages))

    slugs = [page.slug for page in body.pages]
    duplicates = sorted({slug for slug in slugs if slugs.count(slug) > 1})
    conflicts = sorted(set(_taken_slugs(db, slugs)) | set(duplicates))
    if conflicts:
        raise HTTPException(
            status_code=409,
            detail={"error": "slug_conflict",
                    "message": "these slugs already exist or repeat in the request",
                    "conflicting_slugs": conflicts})

    if any(page.auto_generate for page in body.pages):
        refuse_if_daily_limit_reached(db, current_user.id)  # P0-5b
    created = [_new_page(page, workspace_id) for page in body.pages]
    db.add_all(created)
    db.commit()

    from app.workers import site_tasks  # noqa: PLC0415

    for requested, page in zip(body.pages, created):
        if requested.auto_generate:
            site_tasks.enqueue_generation(page.id, current_user.id)
    return [_out(page) for page in created]


# ---------------------------------------------------------------------------
# 11 / 12 — Public: sitemap.xml and robots.txt
# ---------------------------------------------------------------------------
#
# UNAUTHENTICATED on purpose -- Googlebot carries no bearer token. Neither
# exposes anything beyond what crawling the published site would reveal:
# published slugs and their dates. Draft and archived pages appear in neither.


@router.get("/sitemap.xml", response_class=Response,
            responses={200: {"content": {"application/xml": {}},
                             "description": "sitemaps.org XML."}})
def sitemap_xml(db: Session = Depends(get_db)) -> Response:
    """The sitemap for our published pages, plus the three static URLs."""
    return Response(content=sitemap_service.generate_sitemap_xml(db),
                    media_type="application/xml")


@router.get("/robots.txt", response_class=Response,
            responses={200: {"content": {"text/plain": {}},
                             "description": "robots.txt."}})
def robots_txt() -> Response:
    """robots.txt: allow everything, point at the sitemap."""
    return Response(content=sitemap_service.generate_robots_txt(),
                    media_type="text/plain")
