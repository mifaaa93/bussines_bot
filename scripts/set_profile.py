"""Set the bot name and descriptions. Run this file directly so the text stays UTF-8."""

import asyncio
import json
from pathlib import Path

from aiogram import Bot

from secretary.config import load_settings

NAME_EN = "Secretary"
NAME_UK = "Секретар"
NAME_RU = "Секретарь"

SHORT_EN = "Sees edits and deletions in chats and sends them to you."
SHORT_UK = "Бачить правки й видалення в чатах і надсилає їх тобі."
SHORT_RU = "Видит правки и удаления в чатах и присылает их тебе."

DESC_EN = """Connect the bot as a secretary in Telegram Business.

It keeps chats and tells you when someone edits or deletes a message. Text formatting stays, and so do photos and videos.

Events are labeled Edited or Deleted. Works in private chats, groups and channels.

Send /start, then connect the bot in Telegram Business settings."""

DESC_UK = """Підключи бота як секретаря в Telegram Business.

Він зберігає листування і пише тобі, коли хтось змінює або видаляє повідомлення. Текст лишається з оформленням, фото і відео теж.

Події підписані: Змінено або Видалено. Працює в особистих чатах, групах і каналах.

Натисни /start, потім підключи бота в налаштуваннях Telegram Business."""

DESC_RU = """Подключи бота как секретаря в Telegram Business.

Он хранит переписки и пишет тебе, когда кто-то меняет или удаляет сообщение. Оформление текста сохраняется, фото и видео тоже.

События подписаны: Изменено или Удалено. Работает в личных чатах, группах и каналах.

Нажми /start, затем подключи бота в настройках Telegram Business."""


async def main() -> None:
    settings = load_settings()
    bot = Bot(settings.bot_token)
    out = Path(__file__).resolve().parent.parent / "logs" / "profile_check.json"
    try:
        await bot.set_my_name(NAME_EN)
        await bot.set_my_name(NAME_UK, language_code="uk")
        await bot.set_my_name(NAME_RU, language_code="ru")
        await bot.set_my_short_description(SHORT_EN)
        await bot.set_my_short_description(SHORT_UK, language_code="uk")
        await bot.set_my_short_description(SHORT_RU, language_code="ru")
        await bot.set_my_description(DESC_EN)
        await bot.set_my_description(DESC_UK, language_code="uk")
        await bot.set_my_description(DESC_RU, language_code="ru")
        payload = {
            "name": (await bot.get_my_name()).name,
            "name_uk": (await bot.get_my_name(language_code="uk")).name,
            "name_ru": (await bot.get_my_name(language_code="ru")).name,
            "short": (await bot.get_my_short_description()).short_description,
            "short_uk": (await bot.get_my_short_description(language_code="uk")).short_description,
            "short_ru": (await bot.get_my_short_description(language_code="ru")).short_description,
            "description": (await bot.get_my_description()).description,
            "description_uk": (await bot.get_my_description(language_code="uk")).description,
            "description_ru": (await bot.get_my_description(language_code="ru")).description,
        }
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    finally:
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
