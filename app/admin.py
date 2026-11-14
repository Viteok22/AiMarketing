from __future__ import annotations

import asyncio
import io
import json
import logging
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import quote, urlparse

import requests
from bs4 import BeautifulSoup
from fastapi import APIRouter, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from pypdf import PdfReader

from app import runtime
from app.config import settings
from app.models import (
    CALENDAR_STATUS_CSS,
    CALENDAR_STATUS_LABELS,
    STATUS_CSS,
    STATUS_LABELS,
)

logger = logging.getLogger(__name__)
router = APIRouter()
templates = Jinja2Templates(directory=str(settings.base_dir / "app" / "templates"))


def _auth(request: Request) -> bool:
    return bool(request.session.get("auth"))


def _login_redirect() -> RedirectResponse:
    return RedirectResponse("/login", status_code=303)


def _company_payload(form) -> dict:
    fields = (
        "name",
        "description",
        "products",
        "audience",
        "advantages",
        "tone",
        "forbidden",
        "knowledge",
        "channel_id",
    )
    return {field: (form.get(field) or "").strip() for field in fields}


def _private_host(host: str) -> bool:
    host = (host or "").lower()
    if host in {"localhost", "127.0.0.1", "::1", "0.0.0.0"}:
        return True
    if host.startswith(("10.", "192.168.", "169.254.")):
        return True
    if host.startswith("172."):
        try:
            second = int(host.split(".", 2)[1])
            if 16 <= second <= 31:
                return True
        except (ValueError, IndexError):
            pass
    return False


def _fetch_site(url: str) -> str:
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"}:
        raise ValueError("Нужен адрес http:// или https://")
    if _private_host(parsed.hostname or ""):
        raise ValueError("Внутренние адреса запрещены")
    response = requests.get(
        url,
        timeout=15,
        headers={"User-Agent": "AI-Marketer-Hackathon/0.1"},
    )
    response.raise_for_status()
    soup = BeautifulSoup(response.text, "html.parser")
    for tag in soup(["script", "style", "nav", "footer", "header", "noscript"]):
        tag.decompose()
    text = " ".join(soup.get_text(" ").split())
    return text[:20000]


async def _read_upload(upload: UploadFile) -> str:
    data = await upload.read()
    if len(data) > 5 * 1024 * 1024:
        raise ValueError("Файл больше 5 МБ")
    name = (upload.filename or "").lower()
    if name.endswith(".pdf"):
        reader = PdfReader(io.BytesIO(data))
        return "\n".join((page.extract_text() or "") for page in reader.pages).strip()
    return data.decode("utf-8", errors="ignore").strip()


# --------------------------------------------------------------------- auth
@router.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    if _auth(request):
        return RedirectResponse("/", status_code=303)
    return templates.TemplateResponse(request, "login.html", {"error": ""})


@router.post("/login", response_class=HTMLResponse)
async def login_submit(request: Request):
    form = await request.form()
    password = (form.get("password") or "").strip()
    if password == settings.admin_password:
        request.session["auth"] = True
        return RedirectResponse("/", status_code=303)
    return templates.TemplateResponse(
        request,
        "login.html",
        {"error": "Неверный пароль"},
        status_code=401,
    )


@router.get("/logout")
async def logout(request: Request):
    request.session.clear()
    return RedirectResponse("/login", status_code=303)


# ---------------------------------------------------------------- dashboard
@router.get("/", response_class=HTMLResponse)
async def dashboard(request: Request):
    if not _auth(request):
        return _login_redirect()
    return templates.TemplateResponse(
        request,
        "dashboard.html",
        {
            "companies": runtime.store.list_companies(),
            "tasks": runtime.store.list_tasks(limit=20),
            "status_labels": STATUS_LABELS,
            "status_css": STATUS_CSS,
            "problems": settings.validate(),
            "channel": settings.telegram_channel_id,
            "generator_started": runtime.generator.started,
            "image_configured": runtime.generator.image_configured,
        },
    )


