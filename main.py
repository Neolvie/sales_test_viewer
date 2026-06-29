import os
import time
import logging
import threading
from collections import defaultdict
from typing import List, Optional

from dotenv import load_dotenv
load_dotenv()

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
)
log = logging.getLogger("salestester")

from fastapi import FastAPI, Depends, HTTPException, Header, Request, UploadFile, File, Form
from fastapi.responses import FileResponse, StreamingResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from sqlalchemy.orm import Session
from sqlalchemy import desc, asc, and_, or_, func as sql_func

from database import get_engine, SessionLocal, Theme, Prompt, Session as DBSession, PromptTest
from services import (
    transcribe_audio,
    evaluate_answer_async,
    extract_score_from_result,
    DEFAULT_EVALUATION_PROMPT,
)


app = FastAPI()

ADMIN_LOGIN = os.getenv("ADMIN_LOGIN", "")
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "")
TEST_TIMEOUT_MINUTES = int(os.getenv("TEST_TIMEOUT_MINUTES", "20"))
ROOT_PATH = os.getenv("ROOT_PATH", "").rstrip("/")

# Единые логин/пароль для входа на страницу тестирования (один на всех).
USER_LOGIN = os.getenv("USER_LOGIN", "")
USER_PASSWORD = os.getenv("USER_PASSWORD", "")


import re as _re

_ASSET_ATTR_RE = _re.compile(r'(src|href)="([^"]+\.(?:js|css))"')


def _bust_cache(content: str) -> str:
    """Дописывает ?v=<mtime> к ссылкам на локальные js/css, чтобы браузер сам
    подтягивал свежую версию после изменения файла (без Ctrl+Shift+R)."""
    def repl(m):
        attr, url = m.group(1), m.group(2)
        # пропускаем внешние и абсолютные ссылки и уже версионированные
        if url.startswith(("http://", "https://", "//", "/")) or "?" in url:
            return m.group(0)
        path = os.path.join("static", url)
        try:
            ver = int(os.path.getmtime(path))
        except OSError:
            return m.group(0)
        return f'{attr}="{url}?v={ver}"'

    return _ASSET_ATTR_RE.sub(repl, content)


def _serve_html(filename: str) -> HTMLResponse:
    """Serve an HTML file with window.BASE_PATH injected."""
    with open(f"static/{filename}", "r", encoding="utf-8") as f:
        content = f.read()
    script = f'<script>window.BASE_PATH="{ROOT_PATH}";</script>'
    content = content.replace("</head>", script + "\n</head>", 1)
    content = _bust_cache(content)
    # сам HTML не кэшируем, чтобы новые ?v= всегда доходили до браузера
    return HTMLResponse(content, headers={"Cache-Control": "no-cache, must-revalidate"})


@app.get("/api/config")
def get_config():
    """Public endpoint returning frontend configuration."""
    return {"test_timeout_minutes": TEST_TIMEOUT_MINUTES}


@app.on_event("startup")
def create_default_prompt():
    """Создаёт активный дефолтный промт при первом запуске, если промтов ещё нет."""
    db = SessionLocal()
    try:
        if db.query(Prompt).count() == 0:
            p = Prompt(version=1, content=DEFAULT_EVALUATION_PROMPT, is_active=True, is_draft=False,
                       notes="Дефолтный промт из n8n-бота")
            db.add(p)
            db.commit()
    except Exception as e:
        print(f"Startup: could not create default prompt: {e}")
    finally:
        db.close()


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


# ---------- Простая защита от перебора паролей (in-memory, по IP) ----------
MAX_FAILED_ATTEMPTS = 5          # сколько неудачных попыток разрешено
LOCKOUT_SECONDS = 5 * 60         # на сколько блокируем IP после превышения
_failed_attempts = defaultdict(list)  # ip -> список timestamp'ов неудачных попыток
_attempts_lock = threading.Lock()


def _client_ip(request: Request) -> str:
    # За nginx реальный адрес приходит в X-Forwarded-For
    xff = request.headers.get("x-forwarded-for")
    if xff:
        return xff.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def _check_not_locked(ip: str):
    now = time.time()
    with _attempts_lock:
        attempts = [t for t in _failed_attempts.get(ip, []) if now - t < LOCKOUT_SECONDS]
        _failed_attempts[ip] = attempts
        if len(attempts) >= MAX_FAILED_ATTEMPTS:
            retry = int(LOCKOUT_SECONDS - (now - attempts[0])) + 1
            raise HTTPException(
                status_code=429,
                detail=f"Слишком много попыток входа. Попробуйте через {retry} сек.",
                headers={"Retry-After": str(retry)},
            )


