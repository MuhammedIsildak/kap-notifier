import json

from src.documents import (
    DocumentFetcher,
    clean_document_text,
    extract_body_text,
    extract_table_rows,
)
from src.models import Disclosure
from src.config import DocumentsConfig
from src.state import State


def _page(body_html: str) -> str:
    return f"""
    <td colspan="2" class="taxonomy-context-value-summernote multi-language-content content-tr" style="display: table-cell;">
      <div><div><div class="text-block-value">{body_html}</div></div></div>
    </td>
    <td colspan="2" class="taxonomy-context-value-summernote multi-language-content content-en" style="display: table-cell;">
      <div><div><div class="text-block-value">English body copy.</div></div></div>
    </td>
    """


def test_extract_body_text_keeps_turkish_and_drops_english():
    page = _page(
        "<p>Şirketimizce 2026 / 2027 sezonu yurt içi pamuk alımları "
        "kapsamında 34.775.000 TL'lık pamuk alım anlaşması imzalanmıştır.</p>"
    )
    body = extract_body_text(page)
    assert "34.775.000 TL" in body
    assert "English body copy" not in body


def test_extract_body_text_decodes_json_escapes():
    page = _page(
        "<p>\\u015firketimizce \\u003cspan\\u003e34.775.000 TL\\u003c/span\\u003e "
        "s\\u00f6zle\\u015fme imzalanm\\u0131\\u015ft\\u0131r.</p>"
    )
    body = extract_body_text(page)
    assert "\\u003c" not in body
    assert "34.775.000 TL" in body
    assert "irketimizce" in body


def test_extract_body_text_drops_consolidation_marker():
    page = _page("[CONSOLIDATION_METHOD_TITLE] Gerçek açıklama metni.")
    body = extract_body_text(page)
    assert "CONSOLIDATION" not in body
    assert "Gerçek açıklama metni." in body


def test_clean_document_text_drops_script_noise():
    noisy = (
        'self.__next_f.push([1,"44:I[68651"])\n'
        "Özet Bilgi Yurt İçi Pamuk Alım Anlaşması\n"
        "34.775.000 TL'lık anlaşma imzalanmıştır."
    )
    cleaned = clean_document_text(noisy, 6000)
    assert "self.__next_f" not in cleaned
    assert "34.775.000 TL" in cleaned


def _table_row(label: str, values: list[str], row_no: int = 5) -> str:
    cells = "".join(
        f'<td class="taxonomy-context-value col-order-class-{index}">'
        f"<div><div class=\"gwt-Label\">{value}</div></div></td>"
        for index, value in enumerate(values)
    )
    return (
        f'<tr class="banks_role_210011-row-{row_no} data-input-row">'
        '<td class="taxonomy-field-name-cell"><div>x|</div></td>'
        '<td class="taxonomy-field-title"><table><tbody><tr><td>'
        f'<div class="gwt-Label multi-language-content content-tr">{label}</div>'
        '<div class="gwt-Label multi-language-content content-en">English</div>'
        "</td></tr></tbody></table></td>"
        f"{cells}</tr>"
    )


def _table_page(rows: list[str]) -> str:
    header = (
        '<td class="context-header taxonomy-dimensional-header-cell">'
        '<div class="gwt-HTML multi-language-content content-tr">'
        "Cari Dönem<br/>30.09.2026</div></td>"
    )
    return header + "".join(rows)


def test_extract_table_rows_returns_aggregate_lines():
    page = _table_page(
        [
            _table_row("VARLIKLAR TOPLAMI", ["94.271", "38.527"], 3),
            _table_row("Dönem Net Kâr veya Zararı", ["3.175", "4.867"], 9),
            _table_row("ÖZKAYNAKLAR", ["15.763", "13.578"], 51),
            _table_row("Nakit ve Nakit Benzerleri", ["15.333", "2.849"], 5),
        ]
    )
    lines = extract_table_rows(page)
    joined = "\n".join(lines)

    assert "VARLIKLAR TOPLAMI: 94.271 | 38.527" in joined
    assert "Dönem Net Kâr veya Zararı: 3.175 | 4.867" in joined
    # Anahtar kalem olmayan satır bırakılır.
    assert "Nakit ve Nakit Benzerleri" not in joined
    # Dönem başlığı korunur.
    assert "30.09.2026" in joined


def test_extract_table_rows_drops_english_label():
    page = _table_page([_table_row("VARLIKLAR TOPLAMI", ["94.271"], 3)])
    joined = "\n".join(extract_table_rows(page))
    assert "English" not in joined
    assert "VARLIKLAR TOPLAMI" in joined


def test_extract_table_rows_falls_back_when_no_keyword_matches():
    page = _table_page([_table_row("Bilinmeyen Kalem", ["1", "2"], 3)])
    joined = "\n".join(extract_table_rows(page))
    assert "Bilinmeyen Kalem" in joined


def test_extract_table_rows_captures_currency_and_scope():
    page = (
        '<table class="financial-header-table"><tr>'
        '<td class="financial-header-title">Sunum Para Birimi</td>'
        "<td>1.000.000 TL</td></tr><tr>"
        '<td class="financial-header-title">Finansal Tablo Niteliği</td>'
        "<td>Konsolide</td></tr></table>"
    ) + _table_page([_table_row("VARLIKLAR TOPLAMI", ["94.271"], 3)])
    joined = "\n".join(extract_table_rows(page))
    assert "1.000.000 TL" in joined
    assert "Konsolide" in joined


def test_body_text_includes_table_rows():
    page = _page("<p>Şirket açıklama metni.</p>") + _table_page(
        [_table_row("VARLIKLAR TOPLAMI", ["94.271"], 3)]
    )
    body = extract_body_text(page)
    assert "Şirket açıklama metni." in body
    assert "VARLIKLAR TOPLAMI: 94.271" in body


class _FakeResponse:
    def __init__(self, status_code: int, text: str = ""):
        self.status_code = status_code
        self.text = text
        self.content = text.encode("utf-8")

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


def test_fetcher_uses_body_when_no_attachment(tmp_path):
    page = _page(
        "<p>Şirketimizce 2026 / 2027 sezonu kapsamında "
        "34.775.000 TL'lık pamuk alım anlaşması imzalanmıştır.</p>"
    )
    session = _FakeSession([_FakeResponse(200, page)])
    fetcher = DocumentFetcher(DocumentsConfig(), session=session)
    disclosure = Disclosure(
        index="1",
        title="DCT TRADİNG DIŞ TİCARET A.Ş.",
        subject="Özel Durum Açıklaması (Genel)",
        summary="Yurt İçi Pamuk Alım Anlaşması İmzalanması Hakkında",
        publish_date="11.11.2026 13:33:19",
    )
    result = fetcher.fetch([disclosure])

    assert "1696917" not in result
    assert "1" in result
    assert "34.775.000 TL" in result["1"]
