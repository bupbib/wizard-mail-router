from typing import Literal 

from pydantic import BaseModel, Field


class EmailInfo(BaseModel, frozen=True):
    msg_id: str 
    subject: str 
    from_: str 
    in_reply_to: str 
    references: str
    body: str  


class FilterResult(BaseModel):
    classified: dict[EmailInfo, list[str]]
    passed: list[EmailInfo]
    failed: list[EmailInfo] 


class AnswerFromApi(BaseModel):
    is_target: bool 
    department: Literal["ork", "hr", "sales"] | None 
    reasoning: str = Field(..., min_length=1)