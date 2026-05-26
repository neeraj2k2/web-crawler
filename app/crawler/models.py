from typing import Optional
from pydantic import BaseModel


class Metadata(BaseModel):
    title: Optional[str] = None
    description: Optional[str] = None
    keywords: list[str] = []
    og_title: Optional[str] = None
    og_description: Optional[str] = None
    og_image: Optional[str] = None
    canonical_url: Optional[str] = None
    lang: Optional[str] = None
    robots: Optional[str] = None
    schema_type: Optional[str] = None


class Links(BaseModel):
    internal: list[str] = []
    external: list[str] = []
    internal_count: int = 0
    external_count: int = 0


class Content(BaseModel):
    h1: list[str] = []
    h2: list[str] = []
    h3: list[str] = []
    body_text_preview: Optional[str] = None
    word_count: int = 0
    links: Optional[Links] = None


class Classification(BaseModel):
    page_type: str
    topics: list[str] = []