# ----------------------------------------------------------------- companies
@router.get("/companies/new", response_class=HTMLResponse)
async def company_new(request: Request):
    if not _auth(request):
        return _login_redirect()
    return templates.TemplateResponse(
        request,
        "company_form.html",
        {
            "company": None,
            "title": "Новая компания",
            "error": request.query_params.get("error", ""),
        },
    )


@router.post("/companies")
async def company_create(request: Request):
    if not _auth(request):
        return _login_redirect()
    form = await request.form()
    data = _company_payload(form)

    upload = form.get("file")
    if upload is not None and getattr(upload, "filename", ""):
        try:
            extracted = await _read_upload(upload)
            if extracted:
                data["knowledge"] = (data["knowledge"] + "\n\n" + extracted).strip()
        except Exception as exc:  # noqa: BLE001
            return RedirectResponse(
                f"/companies/new?error={quote(f'Не удалось прочитать файл: {exc}')}",
                status_code=303,
            )

    site_url = (form.get("site_url") or "").strip()
    if site_url:
        try:
            extracted = _fetch_site(site_url)
            if extracted:
                data["knowledge"] = (data["knowledge"] + "\n\n" + extracted).strip()
        except Exception as exc:  # noqa: BLE001
            return RedirectResponse(
                f"/companies/new?error={quote(f'Не удалось загрузить сайт: {exc}')}",
                status_code=303,
            )

    company = runtime.store.create_company(data)
    return RedirectResponse(f"/companies/{company.id}", status_code=303)


@router.get("/companies/{company_id}", response_class=HTMLResponse)
async def company_detail(request: Request, company_id: str):
    if not _auth(request):
        return _login_redirect()
    company = runtime.store.get_company(company_id)
    if company is None:
        return RedirectResponse("/", status_code=303)
    schedule = runtime.store.get_schedule(company_id)
    if schedule:
        for key in ("next_run_at", "last_run_at"):
            value = schedule.get(key)
            if value:
                try:
                    schedule[key + "_dt"] = datetime.fromisoformat(value).astimezone()
                except ValueError:
                    schedule[key + "_dt"] = None
    return templates.TemplateResponse(
        request,
        "company_detail.html",
        {
            "company": company,
            "tasks": runtime.store.list_tasks(company_id=company_id, limit=20),
            "status_labels": STATUS_LABELS,
            "status_css": STATUS_CSS,
            "channel": settings.telegram_channel_id,
            "schedule": schedule,
            "default_schedule_user_id": settings.admin_telegram_id,
            "error": request.query_params.get("error", ""),
            "ok": request.query_params.get("ok", ""),
        },
    )


def _week_start(value: str | None) -> datetime:
    today = datetime.now().astimezone().date()
    selected = today
    if value:
        try:
            selected = datetime.strptime(value[:10], "%Y-%m-%d").date()
        except ValueError:
            selected = today
    monday = selected - timedelta(days=selected.weekday())
    return datetime.combine(monday, datetime.min.time()).astimezone()


@router.get("/companies/{company_id}/calendar", response_class=HTMLResponse)
async def company_calendar(request: Request, company_id: str):
    if not _auth(request):
        return _login_redirect()
    company = runtime.store.get_company(company_id)
    if company is None:
        return RedirectResponse("/", status_code=303)

    week_start = _week_start(request.query_params.get("week"))
    week_end = week_start + timedelta(days=7)
    entries = runtime.store.list_calendar_entries(company_id, week_start, week_end)
    by_day: dict = {}
    for entry in entries:
        entry["status_label"] = CALENDAR_STATUS_LABELS.get(
            entry["status"], entry["status"]
        )
        entry["status_css"] = CALENDAR_STATUS_CSS.get(
            entry["status"], "status-drafting"
        )
        by_day.setdefault(entry["planned_at_dt"].date(), []).append(entry)

    weekdays = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"]
    today = datetime.now().astimezone().date()
    days = []
    for offset in range(7):
        day = (week_start + timedelta(days=offset)).date()
        days.append(
            {
                "date": day,
                "label": weekdays[day.weekday()],
                "is_today": day == today,
                "entries": by_day.get(day, []),
            }
        )

    users = runtime.store.list_company_users(company_id)
    default_user = users[0] if users else settings.admin_telegram_id
    recipients = sorted({*users, settings.admin_telegram_id} - {None})
    week_param_prev = (week_start - timedelta(days=7)).date().isoformat()
    week_param_next = (week_start + timedelta(days=7)).date().isoformat()
    return templates.TemplateResponse(
        request,
        "calendar.html",
        {
            "company": company,
            "days": days,
            "users": users,
            "recipients": recipients,
            "default_user": default_user,
            "week_start": week_start,
            "week_end": week_end - timedelta(days=1),
            "week_param_prev": week_param_prev,
            "week_param_next": week_param_next,
            "error": request.query_params.get("error", ""),
            "ok": request.query_params.get("ok", ""),
        },
    )


