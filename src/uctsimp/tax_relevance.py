"""Klasifikácia transakcií pre prehľad daň / nedaň (orientačné, nie právne poradenstvo)."""

from __future__ import annotations

from .models import TransactionCategory

# Nedaňové toky: presun peňazí medzi bankou a brokom (nie kapitálový príjem sám o sebe).
# Ostatné kategórie (obchod, provízie, úrok, FX, …) sa zobrazia pod „daňové“
# — detail podľa DSR a iných pravidiel riešte s poradcom.
NEDANOVE_KATEGORIE: frozenset[str] = frozenset(
    {TransactionCategory.DEPOSIT_WITHDRAWAL.value}
)


def je_danovo_relevantna_kategoria(kategoria: str) -> bool:
    return kategoria not in NEDANOVE_KATEGORIE


def sql_nedanove_kategorie_in() -> str:
    """Hodnoty pre `category IN (...)` v SQL (len názvy kategórií)."""
    return ", ".join(f"'{c}'" for c in sorted(NEDANOVE_KATEGORIE))