def _record_failure(ip: str):
    with _attempts_lock:
        _failed_attempts[ip].append(time.time())


def _record_success(ip: str):
    with _attempts_lock:
        _failed_attempts.pop(ip, None)


def require_admin(request: Request, authorization: Optional[str] = Header(None)):
    if not ADMIN_LOGIN or not ADMIN_PASSWORD:
        raise HTTPException(status_code=503, detail="ADMIN_LOGIN/ADMIN_PASSWORD not configured")
    ip = _client_ip(request)
    _check_not_locked(ip)
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing or invalid Authorization header")
    token = authorization[7:].strip()
    login, sep, password = token.partition(":")
    if not sep or login != ADMIN_LOGIN or password != ADMIN_PASSWORD:
        _record_failure(ip)
        raise HTTPException(status_code=401, detail="Invalid login or password")
    _record_success(ip)
    return True


def require_user(request: Request, authorization: Optional[str] = Header(None)):
    """Авторизация для страницы тестирования: единые логин/пароль на всех
    (USER_LOGIN/USER_PASSWORD из .env). Защита от перебора — та же in-memory по IP."""
    if not USER_LOGIN or not USER_PASSWORD:
        raise HTTPException(status_code=503, detail="USER_LOGIN/USER_PASSWORD not configured")
    ip = _client_ip(request)
    _check_not_locked(ip)
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing or invalid Authorization header")
    token = authorization[7:].strip()
    login, sep, password = token.partition(":")
    if not sep or login != USER_LOGIN or password != USER_PASSWORD:
        _record_failure(ip)
        raise HTTPException(status_code=401, detail="Invalid login or password")
    _record_success(ip)
    return True


# ---------- Public API (seller) ----------


@app.get("/api/themes")
def list_themes(_: bool = Depends(require_user), db: Session = Depends(get_db)):
    rows = db.query(Theme).filter(Theme.is_active == True).order_by(Theme.name).all()
    return [{"id": t.id, "name": t.name} for t in rows]


@app.post("/api/test")
async def submit_test(
    full_name: str = Form(...),
    theme_id: int = Form(...),
    audio: UploadFile = File(...),
    _: bool = Depends(require_user),
    db: Session = Depends(get_db),
):
    theme = db.query(Theme).filter(Theme.id == theme_id, Theme.is_active == True).first()
    if not theme:
        raise HTTPException(status_code=400, detail="Theme not found or inactive")

    audio_bytes = await audio.read()
    if not audio_bytes:
        raise HTTPException(status_code=400, detail="Пустой аудиофайл")

    try:
        user_answer = transcribe_audio(audio_bytes, filename=audio.filename or "audio.webm")
    except Exception as e:
        print(f"Whisper error: {e}")
        raise HTTPException(status_code=502, detail=f"Ошибка распознавания речи: {e}")

    active_prompt = db.query(Prompt).filter(Prompt.is_active == True).first()
    prompt_content = active_prompt.content if active_prompt else DEFAULT_EVALUATION_PROMPT

    try:
        result_text = await evaluate_answer_async(theme.reference_text, user_answer, prompt_content)
    except Exception as e:
        print(f"LLM error: {e}")
        raise HTTPException(status_code=502, detail=f"Ошибка оценки ответа: {e}")

    score = extract_score_from_result(result_text)

    from datetime import datetime, timezone
    session = DBSession(
        state=1,
        theme_id=theme_id,
        full_name=full_name.strip(),
        user_answer=user_answer,
        result=result_text,
        score=score,
        answered_at=datetime.now(timezone.utc),
    )
    if active_prompt:
        session.prompt_id = active_prompt.id
    db.add(session)
    db.commit()
    db.refresh(session)

    return {
        "session_id": session.id,
        "result": result_text,
        "score": score,
        "user_answer": user_answer,
    }


# ---------- Admin API (password-protected) ----------


