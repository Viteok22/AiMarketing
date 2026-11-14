import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.config import settings  # noqa: E402
from app.store import Store  # noqa: E402

COMPANY = "c-salavatsteklo"
PROMPT = (
    "Подготовь пост на актуальную тему для компании. "
    "Используй свежие тренды и обратную связь по реакциям. "
    "Не выдумывай факты."
)


def main() -> int:
    store = Store()
    try:
        store.upsert_schedule(
            company_id=COMPANY,
            enabled=True,
            interval_minutes=1,
            prompt=PROMPT,
            user_id=settings.admin_telegram_id,
        )
        print("SCHEDULE_ENABLED")
        time.sleep(90)
        tasks = store.list_tasks(limit=5)
        for task in tasks:
            print("TASK", task.id, task.status, task.request[:70])
    finally:
        store.upsert_schedule(
            company_id=COMPANY,
            enabled=False,
            interval_minutes=180,
            prompt="",
            user_id=settings.admin_telegram_id,
        )
        store.close()
        print("SCHEDULE_DISABLED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
