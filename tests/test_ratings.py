import json

from src.config import RatingsConfig
from src.models import Disclosure
from src.ratings import RatingExtractor, extract_attachments, html_to_text, parse_rating
from src.state import State


def disclosure(title: str, subject: str = "Kredi Derecelendirmesi") -> Disclosure:
    return Disclosure(
        index="1697264",
        title=title,
        subject=subject,
        summary="KREDİ DERECELENDİRME BİLDİRİMİ",
        publish_date="11.11.2026 18:19:15",
    )


JCR_TEXT = """
RATING BİLDİRİM FORMU
Derecelendirme Kuruluşu  : JCR AVRASYA DERECELENDİRME A.Ş.
Derecelendirilen Kuruluş  : TMKŞ Fair Finansman İkinci Varlık Finansmanı Fonu
Tarih  : 11/11/2026

Kıdemli Tertip Uzun Vadeli Ulusal Kurum Kredi Rating Notu ‘A (tr)’ seviyesinden
‘CCC (tr)’ seviyesine, Kıdemsiz Tertip Uzun Vadeli Ulusal Kurum Kredi Rating
Notu ‘A- (tr)’ seviyesinden ‘CC (tr)’ seviyesine revize edilmiştir.

Kıdemli Tertip Kıdemsiz Tertip
Uzun Vadeli Ulusal Notu : CCC (tr) CC (tr)
Uzun Vadeli Uluslararası Yerel Para Notu : CC C
Kısa Vadeli Ulusal Notu : J5 (tr) J5 (tr)

• 89 günden fazla gecikmiş alacak havuzundaki anapara ödemeleri için garanti
mekanizması kapsamında temerrüt durumu,
• Kaynak kuruluşun hakim ortağının vefatı akabinde yönetimsel belirsizlikler,
• Fon bünyesindeki hesaba beklenen nakit akışının gerçekleştirilememesi.
"""


TURKRATING_TEXT = """
Kredi Derecelendirme Notunun Güncellenmesi

Derecelendirilen Kuruluş Dünya Varlık Yönetim A.Ş.
Derecelendirme Notu
  Uzun Vadeli Ulusal Derecelendirme Notu: TR D
  Kısa Vadeli Ulusal Derecelendirme Notu: TR D
Derecelendirme Notunun
Veriliş Tarihi 11 Kasım 2026
Nedeni  Şirketin Kredi Derecelendirme Notunun Temerrütte Alınması

Şirket tarafından ihraç edilen tahvilin kupon ödemesi gerçekleştirilememiştir.
Temerrüt hali oluşması nedeniyle kredi derecelendirme notları TR D olarak
güncellenmiştir.
"""


SAHA_TEXT = """
Kurumsal Yönetim ve Kredi Derecelendirme Hizmetleri A.Ş.

DUYURU
TMKŞ Fair Finansman İkinci Varlık Finansmanı Fonu
İhraç Kredi Derecelendirme Not Güncellemesi

11.11.2026

İtfa tarihindeki yükümlülüklerin yerine getirilememiş olması nedeniyle,
TMKŞ Fair Finansman İkinci Varlık Finansmanı Fonu’nun ilgili ihracının kredi
derecelendirme notu D (temerrüt) olarak güncellenmiştir.
"""


ROTA_TEXT = """
Fon portföyünde borçlanma aracı bulunan Dünya Varlık Yönetimi A.Ş.'ne yönelik
İstanbul Uluslararası Derecelendirme Hizmetleri A.Ş. (Turkrating) tarafından
yapılan değerlendirme sonucu; Şirket'in uzun vadeli ulusal derecelendirme notu
"TR D" seviyesine revize edilmiş ve derecelendirme notu statüsü "Askıda" olarak
belirlenmiştir.
"""


def test_parse_jcr_downgrade():
    rating = parse_rating(
        JCR_TEXT,
        disclosure("JCR AVRASYA DERECELENDİRME A.Ş."),
        source="TMKŞ Fair Finansman İkinci Varlık Finansmanı Fonu.pdf",
    )
    assert rating.agency == "JCR Avrasya"
    assert rating.entity == "TMKŞ Fair Finansman İkinci Varlık Finansmanı Fonu"
    assert rating.previous == "A (tr)"
    assert rating.current == "CCC (tr)"
    assert rating.action == "Not indirimi"
    assert rating.outlook == "—"
    assert any("Kısa vadeli ulusal" in item for item in rating.details)
    assert len(rating.red_flags) >= 2
    assert "CCC (tr)" in rating.display_note


def test_parse_turkrating_default():
    rating = parse_rating(
        TURKRATING_TEXT,
        disclosure("İSTANBUL ULUSLARARASI DERECELENDİRME HİZMETLERİ A.Ş."),
    )
    assert rating.agency == "Turkrating"
    assert rating.entity == "Dünya Varlık Yönetim A.Ş."
    assert rating.current == "TR D"
    assert rating.action == "Temerrüt"
    assert rating.outlook == "Temerrüt"


def test_parse_saha_default():
    rating = parse_rating(
        SAHA_TEXT,
        disclosure("SAHA KURUMSAL YÖNETİM VE KREDİ DERECELENDİRME HİZMETLERİ A.Ş."),
        source="Fair Finansman Duyuru.pdf",
    )
    assert rating.agency == "Saha Rating"
    assert rating.entity == "TMKŞ Fair Finansman İkinci Varlık Finansmanı Fonu"
    assert rating.current == "D (temerrüt)"
    assert rating.action == "Temerrüt"


