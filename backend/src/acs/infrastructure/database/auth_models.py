"""Local laboratory accounts, separated by administrative and CPE purpose."""

from uuid import UUID

from sqlalchemy import Boolean, CheckConstraint, String, UniqueConstraint, false
from sqlalchemy.orm import Mapped, mapped_column

from acs.infrastructure.database.base import Base


class AuthAccount(Base):
    __tablename__ = "auth_accounts"
    __table_args__ = (
        UniqueConstraint("kind", "username", name="uq_auth_account_username"),
        UniqueConstraint(
            "oui", "product_class", "serial_number", name="uq_auth_account_device"
        ),
        CheckConstraint("kind IN ('admin', 'cpe')", name="account_kind"),
        CheckConstraint(
            "(kind = 'admin' AND role IN ('administrator', 'support', 'pending') "
            "AND shared_cpe = false AND oui IS NULL AND product_class IS NULL AND serial_number IS NULL) OR "
            "(kind = 'cpe' AND role = 'device' AND ((shared_cpe = false AND oui IS NOT NULL "
            "AND product_class IS NOT NULL AND serial_number IS NOT NULL) OR "
            "(shared_cpe = true AND oui IS NULL AND product_class IS NULL AND serial_number IS NULL)))",
            name="account_identity",
        ),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True)
    kind: Mapped[str] = mapped_column(String(8))
    username: Mapped[str] = mapped_column(String(64))
    password_hash: Mapped[str] = mapped_column(String(256))
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    shared_cpe: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default=false()
    )
    role: Mapped[str] = mapped_column(String(24))
    oui: Mapped[str | None] = mapped_column(String(6))
    product_class: Mapped[str | None] = mapped_column(String(64))
    serial_number: Mapped[str | None] = mapped_column(String(64))
