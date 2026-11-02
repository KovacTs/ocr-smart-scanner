import uuid
from datetime import datetime, timezone
from sqlalchemy import (
    Column,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    JSON,
    String,
    Text,
)
from sqlalchemy.orm import relationship
from app.db.database import Base


def utc_now():
    return datetime.now(timezone.utc)


class Scan(Base):
    __tablename__ = "scans"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id = Column(
        String(36), ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False
    )
    document_type = Column(String(64), default="FACTURA_ELECTRONICA", index=True)
    folio = Column(String(64), nullable=True, index=True)
    image_url = Column(String(512), nullable=False)
    thumbnail_url = Column(String(512), nullable=False)
    original_size_bytes = Column(Integer, default=0)
    file_size_bytes = Column(Integer, default=0)
    blur_score = Column(Float, default=0.0)
    current_raw_text = Column(Text, default="")
    current_normalized_data = Column(JSON, default=dict)
    issue_date = Column(String(32), nullable=True, index=True)  # ISO 8601 YYYY-MM-DD
    total_amount = Column(Float, default=0.0)
    created_at = Column(DateTime(timezone=True), default=utc_now, nullable=False)
    updated_at = Column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False
    )

    user = relationship("User", back_populates="scans")
    versions = relationship(
        "ScanVersion",
        back_populates="scan",
        cascade="all, delete-orphan",
        order_by="ScanVersion.version_number.desc()",
    )


class ScanVersion(Base):
    __tablename__ = "scan_versions"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    scan_id = Column(
        String(36), ForeignKey("scans.id", ondelete="CASCADE"), index=True, nullable=False
    )
    version_number = Column(Integer, nullable=False)
    raw_text = Column(Text, default="")
    normalized_data = Column(JSON, default=dict)
    modified_by = Column(String(128), default="OCR_AUTOMATIC")
    created_at = Column(DateTime(timezone=True), default=utc_now, nullable=False)

    scan = relationship("Scan", back_populates="versions")