@router.post("/companies/{company_id}/calendar")
async def company_calendar_create(request: Request, company_id: str):
    if not _auth(request):
        return _login_redirect()
    company = runtime.store.get_company(company_id)
    if company is None:
        return RedirectResponse("/", status_code=303)
    form = await request.form()
    raw = (form.get("planned_at") or "").strip()
    topic = (form.get("topic") or "").strip()
    try:
        planned_at = datetime.strptime(raw, "%Y-%m-%dT%H:%M").astimezone()
    except ValueError:
        return RedirectResponse(
            f"/companies/{company_id}/calendar?error={quote('Укажите дату и время')}",
            status_code=303,
        )
    if not topic:
        return RedirectResponse(
            f"/companies/{company_id}/calendar?error={quote('Укажите тему поста')}",
            status_code=303,
        )
    allowed_users = set(runtime.store.list_company_users(company_id))
    if settings.admin_telegram_id:
        allowed_users.add(settings.admin_telegram_id)
    try:
        user_id = int(form.get("user_id") or 0) or None
    except ValueError:
        user_id = None
    if user_id is not None and user_id not in allowed_users:
        return RedirectResponse(
            f"/companies/{company_id}/calendar?error={quote('Получатель не привязан к этой компании')}",
            status_code=303,
        )
    if user_id is None:
        user_id = (
            runtime.store.list_company_users(company_id)[0]
            if runtime.store.list_company_users(company_id)
            else settings.admin_telegram_id
        )
    runtime.store.create_calendar_entry(
        company_id, planned_at, topic, user_id=user_id
    )
    week = planned_at.date().isoformat()
    return RedirectResponse(
        f"/companies/{company_id}/calendar?week={week}&ok={quote('Тема добавлена в календарь')}",
        status_code=303,
    )


@router.post("/companies/{company_id}/calendar/{entry_id}/run")
async def company_calendar_run(request: Request, company_id: str, entry_id: str):
    if not _auth(request):
        return _login_redirect()
    entry = runtime.store.get_calendar_entry(entry_id)
    if entry is None or entry["company_id"] != company_id:
        return RedirectResponse(f"/companies/{company_id}/calendar", status_code=303)
    if entry["status"] not in {"planned", "failed"}:
        return RedirectResponse(
            f"/companies/{company_id}/calendar?error={quote('Запись уже запущена, отменена или опубликована')}",
            status_code=303,
        )
    if runtime.scheduler is None:
        return RedirectResponse(
            f"/companies/{company_id}/calendar?error={quote('Telegram-бот не запущен')}",
            status_code=303,
        )
    asyncio.create_task(runtime.scheduler.run_calendar_entry_now(entry_id))
    week = entry["planned_at_dt"].date().isoformat()
    return RedirectResponse(
        f"/companies/{company_id}/calendar?week={week}&ok={quote('Генерация запущена: черновик придёт в Telegram')}",
        status_code=303,
    )


@router.post("/companies/{company_id}/calendar/{entry_id}/cancel")
async def company_calendar_cancel(request: Request, company_id: str, entry_id: str):
    if not _auth(request):
        return _login_redirect()
    entry = runtime.store.get_calendar_entry(entry_id)
    if entry is None or entry["company_id"] != company_id:
        return RedirectResponse(f"/companies/{company_id}/calendar", status_code=303)
    ok = runtime.store.cancel_calendar_entry(entry_id, company_id)
    week = entry["planned_at_dt"].date().isoformat()
    if ok:
        message = quote("Запись отменена")
        return RedirectResponse(
            f"/companies/{company_id}/calendar?week={week}&ok={message}",
            status_code=303,
        )
    message = quote("Нельзя отменить опубликованную запись")
    return RedirectResponse(
        f"/companies/{company_id}/calendar?week={week}&error={message}",
        status_code=303,
    )


