import logging 
from email.message import EmailMessage
from pprint import pprint 

from models import EmailInfo, FilterResult

logger = logging.getLogger(__name__)


def partition_emails(messages: dict[str, EmailMessage]) -> FilterResult:
    """
    Разделяет письма на две категории: новые темы и ответы/пересылки.

    Критерии определения ответа/пересылки:
        - Наличие заголовков In-Reply-To или References
        - Тема начинается с одного из префиксов: Re:, Fwd:, Пересл:, Отв:

    Письма без признаков ответа считаются новыми темами и требуют AI-классификации.
    Ответы и пересылки можно маршрутизировать на основе родительского письма
    или просто пометить прочитанными.

    Args:
        messages: Словарь с письмами.

    Returns:
        FilterResult: Объект с двумя списками:
            - passed: письма-инициаторы новых тем (требуют AI)
            - failed: письма-ответы или пересылки (не требуют AI)
    """
    passed = []
    failed = []
    skip_prefixes = ("fwd:", "fw:", "пересл:", "re:", "отв:")

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

        if email_info.in_reply_to or email_info.references or email_info.subject.lower().startswith(skip_prefixes):
            result_partition = "НЕ прошло"
            failed.append(email_info)
        else:
            result_partition = "прошло"
            passed.append(email_info)

        logger.info(f"Письмо {email_info!r} {result_partition} первичный анализ")

    logger.info(f"Прошли первичный анализ: {len(passed)} шт., не прошли: {len(failed)} шт.")
    return FilterResult(passed=passed, failed=failed)