def test_parse_inline_investor_notification():
    rating = parse_rating(
        ROTA_TEXT,
        disclosure(
            "ROTA PORTFÖY ŞEKERBANK PARA PİYASASI (TL) FONU",
            subject="Varlık veya İhraççı Derecelendirme Notu",
        ),
    )
    assert rating.agency == "Turkrating"
    assert rating.entity == "ROTA PORTFÖY ŞEKERBANK PARA PİYASASI (TL) FONU"
    assert rating.current == "TR D"
    assert rating.outlook == "Temerrüt (Askıda)"
    assert rating.action == "Temerrüt"


def test_parse_jcr_double_colon_table_and_stable_outlook():
    text = """
    Derecelendirilen Kuruluş : ERBOSAN ERCİYAS BORU SANAYİ VE TİCARET ANONİM ŞİRKETİ
    Uzun Vadeli Ulusal Kurum Kredi Rating Notu 'A- (tr)' seviyesinden
    'BBB+ (tr)' seviyesine revize edilmiş olup, not görünümü 'Durağan' olarak korunmuştur.
    Uzun Vadeli Ulusal Kurum Kredi Rating Notu: : BBB+  (tr) / (Durağan)
    Kısa Vadeli Ulusal Kurum Kredi Rating Notu: : J2  (tr) / (Durağan)
    """
    rating = parse_rating(
        text,
        disclosure("JCR AVRASYA DERECELENDİRME A.Ş."),
        source="ERBOSAN.pdf",
    )
    assert rating.entity == "ERBOSAN ERCİYAS BORU SANAYİ VE TİCARET ANONİM ŞİRKETİ"
    assert rating.previous == "A- (tr)"
    assert rating.current == "BBB+ (tr)"
    assert rating.action == "Not indirimi"
    assert rating.outlook == "Stabil"
    assert any("Kısa vadeli ulusal" in item for item in rating.details)


def test_fallback_summary_mentions_entity_and_notes():
    rating = parse_rating(JCR_TEXT, disclosure("JCR AVRASYA DERECELENDİRME A.Ş."))
    text = rating.fallback_summary()
    assert "TMKŞ Fair" in text
    assert "A (tr)" in text
    assert "CCC (tr)" in text
    assert text.endswith(".")
    assert "..;" not in text
    assert ",;" not in text


def test_red_flags_clean_punctuation_and_skip_table_dump():
    noisy = """
    İstanbul, 11.11.2026
    Kredi Derecelendirme Notunun Güncellenmesi
    Derecelendirilen Kuruluş Dünya Varlık Yönetim A.Ş.
    Derecelendirme Notu
    Uzun Vadeli Ulusal Derecelendirme Notu: TR D
    Kısa Vadeli Ulusal Derecelendirme Notu: TR D
    Şirket tarafından ihraç edilen tahvilin kupon ödemesi gerçekleştirilememiştir.
    """
    rating = parse_rating(noisy, disclosure("İSTANBUL ULUSLARARASI DERECELENDİRME HİZMETLERİ A.Ş."))
    assert rating.red_flags
    for flag in rating.red_flags:
        assert not flag.endswith(",")
        assert not flag.endswith(";")
        assert "Derecelendirilen Kuruluş" not in flag
        assert "Ulusal Derecelendirme Notu" not in flag
    assert any("kupon ödemesi" in flag for flag in rating.red_flags)


def test_extract_attachments_from_rsc_payload():
    attachments = [
        {
            "objId": "4028328da17e515f01a1cae0fb2307b2",
            "fileName": "Ek.pdf",
            "fileExtension": "pdf",
        }
    ]
    escaped = json.dumps(attachments).replace('"', '\\"')
    page = f'<script>self.__next_f.push([1,"x\\"attachments\\":{escaped}"])</script>'
    result = extract_attachments(page)
    assert result == attachments


def test_extract_attachments_falls_back_to_links():
    page = (
        '<a href="https://www.kap.org.tr/tr/api/file/download/'
        '4028328da17e515f01a1cae0fb2307b2">Ek.pdf</a>'
    )
    result = extract_attachments(page)
    assert result[0]["objId"] == "4028328da17e515f01a1cae0fb2307b2"
    assert result[0]["fileName"] == "Ek.pdf"


def test_html_to_text_strips_scripts():
    page = "<script>gizli</script><div>Görünür <b>metin</b></div>"
    assert "Gizli" not in html_to_text(page)
    assert "Görünür metin" in html_to_text(page)


class _FakeResponse:
    def __init__(self, status_code: int, text: str = "", content: bytes = b""):
        self.status_code = status_code
        self.text = text
        self.content = content

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


class _FakeSession:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = 0

    def get(self, *_args, **_kwargs):
        self.calls += 1
        return self.responses.pop(0)


def test_extractor_retries_on_429(tmp_path, monkeypatch):
    monkeypatch.setattr("src.ratings.time.sleep", lambda _seconds: None)
    session = _FakeSession(
        [_FakeResponse(429), _FakeResponse(200, text="<html>ok</html>")]
    )
    extractor = RatingExtractor(RatingsConfig(), State(tmp_path), session=session)
    assert extractor._fetch("https://example.test") == "<html>ok</html>"
    assert session.calls == 2