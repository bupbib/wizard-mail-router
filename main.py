import re 
import asyncio 
import email 
import logging 
from datetime import datetime, timedelta
from email.policy import default
from email.message import EmailMessage

import aioimaplib

from config import Config 
from utils import partition_emails
from mail_client import get_mail_client


logger = logging.getLogger(__name__)


async def mark_as_read(client: aioimaplib.IMAP4_SSL, msg_ids: str) -> None:
    """
    Помечает указанные письма флагом \\Seen (прочитано) в IMAP-сервере.

    Использует пакетную команду STORE над переданным списком UID писем.

    Args:
        client: Активный SSL-клиент IMAP (aioimaplib).
        msg_ids: Строка с UID писем через запятую (например, "101,102,103").
    """
    if not msg_ids: 
        return None 
    
    status, data = await client.uid("STORE", msg_ids, "+FLAGS", "\\Seen")

    if status == "OK":
        logger.info(f"Письма {msg_ids} успешно помечены как прочитанное")
    else:
        report = data[0].decode() if data else "неизвестная ошибка"
        logger.error(f"Не удалось пометить письма {msg_ids} как прочитанные, причина: {report}")


async def process_messages_batch(client: aioimaplib.IMAP4_SSL, messages: dict[str, EmailMessage]) -> None:
    partition_result = partition_emails(messages=messages)

    if partition_result.failed:
        await mark_as_read(client=client, msg_ids=",".join(message.msg_id for message in partition_result.failed))

    if not partition_result.passed:
        logger.info("Новых писем для классификации нет")
        return None 

    # TODO: Здесь уже будем отправлять EmailInfo к Апи для классификации


async def fetch_multiple_messages(client: aioimaplib.IMAP4_SSL, msg_ids: list[str]) -> dict[str, EmailMessage] | None:
    """
    Загружает содержимое нескольких писем одним пакетным запросом.

    Использует BODY.PEEK[], чтобы не менять флаг прочитанности (\\Seen)
    у загруженных писем. Это позволяет обработать письмо, а затем
    вручную установить флаг только после успешной обработки.

    Args:
        client: IMAP-клиент в состоянии SELECTED.
        msg_ids: Список ID писем для загрузки (не пустой).

    Returns:
        dict[str, EmailMessage]: Словарь писем вида {uid: EmailMessage}.
        None: Если fetch вернул статус != OK.
    """
    msg_ids_string = ",".join(msg_ids)
    logger.info(f"Делаю запрос на получение информации следующих писем: {msg_ids_string}")

    status, data = await client.uid("FETCH", msg_ids_string, "BODY.PEEK[]")

    if status != "OK":
        report = data[0].decode() if data else "неизвестная причина"
        logger.error(f"Не удалось получить информацию для писем {msg_ids_string}, причина: {report}")
        return None

    only_messages = {}

    for idx, item in enumerate(data):
        if isinstance(item, bytearray):
            line_with_id = data[idx - 1]
            search_id = re.search(rb"(?<=\bUID\s)\d+", line_with_id)

            if not search_id:
                logger.error(
                    "UID не найден в предыдущем элементе сообщения с типом bytearray, "
                    f"возможно, структура ответа поменялась, строка поиска: {line_with_id}"
                ) 
                return None

            uid = search_id.group().decode()
            only_messages[uid] = email.message_from_bytes(item, policy=default)

    if len(only_messages) != len(msg_ids):
        logger.warning(
            f"Запрошено {len(msg_ids)} писем, получено {len(only_messages)}. "
            f"Возможно, некоторые письма были удалены или недоступны."
        )

    logger.info(f"Успешно загружено {len(only_messages)} писем")
    return only_messages


async def main(config: Config):
    config.setup_logging()

    async with get_mail_client(config) as client:
        while True:
            week_ago = (datetime.today() - timedelta(days=7)).strftime("%d-%b-%Y")

            logger.info("Делаю запрос на поиск новых писем")
            status, data = await client.uid_search("UNSEEN", "SINCE", week_ago)

            if status != "OK":
                report = data[0].decode() if data else "неизвестная ошибка"
                logger.error(f"Не удалось получить письма из папки {config.mail.work_box}, статус ответа: {status}, ответ: {report}")
                break   

            if not data[0]:
                logger.info("На текущий момент непрочитанных писем не найдено")
                await asyncio.sleep(config.mail.retry_interval)
                continue 

            new_mails = data[0].decode().split()
            logger.info(f"Найдено {len(new_mails)} новых писем, их id: {new_mails}")

            saved_messages = await fetch_multiple_messages(client=client, msg_ids=new_mails)

            if saved_messages is None:
                break 

            await process_messages_batch(client=client, messages=saved_messages)

            # TODO: Убрать потом отсюда break
            break 


if __name__ == "__main__":
    try:
        asyncio.run(main(Config.from_env()))
    except KeyboardInterrupt:
        logger.info("Работа воркера бережно остановлена пользователем (Ctrl+C)")
    except Exception as global_err:
        logger.error(f"Неожиданная ошибка в работе воркера: {global_err}", exc_info=True)