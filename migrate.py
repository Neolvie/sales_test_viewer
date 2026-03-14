"""
One-time migration: Supabase (n8n_sales_test_themes, n8n_sales_test_sessions) -> local PostgreSQL (themes, sessions).
Run after DB is up: docker compose exec app python migrate.py
Requires SUPABASE_URL and SUPABASE_KEY in .env.
"""
import os
import re

from dotenv import load_dotenv
load_dotenv()

from supabase import create_client
from database import get_engine, SessionLocal, Theme, Session as DBSession

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")


def extract_score(result_text: str):
    if not result_text:
        return None
    m = re.search(r"Итоговая оценка:\s*(\d+)", result_text, re.IGNORECASE)
    return int(m.group(1)) if m else None


def main():
    if not SUPABASE_URL or not SUPABASE_KEY:
        print("Set SUPABASE_URL and SUPABASE_KEY in .env")
        return 1

    supabase = create_client(SUPABASE_URL, SUPABASE_KEY)
    db = SessionLocal()

    try:
        # 1. Themes: n8n_sales_test_themes -> themes
        r = supabase.table("n8n_sales_test_themes").select("*").execute()
        old_theme_id_to_new = {}
        for row in r.data:
            t = Theme(
                name=row.get("name") or "",
                reference_text=row.get("text") or "",
                is_active=(row.get("state") == 1),
            )
            db.add(t)
            db.flush()
            old_theme_id_to_new[row["id"]] = t.id
        db.commit()
        print("Themes migrated:", len(old_theme_id_to_new))

        # 2. Sessions: n8n_sales_test_sessions -> sessions
        r = supabase.table("n8n_sales_test_sessions").select("*").order("id").execute()
        count = 0
        for row in r.data:
            new_theme_id = old_theme_id_to_new.get(row.get("theme_id"))
            if new_theme_id is None:
                continue
            first = row.get("first_name") or ""
            last = row.get("last_name") or ""
            username = row.get("username") or ""
            full_name = f"{first} {last} ({username})".strip() or "—"
            if full_name == " ()":
                full_name = "—"
            s = DBSession(
                created_at=row.get("created_at"),
                answered_at=row.get("answered_at"),
                state=row.get("state", 0),
                theme_id=new_theme_id,
                full_name=full_name,
                user_answer=row.get("user_answer"),
                result=row.get("result"),
                prompt_id=None,
                score=extract_score(row.get("result") or ""),
            )
            db.add(s)
            count += 1
        db.commit()
        print("Sessions migrated:", count)
        return 0
    except Exception as e:
        db.rollback()
        print("Error:", e)
        return 1
    finally:
        db.close()


if __name__ == "__main__":
    exit(main())
