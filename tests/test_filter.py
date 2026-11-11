from pathlib import Path

from src.config import AnalystConfig, FilterConfig, load_config
from src.fetch import fetch_from_file
from src.filter import evaluate, select_candidates
from src.models import Disclosure
from src.normalize import normalize

ROOT = Path(__file__).resolve().parents[1]


def test_turkish_normalization():
    assert normalize("ŞİRKET İŞLEMLERİ") == "sirket islemleri"
    assert normalize("Portföy Dağılımı") == "portfoy dagilimi"
    assert normalize("KÂR DAĞITIMI") == "kar dagitimi"


def test_default_config_keeps_relevant_and_drops_noise():
    config = load_config(ROOT / "config.yaml")
    disclosures = fetch_from_file(ROOT / "tests/fixtures/light_sample.json")
    selected = select_candidates(disclosures, config.filters, config.companies)
    indexes = {item.disclosure.index for item in selected}

    # Rutin pay geri alımı analist filtresi tarafından elenir.
    assert indexes == {
        "1697373",  # özel durum / sözleşme
        "1697371",  # finansal rapor
        "1697369",  # kredi derecelendirmesi
        "1697368",  # genel kurul
        "1697365",  # özel durum / yatırım
    }


def test_deny_list_wins_over_keywords():
    filters = FilterConfig(
        subject_allow=("Portföy Dağılım Raporu",),
        subject_deny=("Portföy Dağılım Raporu",),
        include_keywords=("portföy",),
    )
    disclosure = Disclosure(
        index="1",
        title="FON",
        subject="Portföy Dağılım Raporu",
        summary="Günlük portföy dağılımı",
        publish_date="11.11.2026 10:00:00",
    )
    assert evaluate(disclosure, filters) is None


def test_company_filter_is_hard_filter():
    config = load_config(ROOT / "config.yaml")
    disclosures = fetch_from_file(ROOT / "tests/fixtures/light_sample.json")
    selected = select_candidates(disclosures, config.filters, ("ASELSAN",))
    assert [item.disclosure.index for item in selected] == ["1697373"]


def test_require_keyword():
    filters = FilterConfig(
        subject_allow=("Özel Durum Açıklaması",),
        include_keywords=("temettü",),
        require_keyword=True,
    )
    disclosure = Disclosure(
        index="1",
        title="ŞİRKET",
        subject="Özel Durum Açıklaması (Genel)",
        summary="Yeni sözleşme imzalandı",
        publish_date="11.11.2026 10:00:00",
    )
    assert evaluate(disclosure, filters) is None


def test_priority_sorting():
    config = load_config(ROOT / "config.yaml")
    disclosures = fetch_from_file(ROOT / "tests/fixtures/light_sample.json")
    selected = select_candidates(disclosures, config.filters, config.companies)
    assert selected[0].priority == 0
    assert selected[0].disclosure.subject.startswith("Özel Durum")


def analyst_filters(**overrides) -> FilterConfig:
    defaults = dict(
        subject_allow=("Payların Geri Alınmasına İlişkin Bildirim",),
        analyst=AnalystConfig(
            enabled=True,
            routine_topics=("pay geri alım", "geri alım işlemleri"),
            override_keywords=("temerrüt", "borçlanma", "finansal zorluk"),
            critical_keywords=("temerrüt",),
            high_value_keywords=("borçlanma", "finansman"),
        ),
    )
    defaults.update(overrides)
    return FilterConfig(**defaults)


def test_routine_buyback_is_dropped():
    disclosure = Disclosure(
        index="1",
        title="ŞİRKET",
        subject="Payların Geri Alınmasına İlişkin Bildirim",
        summary="11.11.2026 Tarihli Pay Geri Alım İşlemleri",
        publish_date="11.11.2026 10:00:00",
    )
    assert evaluate(disclosure, analyst_filters()) is None


def test_exceptional_buyback_passes_as_high_materiality():
    disclosure = Disclosure(
        index="1",
        title="ŞİRKET",
        subject="Payların Geri Alınmasına İlişkin Bildirim",
        summary="Pay geri alım programı kapsamında borçlanma aracı ihracı",
        publish_date="11.11.2026 10:00:00",
    )
    candidate = evaluate(disclosure, analyst_filters())
    assert candidate is not None
    assert candidate.materiality == "high"


def test_routine_management_appointment_is_dropped():
    filters = FilterConfig(
        subject_allow=("Özel Durum Açıklaması",),
        analyst=AnalystConfig(
            enabled=True,
            routine_topics=("yönetim kurulu üyeliği", "atama"),
            override_keywords=("vefat", "kayyum"),
            high_value_keywords=("vefat", "kayyum"),
        ),
    )
    disclosure = Disclosure(
        index="2",
        title="ŞİRKET",
        subject="Özel Durum Açıklaması (Genel)",
        summary="Yönetim Kurulu Üyeliği Ataması",
        publish_date="11.11.2026 10:00:00",
    )
    assert evaluate(disclosure, filters) is None

    sensational = Disclosure(
        index="3",
        title="ŞİRKET",
        subject="Özel Durum Açıklaması (Genel)",
        summary="Yönetim Kurulu Üyesinin Vefatı",
        publish_date="11.11.2026 10:00:00",
    )
    candidate = evaluate(sensational, filters)
    assert candidate is not None
    assert candidate.materiality == "high"


def test_critical_rating_is_boosted():
    filters = FilterConfig(
        subject_allow=("Kredi Derecelendirmesi",),
        analyst=AnalystConfig(
            enabled=True,
            critical_keywords=("temerrüt", "not indirimi"),
            high_value_keywords=("yatırım",),
        ),
    )
    disclosure = Disclosure(
        index="4",
        title="JCR AVRASYA DERECELENDİRME A.Ş.",
        subject="Kredi Derecelendirmesi",
        summary="Kredi notu temerrüt nedeniyle D seviyesine düşürüldü",
        publish_date="11.11.2026 10:00:00",
    )
    candidate = evaluate(disclosure, filters)
    assert candidate is not None
    assert candidate.materiality == "critical"


def test_company_title_does_not_trigger_high_materiality():
    filters = FilterConfig(
        subject_allow=("Özel Durum Açıklaması",),
        analyst=AnalystConfig(
            enabled=True,
            routine_topics=("ağırlıklı ortalama fiyat",),
            high_value_keywords=("yatırım", "finansman"),
        ),
    )
    disclosure = Disclosure(
        index="5",
        title="EURO KAPİTAL YATIRIM ORTAKLIĞI A.Ş.",
        subject="Özel Durum Açıklaması (Genel)",
        summary="BİAŞ'da oluşan ağırlıklı ortalama fiyatın, pay başına net aktif değerin 2 katını aşması",
        publish_date="11.11.2026 10:00:00",
    )
    assert evaluate(disclosure, filters) is None