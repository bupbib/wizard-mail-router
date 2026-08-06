import re 
import io 
import asyncio 
import email 
import logging 
from pathlib import Path 
from datetime import datetime, timedelta
from email.policy import default
from email.message import EmailMessage
from typing import cast 

import aioimaplib
import aiosmtplib
import httpx 
from pypdf import PdfReader
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from config import Config 
from models import (
    EmailInfo, ApiFilePayload, ResultApiClassification, ApiMethodPayload
)
from utils import partition_emails, safe_api_post
from mail_client import get_mail_imap_client
from report import record_entry, sending_report


logger = logging.getLogger(__name__)


async def forward_messages(
    messages: list[EmailInfo],
    config: Config
) -> list[EmailInfo]:
    """
    Пересылает список писем через SMTP с сохранением оригинального форматирования и вложений.

    Собирает новое MIME-сообщение с локализованной русской шапкой (От, Кому, Дата, Тема),
    копирует все прикрепленные файлы и отправляет их адресатам через TLS-соединение.
    Обрабатывает частичную доставку адресатам и специфичные тайм-ауты сервера (SMTPReadTimeoutError).

    Args:
        messages (list[EmailInfo]): Список объектов EmailInfo с заполненными:
            - original_message: Оригинал письма с вложениями.
            - recipients: Список адресатов для пересылки.
            - from_, to, subject, received_at: Для формирования шапки.
        config (Config): Объект конфигурации с параметрами подключения к SMTP-серверу.

    Returns:
        list[EmailInfo]: Список писем, которые были успешно отправлены.
            Возвращаются только те объекты EmailInfo, чьи письма 
            были приняты SMTP-сервером (даже если частично).
            Если письмо не отправилось — оно не включается в результат.
    """
    successful_messages = []
    try:
        async with aiosmtplib.SMTP(
            hostname=config.mail.smtp_host, 
            port=config.mail.smtp_port, 
            timeout=config.mail.timeout,
            use_tls=True,
            username=config.mail.email,
            password=config.mail.password 
            ) as client:
            
            for email_info in messages:
                try:
                    orig = email_info.original_message
                    received_at = email_info.received_at

                    day = [
                        "Понедельник", "Вторник", "Среда", 
                        "Четверг", "Пятница", "Суббота", "Воскресенье"
                    ][received_at.weekday()]
                    month = [
                        "", "января", "февраля", "марта", "апреля", "мая", "июня",
                        "июля", "августа", "сентября", "октября", "ноября", "декабря"
                    ][received_at.month]
                    z = received_at.strftime("%z")
                    original_date = received_at.strftime(f"{day}, %d {month} %Y, %H:%M {z[:3]}:{z[3:]}")

                    fwd_header = (
                        "-------- Пересылаемое сообщение --------\n"
                        f"От кого: {email_info.from_}\n"
                        f"Кому: {email_info.to}\n"
                        f"Дата: {original_date}\n"
                        f"Тема: {email_info.subject}\n\n"
                    )

                    new_msg = EmailMessage()

                    new_msg["To"] = ", ".join(cast(list[str], email_info.recipients))
                    new_msg["From"] = config.mail.email 
                    new_msg["Subject"] = f"Fwd: {email_info.subject}"

                    orig_body = orig.get_body(preferencelist=("plain", "html"))
                    orig_text = orig_body.get_content() if orig_body else ""    

                    new_msg.set_content(fwd_header + orig_text)

                    for part in orig.iter_attachments():
                        filename = part.get_filename()
                        content_type = part.get_content_type()
                        maintype, subtype = content_type.split('/', 1)
                        payload = part.get_payload(decode=True)

                        new_msg.add_attachment(
                            payload,
                            maintype=maintype,
                            subtype=subtype,
                            filename=filename
                        )

                    errors, response_text = await client.send_message(
                        new_msg,
                        sender=config.mail.email,
                        recipients=email_info.recipients
                    )

                    if errors:
                        logger.warning(
                            f"Письмо {email_info.msg_id} отправлено частично. "
                            f"Не удалось доставить адресатам: {errors}"
                        )
                    else:
                        logger.info(
                            f"Письмо {email_info.msg_id} успешно доставлено всем адресатам: {email_info.recipients}"
                            f"{response_text=}"
                        )

                    successful_messages.append(email_info) 
                except (aiosmtplib.SMTPException, aiosmtplib.SMTPDataError) as msg_err:
                    logger.error(f"Ошибка при отправке письма {email_info.msg_id}: {msg_err}", exc_info=True)  
    except aiosmtplib.SMTPConnectError as con_err:
        logger.error(f"Не удалось подключиться к серверу при входе в контекст: {con_err}", exc_info=True)
    except aiosmtplib.SMTPException as smtp_err:
        logger.error(f"Произошла ошибка SMTP внутри контекста: {smtp_err}", exc_info=True)

    return successful_messages 


