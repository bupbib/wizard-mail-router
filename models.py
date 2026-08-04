from typing import Literal, Any 
from dataclasses import dataclass
from datetime import datetime 

from pydantic import BaseModel, Field
from email.message import EmailMessage


type DepartmentType = Literal["ork", "hr", "sales"]


class EmailInfo(BaseModel, frozen=True):
    msg_id: str 
    subject: str 
    from_: str 
    in_reply_to: str 
    references: str
    received_at: str 
    body: str  


@dataclass
class ForwardMessage:
    msg_id: str 
    original_message: EmailMessage
    recipients: list[str]


class ClassifyMessage(BaseModel):
    email_info: EmailInfo
    department: str 
    redirect: list[str]


class FilterResult(BaseModel):
    classified: list[ClassifyMessage]
    passed: list[EmailInfo]
    failed: list[EmailInfo] 


class ApiResponse(BaseModel):
    """Полный ответ от API"""
    version: str 
    id: str 
    success: bool 
    payload: ApiMethodPayload | ApiFilePayload


class ApiFilePayload(BaseModel):
    file: ApiFileResult 


class ApiMethodPayload(BaseModel):
    """Payload ответа API"""
    result: ApiMethodResult 


class ApiMethodResult(BaseModel):
    """Результат выполнения метода API"""
    data: ResultApiClassification
    attachments: list[Any]

class ApiFileResult(BaseModel):
    id: str 
    name: str 
    size: int | float 
    mime_type: str 
    created_at: datetime
    expires_at: datetime 


class ResultApiClassification(BaseModel):
    is_target: bool 
    department: DepartmentType | None 
    reasoning: str = Field(..., min_length=1)


class ReportRecord(BaseModel):
    msg_id: str 
    received_at: str 
    from_: str 
    subject: str 
    department: DepartmentType | None = None 
    recipients: list[str] | None = None 