@app.get("/api/admin/sessions")
def admin_list_sessions(
    page: int = 1,
    limit: int = 20,
    name: Optional[str] = None,
    theme_id: Optional[int] = None,
    score: Optional[int] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    sort_by: Optional[str] = None,
    sort_dir: Optional[str] = None,
    _: bool = Depends(require_admin),
    db: Session = Depends(get_db),
):
    q = db.query(DBSession).filter(DBSession.state == 1)
    if name and name.strip():
        q = q.filter(DBSession.full_name.ilike(f"%{name.strip()}%"))
    if theme_id is not None:
        q = q.filter(DBSession.theme_id == theme_id)
    if score is not None:
        q = q.filter(DBSession.score == score)
    if date_from:
        q = q.filter(DBSession.created_at >= date_from)
    if date_to:
        # include the full end day: add 1 day and use strict less-than
        from datetime import date as _date, timedelta as _td
        try:
            dt_to_exclusive = (_date.fromisoformat(date_to) + _td(days=1)).isoformat()
            q = q.filter(DBSession.created_at < dt_to_exclusive)
        except ValueError:
            pass

    if sort_by == 'theme_name':
        q = q.join(Theme, DBSession.theme_id == Theme.id)

    total = q.count()

    order_dir_fn = asc if sort_dir == 'asc' else desc
    if sort_by == 'id':
        q = q.order_by(order_dir_fn(DBSession.id))
    elif sort_by == 'full_name':
        q = q.order_by(order_dir_fn(DBSession.full_name))
    elif sort_by == 'theme_name':
        q = q.order_by(order_dir_fn(Theme.name))
    elif sort_by == 'created_at':
        q = q.order_by(order_dir_fn(DBSession.created_at))
    else:
        q = q.order_by(desc(DBSession.created_at))

    q = q.offset((page - 1) * limit).limit(limit)
    rows = q.all()

    theme_ids = {r.theme_id for r in rows}
    themes_map = {}
    if theme_ids:
        for t in db.query(Theme).filter(Theme.id.in_(theme_ids)).all():
            themes_map[t.id] = t.name

    data = []
    for r in rows:
        data.append({
            "id": r.id,
            "created_at": r.created_at.isoformat() if r.created_at else None,
            "answered_at": r.answered_at.isoformat() if r.answered_at else None,
            "full_name": r.full_name,
            "theme_id": r.theme_id,
            "theme_name": themes_map.get(r.theme_id, "—"),
            "result": r.result or "",
            "user_answer": r.user_answer or "",
            "score": r.score,
        })
    return {"data": data, "total": total, "page": page, "limit": limit}


@app.get("/api/admin/sessions/export")
def admin_export_sessions(
    name: Optional[str] = None,
    theme_id: Optional[int] = None,
    score: Optional[int] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    sort_by: Optional[str] = None,
    sort_dir: Optional[str] = None,
    _: bool = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """Выгрузка результатов зачётов в Excel. Применяются те же фильтры, что и в
    списке (без пагинации): дата, ФИО, тема, оценка."""
    q = db.query(DBSession).filter(DBSession.state == 1)
    if name and name.strip():
        q = q.filter(DBSession.full_name.ilike(f"%{name.strip()}%"))
    if theme_id is not None:
        q = q.filter(DBSession.theme_id == theme_id)
    if score is not None:
        q = q.filter(DBSession.score == score)
    if date_from:
        q = q.filter(DBSession.created_at >= date_from)
    if date_to:
        from datetime import date as _date, timedelta as _td
        try:
            dt_to_exclusive = (_date.fromisoformat(date_to) + _td(days=1)).isoformat()
            q = q.filter(DBSession.created_at < dt_to_exclusive)
        except ValueError:
            pass

    if sort_by == 'theme_name':
        q = q.join(Theme, DBSession.theme_id == Theme.id)

    order_dir_fn = asc if sort_dir == 'asc' else desc
    if sort_by == 'id':
        q = q.order_by(order_dir_fn(DBSession.id))
    elif sort_by == 'full_name':
        q = q.order_by(order_dir_fn(DBSession.full_name))
    elif sort_by == 'theme_name':
        q = q.order_by(order_dir_fn(Theme.name))
    elif sort_by == 'created_at':
        q = q.order_by(order_dir_fn(DBSession.created_at))
    else:
        q = q.order_by(desc(DBSession.created_at))

    rows = q.all()

    theme_ids = {r.theme_id for r in rows}
    themes_map = {}
    if theme_ids:
        for t in db.query(Theme).filter(Theme.id.in_(theme_ids)).all():
            themes_map[t.id] = t.name

    import io
    from datetime import datetime, timezone, timedelta
    from openpyxl import Workbook
    from openpyxl.styles import Font, Alignment

    wb = Workbook()
    ws = wb.active
    ws.title = "Зачёты"

    headers = ["Дата", "ФИО", "Тема", "Оценка"]
    ws.append(headers)
    for cell in ws[1]:
        cell.font = Font(bold=True)
        cell.alignment = Alignment(horizontal="center")

    # Локальное время МСК (UTC+3) для отображения даты
    msk = timezone(timedelta(hours=3))
    for r in rows:
        if r.created_at:
            dt = r.created_at
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            date_str = dt.astimezone(msk).strftime("%d.%m.%Y %H:%M")
        else:
            date_str = ""
        ws.append([
            date_str,
            r.full_name or "",
            themes_map.get(r.theme_id, "—"),
            r.score if r.score is not None else "",
        ])

    widths = [18, 30, 40, 8]
    for i, w in enumerate(widths, 1):
        ws.column_dimensions[chr(64 + i)].width = w

    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)

    fname = "zachety_" + datetime.now().strftime("%Y%m%d_%H%M") + ".xlsx"
    return StreamingResponse(
        buf,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{fname}"'},
    )


