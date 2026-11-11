# KAP Notifier

KAP (Kamuyu Aydınlatma Platformu) bildirimlerini saatlik olarak tarar, seçtiğin
konulara uyanları Türkçe özetler ve e-posta olarak gönderir.

- Tüm şirketler taranır; `config.yaml` ile şirket/konu/kelime filtresi uygulanır.
- Aynı bildirim tekrar gönderilmez; içeriği değişirse "GÜNCELLENDİ" olarak gelir.
- KAP özeti her zaman korunur; LLM anahtarı varsa üstüne 2-3 cümlelik AI özeti eklenir.
- Sunucu, veritabanı ve ücretli servis gerekmez: GitHub Actions + Gmail SMTP.

## Hızlı başlangıç

```bash
cd kap-notifier
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements-dev.txt
cp .env.example .env
```

Önce e-posta göndermeden dene:

```bash
python main.py --dry-run --limit 10
```

Bu komut KAP'tan günün bildirimlerini çeker, filtreler, `out/preview_*.html`
dosyasına önizleme yazar ve terminale düz metin basar. State yazmaz, e-posta
göndermez.

Testleri çalıştır:

```bash
pytest -q
```

## Yapılandırma

Tüm filtreler `config.yaml` içinde:

- `companies`: boş = tüm şirketler. Doluysa şirket unvanında bu metinlerden
  biri geçmelidir. Örnek: `["ASELSAN", "TÜPRAŞ"]`.
- `filters.subject_allow`: gönderilecek bildirim türleri.
- `filters.subject_deny`: hiç gönderilmeyecek gürültülü türler.
- `filters.include_keywords`: konu/özet/şirket metninde aranan kelimeler.
- `filters.exclude_keywords`: biri geçerse bildirim gönderilmez.
- `filters.require_keyword`: `true` ise allowlist'e uysa bile en az bir keyword aranır.
- `filters.min_score`: 1 = tür eşleşmesi yeterli; 2+ = ek keyword/şirket eşleşmesi ister.
- `filters.priority_subjects`: e-postada üstte gösterilecek öncelikli türler.
- `filters.max_items`: tek e-postadaki maksimum bildirim.
- `quiet_hours`: varsayılan 00:00-08:00 arası e-posta gönderilmez; birikenler
  08:05'te tek e-postada gelir. Kapatmak için `enabled: false`.
- `llm`: Gemini modeli, parti boyutu ve çalışma başına limit.

Türkçe karakterler eşleştirmede sadeleştirilir: `ı/İ/i`, `ğ/g`, `ü/u`, `ş/s`,
`ö/o`, `ç/c`. Yani `temettu` yazsan da `temettü` bildirimini yakalar.

## E-posta ayarları (Gmail)

Gmail'in normal şifresi çalışmaz; **uygulama şifresi** gerekir.

1. Google Hesabı → Güvenlik → 2 Adımlı Doğrulama'yı aç.
2. Güvenlik → Uygulama şifreleri → "Posta" için yeni şifre oluştur.
3. `.env` dosyasına yaz:

```env
KAP_SMTP_HOST=smtp.gmail.com
KAP_SMTP_PORT=465
KAP_SMTP_USER=ornek@gmail.com
KAP_SMTP_PASS=xxxx xxxx xxxx xxxx
KAP_EMAIL_FROM=ornek@gmail.com
KAP_EMAIL_TO=ornek@gmail.com
```

Uygulama şifresindeki boşluklar otomatik temizlenir. Kurulumu doğrulamak için:

```bash
python main.py --send-test
```

Gmail dışında bir SMTP sağlayıcısı da kullanabilirsin; `KAP_SMTP_HOST` ve
`KAP_SMTP_PORT` değerlerini değiştirmen yeterli (465 = SSL, 587 = STARTTLS).

## AI özeti (opsiyonel)

