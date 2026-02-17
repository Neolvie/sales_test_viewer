import os
from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, StreamingResponse
from supabase import create_client, Client
from typing import List, Dict, Any
from pydantic import BaseModel
import json
from openai import AsyncOpenAI

# Инициализация Supabase из переменных окружения
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")

# Настройки LLM из переменных окружения
LLM_API_KEY = os.getenv("LLM_API_KEY")
LLM_BASE_URL = os.getenv("LLM_BASE_URL")
LLM_MODEL = os.getenv("LLM_MODEL", "gpt-4o")

DEFAULT_ANALYSIS_PROMPT = "проанализируй и обобщи наиболее явные ошибки и дай рекомендации"

if not SUPABASE_URL or not SUPABASE_KEY:
    print("WARNING: SUPABASE_URL or SUPABASE_KEY not found in env vars")

app = FastAPI()

def get_supabase() -> Client:
    return create_client(SUPABASE_URL, SUPABASE_KEY)

def get_llm_client() -> AsyncOpenAI:
    kwargs = {"api_key": LLM_API_KEY or "dummy"}
    if LLM_BASE_URL:
        kwargs["base_url"] = LLM_BASE_URL
    return AsyncOpenAI(**kwargs)

class AnalysisRequest(BaseModel):
    selected_answers: List[Dict[str, Any]]
    prompt: str = DEFAULT_ANALYSIS_PROMPT

@app.get("/api/data")
async def get_test_results(offset: int = 0, limit: int = 20):
    try:
        supabase = get_supabase()

        # 1. Получаем темы (n8n_sales_test_themes)
        themes_response = supabase.table("n8n_sales_test_themes")\
            .select("id, name")\
            .execute()
        
        themes_map = {t['id']: t['name'] for t in themes_response.data}

        # 2. Получаем сессии (n8n_sales_test_sessions) с пагинацией
        sessions_response = supabase.table("n8n_sales_test_sessions")\
            .select("*", count="exact")\
            .gte("id", 45)\
            .eq("state", 1)\
            .order("created_at", desc=True)\
            .range(offset, offset + limit - 1)\
            .execute()

        results = []
        
        for session in sessions_response.data:
            first = session.get("first_name") or ""
            last = session.get("last_name") or ""
            username = session.get("username") or "no_user"
            full_name = f"{first} {last} ({username})".strip()

            theme_id = session.get("theme_id")
            theme_name = themes_map.get(theme_id, "Неизвестная тема")

            results.append({
                "id": session["id"],
                "created_at": session["created_at"],
                "answered_at": session.get("answered_at"),
                "full_name": full_name,
                "theme_name": theme_name,
                "result": session.get("result", ""),
                "user_answer": session.get("user_answer", "")
            })

        total_count = sessions_response.count if hasattr(sessions_response, 'count') else len(results)
        
        return {
            "data": results,
            "total": total_count,
            "offset": offset,
            "limit": limit
        }

    except Exception as e:
        print(f"Error: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/analyze")
async def analyze_answers(request: AnalysisRequest):
    try:
        if not request.selected_answers:
            raise HTTPException(status_code=400, detail="Нет выбранных ответов для анализа")
        
        # Формируем контекст из выбранных ответов
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
        
        client = get_llm_client()
        
        # Потоковая передача ответа
        async def generate():
            try:
                stream = await client.chat.completions.create(
                    model=LLM_MODEL,
                    messages=messages,
                    stream=True,
                )
                async for chunk in stream:
                    if chunk.choices and chunk.choices[0].delta.content:
                        yield chunk.choices[0].delta.content
            except Exception as e:
                error_msg = f"Ошибка при анализе: {str(e)}"
                print(error_msg)
                yield error_msg
        
        return StreamingResponse(generate(), media_type="text/plain")

    except HTTPException:
        raise
    except Exception as e:
        print(f"Analysis Error: {e}")
        raise HTTPException(status_code=500, detail=str(e))

# Раздаем статику (наш HTML)
app.mount("/", StaticFiles(directory="static", html=True), name="static")
