from datetime import datetime
from typing import Any, List, Optional
from pydantic import BaseModel, ConfigDict, Field, model_validator
from app.core.security import create_signed_file_url


class PartyInfo(BaseModel):
    tax_id: str = ""
    legal_name: str = ""
    address: str = ""


class MonetaryAmounts(BaseModel):
    net_amount: float = 0.0
    tax_amount: float = 0.0
    exempt_amount: float = 0.0
    total_amount: float = 0.0
    currency: str = "CLP"


class NormalizedDocumentData(BaseModel):
    document_type: str = "FACTURA_ELECTRONICA"
    document_number: str = ""
    issue_date: str = ""  # YYYY-MM-DD
    issuer: PartyInfo = Field(default_factory=PartyInfo)
    receiver: PartyInfo = Field(default_factory=PartyInfo)
    amounts: MonetaryAmounts = Field(default_factory=MonetaryAmounts)
    confidence_score: float = 0.85


class ScanVersionResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    scan_id: str
    version_number: int
    raw_text: str
    normalized_data: dict[str, Any]
    modified_by: str
    created_at: datetime


class ScanResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    user_id: str
    document_type: str
    folio: Optional[str] = None
    image_url: str
    thumbnail_url: str
    signed_image_url: Optional[str] = None
    signed_thumbnail_url: Optional[str] = None
    original_size_bytes: int
    file_size_bytes: int
    blur_score: float
    current_raw_text: str
    current_normalized_data: dict[str, Any]
    issue_date: Optional[str] = None
    total_amount: float
    status: str = "COMPLETED"
    deleted_at: Optional[datetime] = None
    created_at: datetime
    updated_at: datetime
    versions: List[ScanVersionResponse] = []

    @model_validator(mode="after")
    def populate_signed_urls(self) -> "ScanResponse":
        if not self.signed_image_url and self.image_url:
            self.signed_image_url = create_signed_file_url(
                self.image_url, self.user_id, self.id, "document.webp"
            )
        if not self.signed_thumbnail_url and self.thumbnail_url:
            self.signed_thumbnail_url = create_signed_file_url(
                self.thumbnail_url, self.user_id, self.id, "thumbnail.webp"
            )
        return self


class ScanUpdateRequest(BaseModel):
    raw_text: Optional[str] = None
    normalized_data: dict[str, Any]


class BatchExportRequest(BaseModel):
    scan_ids: Optional[List[str]] = None
    document_type: Optional[str] = None
    date_from: Optional[str] = None
    date_to: Optional[str] = None


class BatchDeleteRequest(BaseModel):
    scan_ids: List[str]
    permanent: bool = False


class BatchRestoreRequest(BaseModel):
    scan_ids: List[str]


class FocusCheckResponse(BaseModel):
    is_focused: bool
    document_detected: bool
    blur_score: float
    threshold: float
    roi_bbox: Optional[List[int]] = None
    status_color: str  # "RED", "YELLOW", "GREEN"
    message: str