@app.get("/api/admin/themes")
def admin_list_themes(_: bool = Depends(require_admin), db: Session = Depends(get_db)):
    rows = db.query(Theme).order_by(Theme.name).all()
    return [
        {"id": t.id, "name": t.name, "reference_text": t.reference_text, "is_active": t.is_active, "created_at": t.created_at.isoformat() if t.created_at else None}
        for t in rows
    ]


class ThemeCreate(BaseModel):
    name: str
    reference_text: str
    is_active: bool = True


class ThemeUpdate(BaseModel):
    name: Optional[str] = None
    reference_text: Optional[str] = None
    is_active: Optional[bool] = None


@app.post("/api/admin/themes")
def admin_create_theme(body: ThemeCreate, _: bool = Depends(require_admin), db: Session = Depends(get_db)):
    t = Theme(name=body.name.strip(), reference_text=body.reference_text.strip(), is_active=body.is_active)
    db.add(t)
    db.commit()
    db.refresh(t)
    return {"id": t.id, "name": t.name, "reference_text": t.reference_text, "is_active": t.is_active}


@app.put("/api/admin/themes/{theme_id}")
def admin_update_theme(theme_id: int, body: ThemeUpdate, _: bool = Depends(require_admin), db: Session = Depends(get_db)):
    t = db.query(Theme).filter(Theme.id == theme_id).first()
    if not t:
        raise HTTPException(status_code=404, detail="Theme not found")
    if body.name is not None:
        t.name = body.name.strip()
    if body.reference_text is not None:
        t.reference_text = body.reference_text.strip()
    if body.is_active is not None:
        t.is_active = body.is_active
    db.commit()
    db.refresh(t)
    return {"id": t.id, "name": t.name, "reference_text": t.reference_text, "is_active": t.is_active}


@app.delete("/api/admin/themes/{theme_id}")
def admin_delete_theme(theme_id: int, _: bool = Depends(require_admin), db: Session = Depends(get_db)):
    t = db.query(Theme).filter(Theme.id == theme_id).first()
    if not t:
        raise HTTPException(status_code=404, detail="Theme not found")
    db.delete(t)
    db.commit()
    return {"ok": True}


@app.get("/api/admin/prompts/default-template")
def admin_get_default_prompt_template(_: bool = Depends(require_admin)):
    return {"content": DEFAULT_EVALUATION_PROMPT}


@app.get("/api/admin/prompts")
def admin_list_prompts(_: bool = Depends(require_admin), db: Session = Depends(get_db)):
    rows = db.query(Prompt).order_by(desc(Prompt.created_at)).all()
    return [
        {
            "id": p.id,
            "version": p.version,
            "content": p.content,
            "is_active": p.is_active,
            "is_draft": p.is_draft,
            "notes": p.notes,
            "created_at": p.created_at.isoformat() if p.created_at else None,
        }
        for p in rows
    ]


class PromptCreate(BaseModel):
    content: str
    notes: Optional[str] = None


class PromptUpdate(BaseModel):
    content: Optional[str] = None
    notes: Optional[str] = None


@app.post("/api/admin/prompts")
def admin_create_prompt(body: PromptCreate, _: bool = Depends(require_admin), db: Session = Depends(get_db)):
    max_ver = db.query(sql_func.coalesce(sql_func.max(Prompt.version), 0)).scalar() or 0
    p = Prompt(version=max_ver + 1, content=body.content.strip(), is_active=False, is_draft=True, notes=body.notes)
    db.add(p)
    db.commit()
    db.refresh(p)
    return {"id": p.id, "version": p.version, "content": p.content, "is_draft": True}


@app.put("/api/admin/prompts/{prompt_id}")
def admin_update_prompt(prompt_id: int, body: PromptUpdate, _: bool = Depends(require_admin), db: Session = Depends(get_db)):
    p = db.query(Prompt).filter(Prompt.id == prompt_id).first()
    if not p:
        raise HTTPException(status_code=404, detail="Prompt not found")
    if not p.is_draft:
        raise HTTPException(status_code=400, detail="Only draft prompts can be edited")
    if body.content is not None:
        p.content = body.content.strip()
    if body.notes is not None:
        p.notes = body.notes
    db.commit()
    db.refresh(p)
    return {"id": p.id, "version": p.version, "content": p.content, "notes": p.notes}