[Google AI Studio](https://aistudio.google.com/) üzerinden ücretsiz bir Gemini
API anahtarı al ve `.env` dosyasına ekle:

```env
GEMINI_API_KEY=...
```

Anahtarı terminalde görünmeden test edip `.env` dosyasına kaydetmek için:

```bash
.venv/bin/python tools/setup_ai.py
```

Farklı bir model denemek için:

```bash
.venv/bin/python tools/setup_ai.py --model gemini-3.8-flash
```

Anahtar yoksa veya kota dolarsa e-posta yine gönderilir; sadece KAP'ın kendi
özeti görünür. LLM yalnızca filtreden geçen yeni bildirimler için çağrılır ve
özetler `state/summaries.json` içinde önbelleğe alınır.

Ücretsiz kota ve model adları zamanla değişebilir. `config.yaml` içindeki
`llm.model` değerini güncel ücretsiz modelle değiştirebilirsin.

## GitHub Actions ile saatlik çalıştırma

1. Bu klasörü bir GitHub reposuna push et (private repo önerilir).
2. Repo → Settings → Secrets and variables → Actions bölümünde şu secret'ları ekle:

| Secret | Zorunlu | Açıklama |
|---|---|---|
| `KAP_SMTP_USER` | Evet | Gmail adresin |
| `KAP_SMTP_PASS` | Evet | Uygulama şifresi |
| `KAP_EMAIL_TO` | Evet | Alıcı(lar), virgülle ayrılabilir |
| `KAP_EMAIL_FROM` | Hayır | Varsayılan: `KAP_SMTP_USER` |
| `KAP_SMTP_HOST` | Hayır | Varsayılan: `smtp.gmail.com` |
| `KAP_SMTP_PORT` | Hayır | Varsayılan: `465` |
| `GEMINI_API_KEY` | Hayır | AI özeti için |

3. Actions sekmesinden **KAP Notifier → Run workflow** ile bir kez elle çalıştır.
4. İlk çalıştırmada `state` branch'i otomatik oluşturulur. Görülen bildirimler
   burada tutulur; `main` branch'i kirlenmez.
5. Saatlik cron `.github/workflows/kap.yml` içinde: `5 * * * *` (UTC).

### GitHub Actions gerçekleri

- Cron saatleri **garantili değildir**; yoğun saatlerde 5-30 dakika gecikebilir.
  Bu yüzden "saatlik özet" olarak tasarlandı, borsa emri tetikleyicisi değil.
- Private repoda ayda 2.000 ücretsiz dakika vardır. Bu iş genelde 1 dakikanın
  altında sürer; saatlik çalışma ayda ~720 dakika eder.
- Scheduled workflow'lar 60 gün repo aktivitesi olmazsa kapanabilir. State
  branch'ine saatlik commit geldiği için bu risk normalde oluşmaz.
- Aynı anda iki çalışma başlamasın diye `concurrency` kilidi vardır.

## Nasıl çalışır?

```
GitHub Actions (saatlik)
  → GET /tr/api/disclosure/list/light  (günün tüm bildirimleri)
  → tür allow/deny + keyword skoru
  → disclosureIndex ile state karşılaştırması
  → Gemini ile toplu özet (opsiyonel)
  → HTML e-posta + Gmail SMTP
  → state branch'ine seen.json / summaries.json
```

KAP'ın bu uç noktası resmî/dokümante bir API değildir; KAP arayüzünün kullandığı
bir uç noktadır. Site değişirse uç nokta da değişebilir. Bu durumda
`src/fetch.py` içindeki `KAP_LIGHT_URL` güncellenmelidir.

## Sorun giderme

**`KAP isteği reddedildi: HTTP 403/405`**
KAP'ın WAF'ı bazı IP'leri engelleyebilir. Birkaç dakika sonra tekrar dene. GitHub
Actions IP'leri engellenirse aynı kodu lokal bir makinede veya farklı bir
zamanlayıcıda (ör. Cloudflare Worker) çalıştırabilirsin.

**`E-posta ayarları eksik`**
`KAP_SMTP_USER`, `KAP_SMTP_PASS` ve `KAP_EMAIL_TO` tanımlı olmalı. GitHub'da
secret olarak, lokalde `.env` içinde.

**`Username and Password not accepted`**
Normal Gmail şifresi değil, 2FA açıkken oluşturulan uygulama şifresi kullanılmalı.

**E-posta gelmiyor**
- Sessiz saatlerde olabilir (`quiet_hours`).
- Yeni bildirim olmayabilir; aynı bildirimler state sayesinde tekrar gönderilmez.
- Actions logunda "Gönderilecek yeni bildirim yok" satırını kontrol et.

**Gemini 429 / kota**
`llm.batch_size` ve `llm.max_items_per_run` değerlerini düşür veya
`llm.enabled: false` yap. E-posta yine çalışır.

## Maliyet

- GitHub Actions: private repo için aylık 2.000 ücretsiz dakika (bu iş için fazlasıyla yeterli).
- Gmail SMTP: ücretsiz (günlük makul gönderim limitleri içinde).
- Gemini API: ücretsiz kota yeterli; kota biterse KAP özetine düşer.
- Sunucu/veritabanı: yok.

## Notlar

- Veri kaynağı KAP'tır; bu araç yalnızca kişisel bilgilendirme amaçlıdır.
- AI özeti yatırım tavsiyesi değildir. Tam ve güncel metin için her zaman KAP
  linkini kullan.
- KAP verisini toplu şekilde yeniden yayınlamak/ticarileştirmek bu projenin
  kapsamı dışındadır.