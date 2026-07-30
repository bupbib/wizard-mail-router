from pydantic import BaseModel 


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