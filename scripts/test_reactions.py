import asyncio
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import app.telegram_bot as bot_module  # noqa: E402
from aiogram.types import ReactionTypeEmoji  # noqa: E402
from app.store import Store  # noqa: E402


async def main() -> int:
    tmp = tempfile.TemporaryDirectory()
    store = Store(db_path=Path(tmp.name) / "reactions.db")
    old_store = bot_module.store
    bot_module.store = store
    try:
        company = store.create_company({"name": "Reaction Co", "channel_id": "@test"})
        task = store.create_task(company.id, 1, "Пост")
        store.update_task(task.id, status="published")
        store.save_publication(task.id, "@test", -100123, 999)
        emoji = "\U0001F44D"
        reaction = ReactionTypeEmoji(type="emoji", emoji=emoji)
        update = SimpleNamespace(
            chat=SimpleNamespace(id=-100123),
            message_id=999,
            user=SimpleNamespace(id=42),
            new_reaction=[reaction],
            old_reaction=[],
        )
        await bot_module.on_reaction(update)
        summary = store.get_reaction_summary(task.id)
        print("AFTER_USER positive=", summary["positive"], "total=", summary["total"])
        assert summary["positive"] == 1

        count_update = SimpleNamespace(
            chat=SimpleNamespace(id=-100123),
            message_id=999,
            reactions=[
                SimpleNamespace(type=reaction, total_count=3),
            ],
        )
        await bot_module.on_reaction_count(count_update)
        summary = store.get_reaction_summary(task.id)
        print("AFTER_COUNT positive=", summary["positive"], "total=", summary["total"])
        assert summary["positive"] == 3
        assert summary["source"] == "counts"
        print("REACTIONS_OK")
        return 0
    finally:
        bot_module.store = old_store
        store.close()
        tmp.cleanup()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