async def upload_file_bytes(
    client: httpx.AsyncClient,
    filename: str,
    file_bytes: bytes,
    config: Config 
) -> str | None:
    """
    Загружает файл в API и возвращает его идентификатор.
    
    Функция отправляет файл (вложение письма) на эндпоинт /files API,
    получает присвоенный ID и возвращает его для дальнейшего использования
    в запросе на классификацию.
    
    Args:
        client: Асинхронный HTTP-клиент (httpx.AsyncClient) для выполнения запроса.
        filename (str): Имя файла (из заголовка вложения).
        file_bytes (bytes): Содержимое файла в виде байтов.
        config (Config): Объект конфигурации с параметрами API (токен, URL).
    
    Returns:
        str | None:
            - str: Идентификатор загруженного файла при успехе.
            - None: При ошибке загрузки или невалидном ответе API.
    """
    headers = {"Authorization": f"Bearer {config.api.token}"}
    files = {"file": (filename, file_bytes)}

    logger.info(f"Отправляю запрос на получение id файла: {filename}")
    api_response = await safe_api_post(
        client=client,
        url=config.api.url_files,
        headers=headers,
        files=files
    )

    if api_response is None:
        return None 
    elif not isinstance(api_response.payload, ApiFilePayload):
        logger.error(f"Неожиданный тип payload: {type(api_response.payload)=}")
        return None 

    file_id = api_response.payload.file.id 
    logger.info(f"Результат получения id файла {filename}: {file_id}")
    return file_id


async def classify_email(
    client: httpx.AsyncClient, 
    email_info: EmailInfo,
    config: Config
) -> ResultApiClassification | None:
    """
    Отправляет письмо в AI-классификатор и возвращает результат.
    
    Функция выполняет полный цикл классификации:
        1. Извлекает все вложения из письма.
        2. Асинхронно загружает их в API (если есть).
        3. Отправляет запрос на классификацию с метаданными письма и ID файлов.
        4. Возвращает результат: является ли письмо целевым и в какой отдел.
    
    Args:
        client: Асинхронный HTTP-клиент (httpx.AsyncClient) для выполнения запросов.
        email_info (EmailInfo): Объект с данными письма.
        config (Config): Объект конфигурации с параметрами API.
    
    Returns:
        ResultApiClassification | None:
            - ResultApiClassification: При успешной классификации.
            - None: При любой ошибке.
    """
    file_ids = []
    attachments = list(email_info.original_message.iter_attachments())

    if attachments:
        upload_tasks = []

        for attachment in attachments:
            filename = attachment.get_filename() or ""
            file_bytes = attachment.get_payload(decode=True)

            if not isinstance(file_bytes, bytes):
                logger.warning(f"Не удалось прочитать вложение {filename}, пропускаем")
                continue 

            suffix = Path(filename).suffix 

            if suffix not in {
                ".pdf",".doc", ".docx", ".txt", ".rtf", ".odt",
                ".xls", ".xlsx", ".csv", ".eml", ".msg", ".xml"
            }:
                continue 

            if suffix == ".pdf":
                try:
                    if len(PdfReader(io.BytesIO(file_bytes)).pages) > 20:
                        logger.info(f"PDF '{filename}' > 20 страниц, пропускаем")
                        continue  
                except Exception:
                    logger.info(f"Не удалось проверить страницы в PDF '{filename}', пропускаем")
                    continue 
            
            upload_tasks.append(upload_file_bytes(
                client=client,
                filename=filename,
                file_bytes=file_bytes,  # type: ignore
                config=config
            ))

        file_ids = await asyncio.gather(*upload_tasks) 

        if None in file_ids: 
            return None 

    headers = {
        "Authorization": f"Bearer {config.api.token}",
        "Content-Type": "application/json"
    }
    payload = {
        "version": config.api.version,
        "command": "method:call",
        "payload": {
            "name": config.api.method_name,
            "arguments": {
                config.api.argument_name: email_info.model_to_api()
            },
            "attachments": [{"file_id": fid} for fid in file_ids],
            "execution": {
                "mode": "sync"
            }
        }
    }

    logger.info(f"Отправляю письмо {email_info.msg_id} в Апи на классификацию")
    api_response = await safe_api_post(
        client=client, 
        url=config.api.url_commands,
        headers=headers,
        json=payload
    )

    if api_response is None:
        return None 
    elif not isinstance(api_response.payload, ApiMethodPayload):
        logger.error(f"Неожиданный тип payload: {type(api_response.payload)=}")
        return None 

    classification_result = api_response.payload.result.data 

    logger.info(f"Результат классификации письма {email_info.msg_id} от Апи: {classification_result}")
    return classification_result


