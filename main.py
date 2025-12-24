import os
from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from supabase import create_client, Client
from typing import List, Dict, Any

# Инициализация Supabase из переменных окружения
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")

if not SUPABASE_URL or not SUPABASE_KEY:
    print("WARNING: SUPABASE_URL or SUPABASE_KEY not found in env vars")

app = FastAPI()

def get_supabase() -> Client:
    return create_client(SUPABASE_URL, SUPABASE_KEY)

@app.get("/api/data")
async def get_test_results():
    try:
        supabase = get_supabase()

        # 1. Получаем темы (n8n_sales_test_themes)
        # Нам нужно сопоставить theme_id с названием
        themes_response = supabase.table("n8n_sales_test_themes")\
            .select("id, name")\
            .execute()
        
        # Создаем словарь для быстрого поиска: {id: name}
        themes_map = {t['id']: t['name'] for t in themes_response.data}

        # 2. Получаем сессии (n8n_sales_test_sessions)
        # Фильтры: id >= 45, state = 1
        sessions_response = supabase.table("n8n_sales_test_sessions")\
            .select("*")\
            .gte("id", 45)\
            .eq("state", 1)\
            .order("created_at", desc=True)\
            .execute()

        results = []
        
        for session in sessions_response.data:
            # Формируем ФИО
            first = session.get("first_name") or ""
            last = session.get("last_name") or ""
            username = session.get("username") or "no_user"
            full_name = f"{first} {last} ({username})".strip()

            # Ищем название темы
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

        return results

    except Exception as e:
        print(f"Error: {e}")
        raise HTTPException(status_code=500, detail=str(e))

# Раздаем статику (наш HTML)
app.mount("/", StaticFiles(directory="static", html=True), name="static")
