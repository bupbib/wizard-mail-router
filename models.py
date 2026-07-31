from typing import Literal, Any 
from dataclasses import dataclass

from pydantic import BaseModel, Field
from email.message import EmailMessage


class EmailInfo(BaseModel, frozen=True):
    msg_id: str 
    subject: str 
    from_: str 
    in_reply_to: str 
    references: str
    body: str  


@dataclass
class ForwardMessage:
    msg_id: str 
    original_message: EmailMessage
    recipients: list[str]


class FilterResult(BaseModel):
    classified: dict[EmailInfo, list[str]]
    passed: list[EmailInfo]
    failed: list[EmailInfo] 


class ApiResponse(BaseModel):
    """Полный ответ от API"""
    version: str 
    id: str 
    success: bool 
    payload: ApiPayload 


class ApiPayload(BaseModel):
    """Payload ответа API"""
    result: ApiResult 


class ApiResult(BaseModel):
    """Результат выполнения метода API"""
    data: ResultApiClassification
    attachments: list[Any]


class ResultApiClassification(BaseModel):
    is_target: bool 
    department: Literal["ork", "hr", "sales"] | None 
    reasoning: str = Field(..., min_length=1)