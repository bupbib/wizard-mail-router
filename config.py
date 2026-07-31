import sys 
import logging 

from pydantic import BaseModel
from environs import Env
from pythonjsonlogger.jsonlogger import JsonFormatter  # type: ignore 


logger = logging.getLogger(__name__)


class MailSettings(BaseModel):
    imap_host: str 
    imap_port: int 
    smtp_host: str 
    smtp_port: int
    email: str 
    password: str 
    work_box: str 
    timeout: float
    retry_interval: float


class RedirectionSettings(BaseModel):
    sales: list[str]
    onec: list[str]
    ork: list[str]
    hr: list[str]


class ClassificationSettings(BaseModel):
    rules: dict[str, str]


class ApiSettings(BaseModel):
    url: str 
    token: str 
    version: str 
    method_name: str 
    argument_name: str 


class Config(BaseModel):
    mail: MailSettings
    redirection: RedirectionSettings
    classification: ClassificationSettings
    api: ApiSettings

    @classmethod
    def from_env(cls, path: str | None = None) -> "Config":
        env = Env()
        env.read_env(path, override=True)

        mail = MailSettings(
            imap_host=env("IMAP_HOST"),
            imap_port=env.int("IMAP_PORT"),
            smtp_host=env("SMTP_HOST"),
            smtp_port=env.int("SMTP_PORT"),
            email=env("EMAIL"),
            password=env("PASSWORD"),
            work_box=env("WORK_BOX"),
            timeout=env.float("TIMEOUT"),
            retry_interval=env.float("RETRY_INTERVAL")
        )

        redirection = RedirectionSettings(
            sales=env.list("SALES"),
            onec=env.list("ONEC"),
            ork=env.list("ORK"),
            hr=env.list("HR")
        )

        classification = ClassificationSettings(
            rules=env.dict("RULES")
        )

        api = ApiSettings(
            url=env("URL"),
            token=env("TOKEN"),
            version=env("VERSION"),
            method_name=env("METHOD_NAME"),
            argument_name=env("ARGUMENT_NAME")
        )

        logger.info("Все конфигурационные данные успешно настроены")

        return cls(
            mail=mail, 
            redirection=redirection, 
            classification=classification,
            api=api
        )

    @staticmethod
    def setup_logging() -> None:
        root_logger = logging.getLogger()
        root_logger.setLevel(logging.INFO)

        root_logger.handlers.clear()
        stdout_handler = logging.StreamHandler(sys.stdout)

        formatter = JsonFormatter(
            fmt='%(asctime)s %(levelname)s %(filename)s %(lineno)d %(name)s %(message)s',
            datefmt='%d-%m-%YT%H:%M:%S',
            json_ensure_ascii=False,
            rename_fields={
                'asctime': 'time',  
                'levelname': 'level',
                'filename': 'file',
                'lineno': 'line_number',
                'name': 'logger_name',
                'message': 'message'
            }
        )

        stdout_handler.setFormatter(formatter)
        root_logger.addHandler(stdout_handler)

        logger.info("Формат логов успешно настроен")
