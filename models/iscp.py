from __future__ import annotations

from typing import final, override

from sqlalchemy import (
    JSON,
    Boolean,
    Float,
    ForeignKey,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from models.base import Base


@final
class IscpStop(Base):
    """One stop in the ISCP registry, stored as a first-class source record.

    Unlike the canonical Stop model, this is a faithful representation of
    the registry itself: the source name fields are kept separate, every
    GPS coordinate pair is stored as an IscpPlatform row, and the parsed
    source fields are preserved in raw_fields for auditing.
    """

    __tablename__ = "iscp_stops"

    iscp_id: Mapped[str] = mapped_column(String, primary_key=True)
    municipality: Mapped[str] = mapped_column(String, nullable=False, default="")
    local_part: Mapped[str | None] = mapped_column(String)
    locality: Mapped[str | None] = mapped_column(String)
    name_ids: Mapped[str | None] = mapped_column(String)
    name_mhd: Mapped[str | None] = mapped_column(String)
    display_name: Mapped[str] = mapped_column(String, nullable=False, default="")
    district: Mapped[str | None] = mapped_column(String)
    country: Mapped[str | None] = mapped_column(String)
    transport_system: Mapped[str | None] = mapped_column(String)
    validity: Mapped[str | None] = mapped_column(String)
    is_cancelled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    source_file: Mapped[str] = mapped_column(String, nullable=False, default="")
    raw_fields: Mapped[dict[str, str]] = mapped_column(JSON, default=dict)

    platforms: Mapped[list[IscpPlatform]] = relationship(
        back_populates="parent",
        cascade="all, delete-orphan",
        lazy="selectin",
        order_by="IscpPlatform.position",
    )

    @property
    def lat(self) -> float | None:
        return self.platforms[0].lat if self.platforms else None

    @property
    def lon(self) -> float | None:
        return self.platforms[0].lon if self.platforms else None

    @override
    def __repr__(self) -> str:
        return f"<IscpStop(iscp_id={self.iscp_id!r}, name={self.display_name!r})>"


@final
class IscpPlatform(Base):
    """One GPS coordinate pair belonging to an ISCP registry stop.

    The registry does not name or number its platforms; ``position`` is
    simply the order of the coordinate pair in the source record.
    """

    __tablename__ = "iscp_platforms"

    iscp_id: Mapped[str] = mapped_column(
        String, ForeignKey("iscp_stops.iscp_id"), primary_key=True
    )
    position: Mapped[int] = mapped_column(Integer, primary_key=True)
    lat: Mapped[float] = mapped_column(Float, nullable=False)
    lon: Mapped[float] = mapped_column(Float, nullable=False)

    parent: Mapped[IscpStop] = relationship(back_populates="platforms")

    @override
    def __repr__(self) -> str:
        return f"<IscpPlatform(iscp_id={self.iscp_id!r}, position={self.position!r})>"


@final
class IscpMatch(Base):
    """A link between a canonical stop and an ISCP registry stop."""

    __tablename__ = "iscp_matches"
    __table_args__ = (UniqueConstraint("iscp_id", name="uq_iscp_matches_iscp_id"),)

    stop_id: Mapped[str] = mapped_column(
        String, ForeignKey("stops.id"), primary_key=True
    )
    iscp_id: Mapped[str] = mapped_column(
        String, ForeignKey("iscp_stops.iscp_id"), nullable=False, index=True
    )
    quality: Mapped[str] = mapped_column(String, nullable=False)
    distance_m: Mapped[float | None] = mapped_column(Float)

    @override
    def __repr__(self) -> str:
        return (
            f"<IscpMatch(stop_id={self.stop_id!r}, iscp_id={self.iscp_id!r}, "
            f"quality={self.quality!r})>"
        )