async def mark_as_read(client: aioimaplib.IMAP4_SSL, msg_ids: str) -> bool:
    """
    Помечает указанные письма флагом \\Seen (прочитано) в IMAP-сервере.

    Использует пакетную команду STORE над переданным списком UID писем.

    Args:
        client: Активный SSL-клиент IMAP (aioimaplib).
        msg_ids: Строка с UID писем через запятую (например, "101,102,103").
    
    Returns:
        bool: 
            - True: все валидные UID из списка успешно помечены (или список пуст).
            - False: произошла ошибка (соединение, серверная ошибка), ни одно письмо не было помечено.
    """
    if not msg_ids: 
        return True 
    
    status, data = await client.uid("STORE", msg_ids, "+FLAGS", "\\Seen")

    if status == "OK":
        logger.info(f"Письма {msg_ids} успешно помечены как прочитанные")
        return True 
    else:
        report = data[0].decode() if data else "неизвестная ошибка"
        logger.error(f"Не удалось пометить письма {msg_ids} как прочитанные, причина: {report}")
        return False 


async def process_messages_batch(
    client: aioimaplib.IMAP4_SSL, 
    config: Config,
    messages: dict[str, EmailMessage]
) -> None | list[EmailInfo]:
    """
    Осуществляет конвейерную обработку входящих писем.

    Этапы:
        1. Разделяет письма на три категории (partition_emails):
           - failed: ответы (Re:/Отв:) — сразу в прочитанные.
           - classified: локальные правила — в очередь на отправку.
           - passed: требуют ИИ-классификации.
        2. Для passed писем асинхронно запрашивает ИИ-классификацию.
        3. Целевые письма (из classified и успешных ИИ) отправляет через forward_messages.
        4. Все обработанные письма помечает как прочитанные (\\Seen).

    Args:
        client: Активный SSL-клиент IMAP для маркировки писем.
        config: Объект конфигурации с правилами маршрутизации и параметрами.
        messages: Словарь входящих писем {UID: EmailMessage}.

    Returns:
        list[EmailInfo] | None:
            - list[EmailInfo]: Все письма, которые были помечены как прочитанные.
            - None: Если не удалось пометить письма (ошибка IMAP).
    """
    partition_result = partition_emails(messages=messages, config=config) 

    messages_to_forward = []
    messages_to_mark_as_read = []

    for email_info in partition_result.failed:
        messages_to_mark_as_read.append(email_info)

    for email_info in partition_result.classified:
        messages_to_forward.append(email_info)

    if partition_result.passed:
        logger.info("Отправляю письма на классификацию ИИ")
        timeout_config = httpx.Timeout(timeout=180, connect=10)

        async with httpx.AsyncClient(timeout=timeout_config) as http_client:
            classify_results = await asyncio.gather(
                *(classify_email(client=http_client, email_info=email_info, config=config) 
                for email_info in partition_result.passed)    
            )

        for email_info, classify in zip(partition_result.passed, classify_results):
            if classify is None: 
                continue 

            if classify.is_target and classify.department: 
                redirect = getattr(config.redirection, classify.department.lower(), None)

                if redirect is not None:
                    email_info.department = classify.department
                    email_info.recipients = redirect 
                    messages_to_forward.append(email_info)
                else:
                    # по сути таких ситуаций быть не должно, но на всякий случай
                    logger.error(f"{classify.department.lower()=} не удалось найти в {config.redirection}")
            else:
                messages_to_mark_as_read.append(email_info)

    if messages_to_forward:
        logger.info(f"Начинаю переотправку писем ответственным: {len(messages_to_forward)}")
        successful_forward_msgs = await forward_messages(messages=messages_to_forward, config=config)
        messages_to_mark_as_read.extend(successful_forward_msgs)

    if messages_to_mark_as_read:
        if not (
            await mark_as_read(client=client, msg_ids=",".join(email_info.msg_id for email_info in messages_to_mark_as_read))
        ): return None 
        return messages_to_mark_as_read


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

    scheduler = AsyncIOScheduler()
    trigger = CronTrigger(hour=config.report.work_time, minute="0", second="0")
    scheduler.add_job(
        func=sending_report,
        trigger=trigger,
        kwargs={"config": config}
    )

    scheduler.start()
    logger.info("Фоновая задача по отправке отчета запущена")

    async with get_mail_imap_client(config) as client:
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

            successful_messages = await process_messages_batch(client=client, config=config, messages=saved_messages)

            if successful_messages is not None:
                await record_entry(messages=successful_messages, config=config) 


if __name__ == "__main__":
    try:
        asyncio.run(main(Config.from_env()))
    except KeyboardInterrupt:
        logger.info("Работа воркера бережно остановлена пользователем (Ctrl+C)")
    except Exception as global_err:
        logger.error(f"Неожиданная ошибка в работе воркера: {global_err}", exc_info=True)