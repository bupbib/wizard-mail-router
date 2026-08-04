import logging 
from typing import cast 
from json import JSONDecodeError
from email.message import EmailMessage
from email.utils import parseaddr
from datetime import datetime 

import httpx
from pydantic import ValidationError

from models import EmailInfo, FilterResult, ApiResponse, DepartmentType
from config import Config 


logger = logging.getLogger(__name__)


def partition_emails(messages: dict[str, EmailMessage], config: Config) -> FilterResult:
    """
    Разделяет входящие письма на три категории:
    1. Ответы (failed) — исключаются из обработки.
    2. Совпадения по локальным правилам (classified) — маршрутизируются напрямую.
    3. Остальные (passed) — отправляются на ИИ-классификацию.

    Критерии:
        - Ответ: тема начинается с Re: или Отв:.
        - Локальное правило: отправитель найден в config.classification.rules.
        - ИИ-классификация: все остальные письма.

    Args:
        messages: Словарь писем {msg_id: EmailMessage}.
        config: Объект конфигурации.

    Returns:
        FilterResult: Объект с полями:
            - classified (list[EmailInfo]): Письма с заполненными department и recipients.
            - passed (list[EmailInfo]): Письма для ИИ-классификации.
            - failed (list[EmailInfo]): Письма-ответы.
    """
    classified = []
    passed = []
    failed = []
    skip_prefixes = ("re:", "отв:")

    logger.info(f"Пришло {len(messages)} писем на первичный анализ")

    for msg_id, email in messages.items():
        part = email.get_body(preferencelist=("plain", "html"))
        body = part.get_content().strip() if part else ""

        if (received_at := email.get("Date")) is not None:
            received_at = received_at.datetime
        else:
            received_at = datetime.now()

        email_info = EmailInfo(
            msg_id=msg_id,
            subject=(email.get("Subject") or "").strip(),
            from_=email.get("From") or "",
            to=email.get("To") or "",
            in_reply_to=email.get("In-Reply-To") or "",
            references=email.get("References") or "",
            received_at=received_at,
            body=body,
            original_message=email
        )

        _, clean_email = parseaddr(email_info.from_)
        clean_email = clean_email.lower().strip()

        if email_info.subject.lower().startswith(skip_prefixes):
            result_partition = "НЕ прошло первичный анализ"
            failed.append(email_info)
        elif (department := config.classification.rules.get(clean_email)) is not None:
            redirect = getattr(config.redirection, department, None) 

            if redirect is not None:
                email_info.department = cast(DepartmentType, department)
                email_info.recipients = redirect 
                classified.append(email_info)
                result_partition = "успешно классифицировано для перенаправления без помощи ИИ"
            else:
                logger.error(
                    f"Несмотря на то, что департамент: {department} удалось идентифицировать в {config.classification.rules} "
                    f"- в {config.redirection} нет аттрибута {department}, необходимо добавить значения в .env,"
                    f"текущее письмо было добавлено в классификацию для ИИ"
                )
                result_partition = "не удалось успешно классифицировать из-за нестыковки параметров, добавлено для классификации ИИ"
                passed.append(email_info)
        else:
            result_partition = "прошло первичный анализ"
            passed.append(email_info)

        logger.info(f"Письмо {email_info!r} {result_partition}")

    logger.info(f"Классифицированы без ИИ: {len(classified)} шт., добавлены на классификацию ИИ: {len(passed)} шт., не прошли: {len(failed)} шт.")
    return FilterResult(classified=classified, passed=passed, failed=failed)


async def safe_api_post(
    client: httpx.AsyncClient,
    url: str,
    headers: dict,
    **kwargs
) -> ApiResponse | None:
    """
    Безопасно выполняет POST-запрос к API с обработкой всех ошибок.
    
    Функция-обертка для httpx.post, которая перехватывает и логирует:
        - Сетевые ошибки (HTTPError)
        - Ошибки парсинга JSON (JSONDecodeError)
        - Ошибки валидации Pydantic (ValidationError)
        - HTTP-статусы, отличные от 200
    
    Args:
        client: Асинхронный HTTP-клиент (httpx.AsyncClient).
        url (str): Адрес эндпоинта.
        headers (dict): HTTP-заголовки запроса.
        **kwargs: Дополнительные параметры httpx (json, files, params и т.д.).
    """
    response = None
    try:
        response = await client.post(
            url=url,
            headers=headers,
            **kwargs
        ) 

        status_code = response.status_code

        if status_code != 200:
            logger.error(f"Апи ответил не 200 статус-кодом, статус-код ответа: {status_code}, ответ: {response.text}")
            return 

        return ApiResponse(**response.json())
    except httpx.HTTPError as http_err:
        logger.error(f"Ошибка сети при подключении к API: {http_err}", exc_info=True) 
    except (JSONDecodeError, ValidationError) as valid_err:
        if response is not None:
            logger.error(
                f"Не получилось распарсить структуру ответа в известную схему, полученный ответ с апи: {response.text}"
                f", ошибка: {valid_err}", exc_info=True
            ) 
    except Exception as err:
        logger.error(f"Неожиданная ошибка при подключении к API: {err}", exc_info=True) 