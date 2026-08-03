import re 
import asyncio 
import email 
import logging 
from datetime import datetime, timedelta
from email.policy import default
from email.message import EmailMessage

import aioimaplib
import aiosmtplib
import httpx 

from config import Config 
from models import (
    EmailInfo, ApiFilePayload, ResultApiClassification, 
    ForwardMessage, ApiMethodPayload
)
from utils import partition_emails, safe_api_post
from mail_client import get_mail_imap_client


logger = logging.getLogger(__name__)


async def forward_messages(
    messages: list[ForwardMessage],
    config: Config
) -> list[str]:
    """
    Пересылает список писем через SMTP с сохранением оригинального форматирования и вложений.

    Собирает новое MIME-сообщение с локализованной русской шапкой (От, Кому, Дата, Тема),
    копирует все прикрепленные файлы и отправляет их адресатам через TLS-соединение.
    Обрабатывает частичную доставку адресатам и специфичные тайм-ауты сервера (SMTPReadTimeoutError).

    Args:
        messages: Список объектов ForwardMessage, содержащих исходное письмо, 
                  список получателей и msg_id.
        config: Объект конфигурации с параметрами подключения к SMTP-серверу 
                (хост, порт, учетные данные, timeout).

    Returns:
        Список msg_id писем, которые были успешно доставлены или приняты сервером.
    """
    successful_msg_ids = []
    try:
        async with aiosmtplib.SMTP(
            hostname=config.mail.smtp_host, 
            port=config.mail.smtp_port, 
            timeout=config.mail.timeout,
            use_tls=True,
            username=config.mail.email,
            password=config.mail.password 
            ) as client:
            
            for message in messages:
                try:
                    orig = message.original_message

                    original_from = orig["From"]
                    original_to = orig["To"]
                    original_date = (orig["Date"]).datetime
                    original_subject = orig["Subject"]

                    day = [
                        "Понедельник", "Вторник", "Среда", 
                        "Четверг", "Пятница", "Суббота", "Воскресенье"
                    ][original_date.weekday()]
                    month = [
                        "", "января", "февраля", "марта", "апреля", "мая", "июня",
                        "июля", "августа", "сентября", "октября", "ноября", "декабря"
                    ][original_date.month]
                    z = original_date.strftime("%z")
                    original_date = original_date.strftime(f"{day}, %d {month} %Y, %H:%M {z[:3]}:{z[3:]}")

                    fwd_header = (
                        "-------- Пересылаемое сообщение --------\n"
                        f"От кого: {original_from}\n"
                        f"Кому: {original_to}\n"
                        f"Дата: {original_date}\n"
                        f"Тема: {original_subject}\n\n"
                    )

                    new_msg = EmailMessage()

                    new_msg["To"] = ", ".join(message.recipients)
                    new_msg["From"] = config.mail.email 
                    new_msg["Subject"] = f"Fwd: {original_subject}"

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
                        recipients=message.recipients
                    )

                    if errors:
                        logger.warning(
                            f"Письмо {message.msg_id} отправлено частично. "
                            f"Не удалось доставить адресатам: {errors}"
                        )
                    else:
                        logger.info(
                            f"Письмо {message.msg_id} успешно доставлено всем адресатам: {message.recipients}"
                            f"{response_text=}"
                        )

                    successful_msg_ids.append(message.msg_id) 
                except (aiosmtplib.SMTPException, aiosmtplib.SMTPDataError) as msg_err:
                    logger.error(f"Ошибка при отправке письма {message.msg_id}: {msg_err}", exc_info=True)  
    except aiosmtplib.SMTPConnectError as con_err:
        logger.error(f"Не удалось подключиться к серверу при входе в контекст: {con_err}", exc_info=True)
    except aiosmtplib.SMTPException as smtp_err:
        logger.error(f"Произошла ошибка SMTP внутри контекста: {smtp_err}", exc_info=True)

    return successful_msg_ids 


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
    original_message: EmailMessage, 
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
        email_info (EmailInfo): Объект с данными письма (тема, отправитель, тело).
        original_message (EmailMessage): Оригинальный объект письма для извлечения вложений.
        config (Config): Объект конфигурации с параметрами API.
    
    Returns:
        ResultApiClassification | None:
            - ResultApiClassification: При успешной классификации.
            - None: При любой ошибке.
    """
    file_ids = []
    attachments = list(original_message.iter_attachments())

    if attachments:
        upload_tasks = [
            upload_file_bytes(
                client=client,
                filename=attachment.get_filename() or "",                   # type: ignore
                file_bytes=attachment.get_payload(decode=True),             # type: ignore
                config=config
            )
            for attachment in attachments
        ]
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
                config.api.argument_name: email_info.model_dump_json()
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


async def process_messages_batch(
    client: aioimaplib.IMAP4_SSL, 
    config: Config,
    messages: dict[str, EmailMessage]
) -> None:
    """
    Осуществляет конвейерную обработку входящих писем.

    Сначала разделяет письма на нецелевые, предклассифицированные и подходящие под ИИ-анализ.
    Отправляет целевую группу на асинхронную классификацию в ИИ, распределяет успешные 
    письма по департаментам и выполняет массовую пересылку (forward). В завершение 
    помечает прочитанными (`\\Seen`) в IMAP все обработанные, нецелевые и сбойные письма.

    Args:
        client: Активный SSL-клиент IMAP (aioimaplib) для маркировки писем.
        config: Объект конфигурации с правилами маршрутизации и SMTP/ИИ параметрами.
        messages: Словарь с входящими письмами, где ключ — UID письма, 
                  а значение — объект EmailMessage.
    """
    partition_result = partition_emails(messages=messages, config=config) 

    messages_to_forward = []
    ids_to_mark_as_read = []

    for email_info in partition_result.failed:
        ids_to_mark_as_read.append(email_info.msg_id)

    for email_info, recipients in partition_result.classified.items():
        messages_to_forward.append(
            ForwardMessage(msg_id=email_info.msg_id, original_message=messages[email_info.msg_id], recipients=recipients)
        )

    if partition_result.passed:
        logger.info("Отправляю письма на классификацию ИИ")
        timeout_config = httpx.Timeout(timeout=30, connect=10)

        async with httpx.AsyncClient(timeout=timeout_config) as http_client:
            classify_results = await asyncio.gather(
                *(classify_email(client=http_client, email_info=email_info, original_message=messages[email_info.msg_id], config=config) 
                  for email_info in partition_result.passed)    
            )

        for email_info, classify in zip(partition_result.passed, classify_results):
            if classify is None: 
                continue 

            if classify.is_target and classify.department: 
                redirect = getattr(config.redirection, classify.department.lower(), None)

                if redirect is not None:
                    messages_to_forward.append(
                        ForwardMessage(msg_id=email_info.msg_id, original_message=messages[email_info.msg_id], recipients=redirect)
                    ) 
                else:
                    # по сути таких ситуаций быть не должно, но на всякий случай
                    logger.error(f"{classify.department.lower()=} не удалось найти в {config.redirection}")
            else:
                ids_to_mark_as_read.append(email_info.msg_id) 

    if messages_to_forward:
        logger.info(f"Начинаю переотправку писем ответственным: {len(messages_to_forward)}")
        forward_ids = await forward_messages(messages=messages_to_forward, config=config)
        ids_to_mark_as_read.extend(forward_ids)

    if ids_to_mark_as_read:
        await mark_as_read(client=client, msg_ids=",".join(ids_to_mark_as_read))


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

            await process_messages_batch(client=client, config=config, messages=saved_messages)

            # TODO: Убрать потом отсюда break
            break 


if __name__ == "__main__":
    try:
        asyncio.run(main(Config.from_env()))
    except KeyboardInterrupt:
        logger.info("Работа воркера бережно остановлена пользователем (Ctrl+C)")
    except Exception as global_err:
        logger.error(f"Неожиданная ошибка в работе воркера: {global_err}", exc_info=True)