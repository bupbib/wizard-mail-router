import logging 
from typing import Literal
from contextlib import asynccontextmanager
from collections.abc import AsyncGenerator

import aioimaplib

from config import Config 


logger = logging.getLogger(__name__)


def is_valid_message(subject: str, in_reply_to: str, references: str) -> bool:
    """
    Проверяет, является ли письмо новым первичным обращением.

    Отфильтровывает ответы (Re:) и пересланные сообщения (Fwd:),
    анализируя заголовки цепочки и префиксы темы.

    Args:
        subject: Тема письма.
        in_reply_to: Значение IMAP-заголовка In-Reply-To.
        references: Значение IMAP-заголовка References.

    Returns:
        True, если письмо новое и подлежит дальнейшей обработке.
        False, если это ответ или пересылка.
    """
    if in_reply_to or references:
        return False 

    if subject.lower().startswith(("fwd:", "fw:", "пересл:", "re:", "отв:")):
        return False 

    return True 


async def _connection_attempt(
    client: aioimaplib.IMAP4_SSL, 
    config: Config, 
    phase: Literal["login", "box"]
) -> None:
    """
    Выполняет попытку авторизации или выбора папки на IMAP-сервере.

    Функция является вспомогательной и инкапсулирует общую логику для двух
    последовательных этапов: логин и выбор рабочей папки. В случае неудачи
    выбрасывает исключение с детальным сообщением от сервера.
    """
    is_login_phase = (phase == "login")
    email, password = config.mail.email, config.mail.password
    work_box = config.mail.work_box
    
    if is_login_phase:
        status, data = await client.login(user=email, password=password)
    else:
        status, data = await client.select(work_box)

    if status == "OK":
        success_msg = (
            f"Авторизация к {email} прошла" if is_login_phase 
            else f"Подключение к папке {work_box} прошло"
        ) + f" успешно, текущее состояние: {client.get_state()}"
        logger.info(success_msg)
    else:
        report = data[0].decode() if data else "неизвестная ошибка"
        error_msg = (
            f"Не удалось авторизоваться под логином {email}" if is_login_phase
            else f"Не получилось подключиться к папке {config.mail.work_box}"
        ) + f", статус ответа: {status}, ответ: {report}"
        logger.info(error_msg)
        raise Exception(error_msg)


@asynccontextmanager
async def get_mail_client(config: Config) -> AsyncGenerator[aioimaplib.IMAP4_SSL, None]:
    """
    Асинхронный контекстный менеджер для подключения к IMAP-серверу.

    Создаёт клиент, устанавливает соединение, выполняет авторизацию
    и выбор рабочей папки. После выхода из блока `with` гарантированно
    закрывает сессию через logout() (если состояние позволяет).

    При возникновении любой ошибки на этапах подключения, логина или выбора
    папки функция логирует ошибку и пробрасывает исключение выше,
    предварительно закрывая клиент (если он был создан).

    Args:
        config (Config): Объект конфигурации.

    Yields:
        aioimaplib.IMAP4_SSL: Активный клиент с уже выбранной папкой
        (состояние SELECTED). Используйте его для выполнения команд
        поиска, чтения, удаления писем.

    Raises:
        Exception: Любая ошибка, возникшая при создании клиента,
        авторизации или выборе папки. Сообщение содержит детали
        от сервера или описание проблемы.
    """
    client = aioimaplib.IMAP4_SSL(
        host=config.mail.host,
        port=config.mail.port,
        timeout=config.mail.timeout
    )
    logger.info(f"Клиент создан, состояние (до wait_hello): {client.get_state()}")

    try:
        await client.wait_hello_from_server()
        logger.info(f"Строка с приветствием успешно принята, состояние: {client.get_state()}") 

        await _connection_attempt(client=client, config=config, phase="login")
        await _connection_attempt(client=client, config=config, phase="box")       
        
        yield client 
    except Exception as err:
        logger.error(f"Произошла неожиданная ошибка при получении клиента: {err}", exc_info=True)
        raise
    finally:
        if client.get_state() not in ("STARTED", "LOGOUT"):
            logger.info(f"Закрываем клиент с помощью logout(), текущее состояние: {client.get_state()}")
            await client.logout() 
            logger.info(f"Состояние после закрытия: {client.get_state()}")
        else:
            logger.info(f"Текущее состояние клиента: {client.get_state()} уже находилось в ('STARTED', 'LOGOUT'), закрывать нечего")