@router.post("/companies/{company_id}")
async def company_update(request: Request, company_id: str):
    if not _auth(request):
        return _login_redirect()
    form = await request.form()
    runtime.store.update_company(company_id, _company_payload(form))
    return RedirectResponse(f"/companies/{company_id}?ok=Сохранено", status_code=303)


@router.post("/companies/{company_id}/upload")
async def company_upload(request: Request, company_id: str):
    if not _auth(request):
        return _login_redirect()
    company = runtime.store.get_company(company_id)
    if company is None:
        return RedirectResponse("/", status_code=303)
    form = await request.form()
    upload = form.get("file")
    site_url = (form.get("site_url") or "").strip()
    extra: list[str] = []
    try:
        if upload is not None and getattr(upload, "filename", ""):
            extracted = await _read_upload(upload)
            if extracted:
                extra.append(extracted)
        if site_url:
            extracted = _fetch_site(site_url)
            if extracted:
                extra.append(extracted)
    except Exception as exc:  # noqa: BLE001
        return RedirectResponse(
            f"/companies/{company_id}?error={quote(f'Не удалось загрузить материалы: {exc}')}",
            status_code=303,
        )

    if extra:
        company.knowledge = (company.knowledge + "\n\n" + "\n\n".join(extra)).strip()
    return RedirectResponse(f"/companies/{company_id}?ok=Материалы добавлены", status_code=303)


@router.post("/companies/{company_id}/bind")
async def company_bind(request: Request, company_id: str):
    if not _auth(request):
        return _login_redirect()
    form = await request.form()
    raw = (form.get("user_id") or "").strip()
    try:
        user_id = int(raw)
    except ValueError:
        return RedirectResponse(
            f"/companies/{company_id}?error={quote('Telegram ID должен быть числом')}",
            status_code=303,
        )
    if not runtime.store.bind_user(user_id, company_id):
        return RedirectResponse(
            f"/companies/{company_id}?error={quote('Компания не найдена')}",
            status_code=303,
        )
    return RedirectResponse(f"/companies/{company_id}?ok=Пользователь привязан", status_code=303)


# --------------------------------------------------------------------- misc
@router.get("/chatgpt/check")
async def chatgpt_check(request: Request):
    if not _auth(request):
        return _login_redirect()
    try:
        available = await runtime.generator.check_login()
        return JSONResponse(
            {
                "logged_in": available,
                "image_configured": runtime.generator.image_configured,
                "message": "DeepSeek API доступен" if available else "DeepSeek API не отвечает или ключ не задан",
            }
        )
    except Exception as exc:  # noqa: BLE001
        logger.exception("Ошибка проверки DeepSeek API")
        return JSONResponse({"logged_in": False, "error": str(exc)}, status_code=500)


@router.post("/demo/reset")
async def demo_reset(request: Request):
    if not _auth(request):
        return _login_redirect()
    runtime.store.reset()
    return RedirectResponse("/?ok=Демо сброшено", status_code=303)


def _selected_company(request: Request, allow_all: bool = True):
    companies = runtime.store.list_companies()
    raw = (request.query_params.get("company") or "").strip()
    if allow_all and raw == "all":
        return None, companies, "all"
    selected = runtime.store.get_company(raw) if raw else None
    if selected is None and companies:
        selected = companies[0]
    return selected, companies, (selected.id if selected else "")


