import asyncio 
import logging 

import aiofiles
from aiocsv import AsyncWriter 

from models import EmailInfo
from config import Config 


logger = logging.getLogger(__name__)
_lock = asyncio.Lock()


async def record_entry(messages: list[EmailInfo], config: Config) -> None:
    try:
        is_exists = config.report.filepath.exists()
        logger.info(f"Начинаю работу с отчетом, количество записей на добавление: {len(messages)}")
        async with(
            _lock,
            aiofiles.open(config.report.filepath, mode="a", encoding="utf-8-sig", newline="") as file
        ):
            writer = AsyncWriter(file, delimiter=";")

            if not is_exists:
                await writer.writerow(config.report.headers)

            await writer.writerows((email_info.model_to_report() for email_info in messages))
            logger.info("Все записи в отчет успешно добавлены")
    except Exception as err:
        logger.error(f"Произошла неожиданная ошибка при работе с отчетом: {err}", exc_info=True)