@app.post("/api/admin/prompts/{prompt_id}/activate")
def admin_activate_prompt(prompt_id: int, _: bool = Depends(require_admin), db: Session = Depends(get_db)):
    p = db.query(Prompt).filter(Prompt.id == prompt_id).first()
    if not p:
        raise HTTPException(status_code=404, detail="Prompt not found")
    if not p.is_draft:
        raise HTTPException(status_code=400, detail="Only draft prompts can be activated")
    db.query(Prompt).filter(Prompt.is_active == True).update({"is_active": False})
    p.is_active = True
    p.is_draft = False
    db.commit()
    db.refresh(p)
    return {"id": p.id, "is_active": True}


@app.post("/api/admin/prompts/{prompt_id}/test-on-session/{session_id}")
async def admin_test_prompt_on_session(
    prompt_id: int,
    session_id: int,
    _: bool = Depends(require_admin),
    db: Session = Depends(get_db),
):
    p = db.query(Prompt).filter(Prompt.id == prompt_id).first()
    if not p:
        raise HTTPException(status_code=404, detail="Prompt not found")
    s = db.query(DBSession).filter(DBSession.id == session_id, DBSession.state == 1).first()
    if not s:
        raise HTTPException(status_code=404, detail="Session not found")
    theme = db.query(Theme).filter(Theme.id == s.theme_id).first()
    if not theme:
        raise HTTPException(status_code=404, detail="Theme not found")

    result_text = await evaluate_answer_async(theme.reference_text, s.user_answer or "", p.content)
    pt = PromptTest(prompt_id=prompt_id, session_id=session_id, result=result_text)
    db.add(pt)
    db.commit()
    return {"result": result_text, "prompt_test_id": pt.id}


class AnalysisRequest(BaseModel):
    selected_answers: List[dict]
    prompt: str = "проанализируй и обобщи наиболее явные ошибки и дай рекомендации"


@app.post("/api/admin/analyze")
async def admin_analyze(request: AnalysisRequest, _: bool = Depends(require_admin)):
    if not request.selected_answers:
        raise HTTPException(status_code=400, detail="Нет выбранных ответов для анализа")

    from services import get_llm_client_async, LLM_MODEL, OPENROUTER_BASE_URL
    client = get_llm_client_async()
    model = LLM_MODEL

    log.info(
        "ANALYZE start: model=%s base_url=%s answers=%d prompt_len=%d",
        model, OPENROUTER_BASE_URL, len(request.selected_answers), len(request.prompt),
    )

    answers_context = []
    for idx, answer in enumerate(request.selected_answers, 1):
        answers_context.append(f"""
Ответ #{idx}:
- Сотрудник: {answer.get('full_name', 'Неизвестно')}
- Тема: {answer.get('theme_name', 'Неизвестно')}
- Ответ пользователя: {answer.get('user_answer', '')}
- Результат ИИ: {answer.get('result', '')}
""")

    messages = [
        {"role": "system", "content": "Ты эксперт по анализу продаж и обучению сотрудников. Твоя задача — анализировать ответы сотрудников и выявлять типичные ошибки."},
        {"role": "user", "content": f"{request.prompt}\n\nДанные для анализа:\n\n" + "\n".join(answers_context)}
    ]

    total_chars = sum(len(m["content"]) for m in messages)
    log.info("ANALYZE request: total_input_chars=%d", total_chars)

    async def generate():
        t0 = time.time()
        chars = 0
        try:
            stream = await client.chat.completions.create(
                model=model, messages=messages, stream=True, timeout=120,
            )
            log.info("ANALYZE stream opened in %.1fs", time.time() - t0)
            async for chunk in stream:
                if chunk.choices and chunk.choices[0].delta.content:
                    chars += len(chunk.choices[0].delta.content)
                    yield chunk.choices[0].delta.content
            log.info("ANALYZE done: %d chars in %.1fs", chars, time.time() - t0)
        except Exception as e:
            log.exception("ANALYZE failed after %.1fs: %s", time.time() - t0, e)
            yield f"Ошибка при анализе: {type(e).__name__}: {str(e)}"

    return StreamingResponse(generate(), media_type="text/plain")


# ---------- Static & pages ----------


@app.get("/")
def serve_index():
    return _serve_html("index.html")


@app.get("/admin")
def serve_admin():
    return _serve_html("admin.html")


app.mount("/", StaticFiles(directory="static", html=False), name="static")