@router.post("/companies/{company_id}/schedule")
async def company_schedule(request: Request, company_id: str):
    if not _auth(request):
        return _login_redirect()
    if runtime.store.get_company(company_id) is None:
        return RedirectResponse("/", status_code=303)
    form = await request.form()
    try:
        interval = int(form.get("interval_minutes") or 180)
    except ValueError:
        interval = 180
    raw_user = (form.get("user_id") or "").strip()
    try:
        user_id = int(raw_user) if raw_user else None
    except ValueError:
        user_id = None
    runtime.store.upsert_schedule(
        company_id=company_id,
        enabled=(form.get("enabled") == "on"),
        interval_minutes=interval,
        prompt=(form.get("prompt") or "").strip(),
        user_id=user_id,
    )
    return RedirectResponse(
        f"/companies/{company_id}?ok={quote('Расписание сохранено')}", status_code=303
    )


@router.post("/companies/{company_id}/schedule/run")
async def company_schedule_run(request: Request, company_id: str):
    if not _auth(request):
        return _login_redirect()
    if runtime.scheduler is None:
        return RedirectResponse(
            f"/companies/{company_id}?error={quote('Telegram-бот не запущен')}",
            status_code=303,
        )
    asyncio.create_task(runtime.scheduler.run_company_now(company_id))
    return RedirectResponse(
        f"/companies/{company_id}?ok={quote('Автопостинг запущен: черновик придёт в Telegram')}",
        status_code=303,
    )


@router.get("/analytics", response_class=HTMLResponse)
async def analytics(request: Request):
    if not _auth(request):
        return _login_redirect()
    selected_company, companies, selected_id = _selected_company(request, allow_all=True)
    company_id = selected_company.id if selected_company else None
    reports = []
    for report in runtime.store.list_reaction_reports(limit=50, company_id=company_id):
        data = dict(report)
        channel = data.get("channel_id") or ""
        data["link"] = (
            f"https://t.me/{channel.lstrip('@')}/{data['message_id']}"
            if channel.startswith("@")
            else ""
        )
        try:
            data["published_at_dt"] = datetime.fromisoformat(data["published_at"]).astimezone()
        except ValueError:
            data["published_at_dt"] = None
        reports.append(data)
    unlinked_posts = []
    for company in ([selected_company] if selected_company else companies):
        if company is None:
            continue
        for post in runtime.analytics_service.list_unlinked_posts(
            company.id, limit=10
        ):
            item = dict(post)
            item["company_name"] = company.name
            unlinked_posts.append(item)
    total_positive = sum(item["reactions"]["positive"] for item in reports)
    total_negative = sum(item["reactions"]["negative"] for item in reports)
    total_reactions = sum(item["reactions"]["total"] for item in reports)
    total_views = sum(int(item.get("views") or 0) for item in reports)
    insight = (
        runtime.store.get_latest_analytics_insight(company_id)
        if company_id
        else None
    )
    if insight:
        try:
            insight["created_at_dt"] = datetime.fromisoformat(
                insight["created_at"]
            ).astimezone()
        except ValueError:
            insight["created_at_dt"] = None
    return templates.TemplateResponse(
        request,
        "analytics.html",
        {
            "reports": reports,
            "unlinked_posts": unlinked_posts,
            "total_positive": total_positive,
            "total_negative": total_negative,
            "total_reactions": total_reactions,
            "total_views": total_views,
            "insight": insight,
            "companies": companies,
            "selected_company": selected_company,
            "selected_company_id": selected_id,
        },
    )


@router.post("/analytics/refresh")
async def analytics_refresh(request: Request):
    if not _auth(request):
        return _login_redirect()
    form = await request.form()
    raw = (form.get("company_id") or "").strip()
    company_id = None if raw in {"", "all"} else raw
    target = "all" if company_id is None else company_id
    try:
        updated = await runtime.analytics_service.refresh_metrics(company_id)
        message = quote(f"Метрики обновлены: {updated} публикаций")
        return RedirectResponse(
            f"/analytics?company={quote(target)}&ok={message}",
            status_code=303,
        )
    except Exception as exc:  # noqa: BLE001
        logger.exception("Ошибка обновления метрик")
        message = quote(str(exc))
        return RedirectResponse(
            f"/analytics?company={quote(target)}&error={message}",
            status_code=303,
        )


