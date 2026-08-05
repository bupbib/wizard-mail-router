import asyncio 
import logging 
from email.message import EmailMessage

import aiofiles
import aiosmtplib
from aiocsv import AsyncWriter 

from models import EmailInfo
from config import Config 


logger = logging.getLogger(__name__)
_lock = asyncio.Lock()


async def record_entry(messages: list[EmailInfo], config: Config) -> None:
    try:
        async with _lock:
            is_exists = config.report.filepath.exists()
            logger.info(f"Начинаю работу с отчетом, количество записей на добавление: {len(messages)}")

            async with aiofiles.open(config.report.filepath, mode="a", encoding="utf-8-sig", newline="") as file:
                writer = AsyncWriter(file, delimiter=";")

                if not is_exists:
                    await writer.writerow(config.report.headers)

                await writer.writerows((email_info.model_to_report() for email_info in messages))
                logger.info("Все записи в отчет успешно добавлены")
    except Exception as err:
        logger.error(f"Произошла неожиданная ошибка при работе с отчетом: {err}", exc_info=True)


async def sending_report(config: Config):
    try:
        async with _lock:
            logger.info("Запущена функция по отправке отчета")
            filepath = config.report.filepath

            if not filepath.exists():
                logger.warning("Файл отчета не найден, пропускаю отправку") 
                return 

            async with aiosmtplib.SMTP(
                hostname=config.mail.smtp_host, 
                port=config.mail.smtp_port, 
                timeout=config.mail.timeout,
                use_tls=True,
                username=config.mail.email,
                password=config.mail.password 
            ) as client:
            
                async with aiofiles.open(filepath, "rb") as file:
                    file_content = await file.read()

                message = EmailMessage()

                message["To"] = ", ".join(config.report.responsible)
                message["From"] = config.mail.email 
                message["Subject"] = "Отчет ИИ классификатора писем"

                message.set_content("Отчет о разобранных письмах во вложении")

                filename = filepath.name 

                message.add_attachment(
                    file_content,
                    maintype="text",
                    subtype="csv",
                    filename=filename 
                )

                await client.send_message(message)
                logger.info("Письмо с отчетом успешно отправлено")

                filepath.unlink()
                logger.info("Файл отчета успешно удален")
    except aiosmtplib.SMTPConnectError as con_err:
        logger.error(f"Не удалось подключиться к серверу при входе в контекст: {con_err}", exc_info=True) 
    except (aiosmtplib.SMTPException, aiosmtplib.SMTPDataError) as msg_err:
        logger.error(f"Ошибка при отправке отчета: {msg_err}", exc_info=True)         
    except Exception as err:
        logger.error(f"Произошла неожиданная ошибка при работе фоновой задачи по отправке отчета: {err}", exc_info=True) 