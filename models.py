from pydantic import BaseModel 


class EmailInfo(BaseModel):
    msg_id: str 
    subject: str 
    from_: str 
    in_reply_to: str 
    references: str
    body: str  


class FilterResult(BaseModel):
    passed: list[EmailInfo]
    failed: list[EmailInfo] 