@router.post("/analytics/insight")
async def analytics_insight(request: Request):
    if not _auth(request):
        return _login_redirect()
    form = await request.form()
    company_id = (form.get("company_id") or "").strip()
    if not company_id or company_id == "all":
        return RedirectResponse(
            f"/analytics?error={quote('Выберите компанию для AI-вывода')}",
            status_code=303,
        )
    try:
        await runtime.analytics_service.refresh_insight(company_id)
        return RedirectResponse(
            f"/analytics?company={quote(company_id)}&ok={quote('AI-вывод готов')}",
            status_code=303,
        )
    except Exception as exc:  # noqa: BLE001
        logger.exception("Ошибка AI-вывода")
        return RedirectResponse(
            f"/analytics?company={quote(company_id)}&error={quote(str(exc))}",
            status_code=303,
        )


@router.get("/trends", response_class=HTMLResponse)
async def trends(request: Request):
    if not _auth(request):
        return _login_redirect()
    selected_company, companies, selected_id = _selected_company(request, allow_all=False)
    company_id = selected_company.id if selected_company else None
    sources = runtime.store.list_trend_sources(enabled_only=False)
    insight = runtime.store.get_latest_trend_insight(company_id)
    if insight:
        try:
            insight["created_at_dt"] = datetime.fromisoformat(
                insight["created_at"]
            ).astimezone()
        except ValueError:
            insight["created_at_dt"] = None
        insight["display_summary"] = runtime.trend_analyzer.display_summary(insight)
    raw_posts = runtime.store.list_trend_posts(limit=200)
    by_channel: dict[str, list[dict]] = {}
    for post in raw_posts:
        by_channel.setdefault(post["channel"], []).append(post)
    balanced: list[dict] = []
    for channel_posts in by_channel.values():
        balanced.extend(channel_posts[:2])
    balanced.sort(key=lambda post: post["score"], reverse=True)
    posts = []
    for post in balanced[:20]:
        data = dict(post)
        try:
            data["reactions"] = json.loads(data.get("reactions_json") or "{}")
        except json.JSONDecodeError:
            data["reactions"] = {}
        posts.append(data)
    return templates.TemplateResponse(
        request,
        "trends.html",
        {
            "sources": sources,
            "insight": insight,
            "posts": posts,
            "companies": companies,
            "selected_company": selected_company,
            "selected_company_id": selected_id,
            "ok": request.query_params.get("ok", ""),
            "error": request.query_params.get("error", ""),
        },
    )


@router.post("/trends/refresh")
async def trends_refresh(request: Request):
    if not _auth(request):
        return _login_redirect()
    form = await request.form()
    company_id = (form.get("company_id") or "").strip() or None
    suffix = f"&company={quote(company_id)}" if company_id else ""
    try:
        await runtime.trend_analyzer.refresh(company_id=company_id)
        return RedirectResponse(
            f"/trends?ok={quote('Анализ трендов обновлён')}{suffix}", status_code=303
        )
    except Exception as exc:  # noqa: BLE001
        logger.exception("Ошибка обновления трендов")
        return RedirectResponse(
            f"/trends?error={quote(str(exc))}{suffix}", status_code=303
        )


@router.post("/trends/sources")
async def trends_add_source(request: Request):
    if not _auth(request):
        return _login_redirect()
    form = await request.form()
    channel = (form.get("channel") or "").strip()
    if channel:
        runtime.store.add_trend_source(channel)
    return RedirectResponse(
        f"/trends?ok={quote('Канал добавлен')}", status_code=303
    )


@router.post("/trends/sources/delete")
async def trends_delete_source(request: Request):
    if not _auth(request):
        return _login_redirect()
    form = await request.form()
    channel = (form.get("channel") or "").strip()
    if channel:
        runtime.store.delete_trend_source(channel)
    return RedirectResponse(
        f"/trends?ok={quote('Канал удалён')}", status_code=303
    )


@router.get("/health")
async def health():
    insight = runtime.store.get_latest_trend_insight()
    return {
        "ok": True,
        "companies": len(runtime.store.companies),
        "tasks": len(runtime.store.tasks),
        "generator_started": runtime.generator.started,
        "image_configured": runtime.generator.image_configured,
        "bot_started": runtime.bot_started,
        "trends_last_refresh": insight["created_at"] if insight else None,
    }
