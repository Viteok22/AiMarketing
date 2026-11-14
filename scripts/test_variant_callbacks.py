import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.config import settings  # noqa: E402
from app.runtime import store  # noqa: E402
from app.telegram_bot import callback_image, callback_text  # noqa: E402


async def fake_edit(text, reply_markup=None):
    print("EDIT_OK", text[:120].replace("\n", " | "))


async def fake_answer(text=None, show_alert=False):
    print("ANSWER_OK", text)


async def main() -> int:
    tasks = store.list_tasks(limit=5)
    if not tasks:
        print("NO_TASKS")
        return 1
    task = tasks[0]
    texts = store.list_variants(task.id, "text", store.latest_batch(task.id))
    images = store.list_variants(task.id, "image", store.latest_batch(task.id))
    print("TASK", task.id, "texts", len(texts), "images", len(images))
    if len(texts) < 2 or len(images) < 2:
        print("NOT_ENOUGH_VARIANTS")
        return 2
    text2, image2 = texts[1], images[1]
    fake_callback_text = SimpleNamespace(
        data=f"text:{task.id}:{text2.id}",
        from_user=SimpleNamespace(id=settings.admin_telegram_id),
        message=SimpleNamespace(edit_text=fake_edit),
        answer=fake_answer,
    )
    await callback_text(fake_callback_text)
    selected = store.get_task(task.id)
    print("TEXT_SELECTED", selected.selected_text_id == text2.id, selected.post_text == text2.content)

    fake_callback_image = SimpleNamespace(
        data=f"image:{task.id}:{image2.id}",
        from_user=SimpleNamespace(id=settings.admin_telegram_id),
        message=SimpleNamespace(edit_text=fake_edit),
        answer=fake_answer,
    )
    await callback_image(fake_callback_image)
    selected = store.get_task(task.id)
    print("IMAGE_SELECTED", selected.selected_image_id == image2.id, selected.image_path == image2.image_path)

    # Вернуть выбор на первые варианты, чтобы не менять демо-состояние.
    store.select_variant(task.id, "text", texts[0].id)
    store.select_variant(task.id, "image", images[0].id)
    store.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
