from __future__ import annotations

from dataclasses import dataclass, field, asdict


@dataclass
class Organization:
    """Одна карточка организации, как её видит пользователь в Яндекс.Картах."""

    name: str = ""
    category: str = ""
    address: str = ""
    phone: str = ""
    rating: float | None = None
    reviews_count: int | None = None
    has_site: bool = False
    site_url: str = ""
    socials: list[str] = field(default_factory=list)   # ["Telegram", "WhatsApp", ...]
    card_url: str = ""
    org_id: str = ""   # уникальный id из Яндекс.Карт, нужен для дедупликации

    def dedupe_key(self) -> str:
        return self.org_id or f"{self.name.strip().lower()}|{self.address.strip().lower()}"

    def rating_display(self) -> str:
        if self.rating is None:
            return "Нет оценок"
        if self.reviews_count:
            return f"{self.rating:.1f} · {self.reviews_count}"
        return f"{self.rating:.1f}"

    def to_row(self) -> dict:
        """Плоский вид для таблиц/экспорта — как heads/keys в рефке."""
        return {
            "name": self.name,
            "category": self.category,
            "address": self.address,
            "phone": self.phone or "Телефон не указан",
            "rating": self.rating_display(),
            "links": ", ".join(
                (["Сайт"] if self.has_site else []) + self.socials
            ) or "Без сайта",
        }

    def to_dict(self) -> dict:
        return asdict(self)
