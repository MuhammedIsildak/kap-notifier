from __future__ import annotations

from collections.abc import Iterable

# Türkçe karakterleri ASCII'ye indirger. Amaç kelime eşleştirmesini
# büyük/küçük harf ve noktalı/noktasız i farkından bağımsız yapmak.
_TR_MAP = str.maketrans(
    {
        "ğ": "g",
        "ü": "u",
        "ş": "s",
        "ö": "o",
        "ç": "c",
        "â": "a",
        "î": "i",
        "û": "u",
    }
)


def normalize(text: object) -> str:
    """Metni eşleştirme için sadeleştirir.

    - İ/I/ı -> i
    - Türkçe harfler ASCII'ye çevrilir
    - Unicode büyük İ'nin bıraktığı combining dot temizlenir
    - Boşluklar tekilleştirilir
    """
    if text is None:
        return ""
    value = str(text).replace("İ", "i").replace("I", "i").replace("ı", "i")
    value = value.casefold().translate(_TR_MAP).replace("\u0307", "")
    return " ".join(value.split())


def matching_terms(text: object, terms: Iterable[str]) -> list[str]:
    haystack = normalize(text)
    return [term for term in terms if normalize(term) and normalize(term) in haystack]


def subject_rank(subject: object, priority_terms: Iterable[str]) -> int:
    """Öncelik listesindeki ilk eşleşmenin sırası; yoksa büyük sayı."""
    haystack = normalize(subject)
    for rank, term in enumerate(priority_terms):
        needle = normalize(term)
        if needle and needle in haystack:
            return rank
    return 10_000