
from pydantic import BaseModel, Json
from typing import List, Optional
from datetime import datetime

class ContentDto(BaseModel):
    text: str

class AnalysisDto(BaseModel):
    company: Optional[str]
    company_match: Optional[str]
    original_text: Optional[str]
    original_language: Optional[str]
    translated_text: Optional[str]
    mana_assistant_result: Optional[str]
    mana_assistant_score: Optional[float]
    status: Optional[str]
    status_exception: Optional[str]

class RssFeedUrlDto(BaseModel):
    rss_url: str
    since: datetime

class CompanyDto(BaseModel):
    name: str
    synonyms: Optional[List[str]]