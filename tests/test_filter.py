from pathlib import Path

from src.config import FilterConfig, load_config
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

    assert indexes == {
        "1697374",  # geri alım
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