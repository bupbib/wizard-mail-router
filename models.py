import json 
from typing import Literal, Any 
from dataclasses import dataclass
from datetime import datetime 

from pydantic import BaseModel, Field
from email.message import EmailMessage


type DepartmentType = Literal["ork", "hr", "sales"]


@dataclass
class EmailInfo:
    msg_id: str 
    subject: str 
    from_: str 
    to: str 
    in_reply_to: str 
    references: str
    received_at: datetime 
    body: str  
    original_message: EmailMessage
    department: DepartmentType | None = None 
    recipients: list[str] | None = None 

    def model_to_api(self) -> str:
        return json.dumps(
            obj={
                "msg_id": self.msg_id,
                "subject": self.subject,
                "from": self.from_,
                "in_reply_to": self.in_reply_to,
                "references": self.references,
                "body": self.body 
            },
            ensure_ascii=False 
        )

    def model_to_report(self) -> list[str]:
        return [
            self.received_at.strftime("%d.%m.%Y %H:%M"),
            self.from_,
            self.subject,
            self.department or "нецелевое",
            ", ".join(self.recipients) if self.recipients else "не_пересылалось"
        ]


@dataclass
class FilterResult:
    classified: list[EmailInfo]
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