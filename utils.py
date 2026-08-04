import logging 
import httpx
from json import JSONDecodeError
from email.message import EmailMessage
from email.utils import parseaddr

from pydantic import ValidationError

from models import EmailInfo, FilterResult, ApiResponse
from config import Config 


logger = logging.getLogger(__name__)


def partition_emails(messages: dict[str, EmailMessage], config: Config) -> FilterResult:
    """
    Разделяет входящие письма на три категории:
    1. Ответы/пересылки (failed) — исключаются из обработки.
    2. Совпадения по локальным правилам (classified) — маршрутизируются в отделы 
       напрямую без обращения к ИИ.
    3. Новые целевые письма (passed) — отправляются на классификацию в ИИ-сервис.

    Критерии фильтрации:
        - Исключение: наличие заголовков In-Reply-To/References или префиксов Re:, Fwd:, etc.
        - Локальное правило: адрес отправителя (извлекается через parseaddr) найден в config.classification.rules.
        - ИИ-классификация: письмо является новым и не подпадает под локальные правила.

    Args:
        messages (dict[str, EmailMessage]): Словарь извлеченных писем {msg_id: EmailMessage}.
        config (Config): Объект конфигурации приложения с правилами маршрутизации и фильтрации.

    Returns:
        FilterResult: Объект результатов со следующими полями:
            - classified (dict[EmailInfo, list[str]]): Письма, классифицированные без ИИ,
              маппированные на список адресов перенаправления.
            - passed (list[EmailInfo]): Письма-инициаторы новых тем, требующие AI-классификации.
            - failed (list[EmailInfo]): Письма-ответы или пересылки, не требующие AI-обработки.
    """
    classified = {}
    passed = []
    failed = []
    skip_prefixes = ("re:", "отв:")

    logger.info(f"Пришло {len(messages)} писем на первичный анализ")

    for msg_id, email in messages.items():
        part = email.get_body(preferencelist=("plain", "html"))
        body = part.get_content().strip() if part else ""

        email_info = EmailInfo(
            msg_id=msg_id,
            subject=(email.get("Subject") or "").strip(),
            from_=email.get("From") or "",
            in_reply_to=email.get("In-Reply-To") or "",
            references=email.get("References") or "",
            body=body
        )

        _, clean_email = parseaddr(email_info.from_)
        clean_email = clean_email.lower().strip()

        if email_info.subject.lower().startswith(skip_prefixes):
            result_partition = "НЕ прошло первичный анализ"
            failed.append(email_info)
        elif (department := config.classification.rules.get(clean_email)) is not None:
            redirect = getattr(config.redirection, department, None) 

            if redirect is not None:
                classified[email_info] = redirect
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