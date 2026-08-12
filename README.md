# Barnes & Noble Book Scraper

Barnes & Noble'ın herkese açık **Bestselling Books** sayfasından güncel kitap verisi toplar ve her çalıştırmada sonuçları CSV ile JSON olarak yeniler.

Toplanan alanlar: başlık, yazar, fiyat, varsa eski fiyat ve indirim, puan, yorum sayısı, format, ISBN-13, stok durumu, satış sıralaması, kategori, ürün ve görsel URL'leri ile toplama zamanı.

> Site sayfa yapısını değiştirebilir. Scraper erişim kontrolü, CAPTCHA veya giriş mekanizmalarını aşmaya çalışmaz.

## Kurulum (Windows / VS Code)

Proje klasöründe PowerShell açın.

Mevcut bir `venv` klasörünüz varsa onu kullanın:

```powershell
.\venv\Scripts\Activate.ps1
```

Yoksa oluşturun ve etkinleştirin:

```powershell
python -m venv venv
.\venv\Scripts\Activate.ps1
```

Ardından paketleri yükleyin:

```powershell
python -m pip install -r requirements.txt
```

PowerShell aktivasyon politikasına takılırsa, aktivasyon yapmadan da şu komutlarla çalışabilirsiniz:

```powershell
.\venv\Scripts\python.exe -m pip install -r requirements.txt
.\venv\Scripts\python.exe main.py
```

## Çalıştırma

```powershell
python main.py
```

Scraper terminalde sayfa ilerlemesini gösterir. Tamamlandığında güncel dosyalar şurada oluşur:

- `exports/books.csv` — Excel için UTF-8 BOM ile yazılır.
- `exports/books.json` — Girintili, geçerli JSON dizisidir.

Bu dosyalar her çalıştırmada **üzerine yazılır**; geçmiş veya veritabanı tutulmaz.

## Ayarlar

`scraper.py` dosyasının üst kısmındaki ayarlar tek noktadadır:

```python
BASE_URL = "https://www.barnesandnoble.com"
START_URL = f"{BASE_URL}/collections/books/bestselling-books"
TIMEOUT = 30.0
REQUEST_DELAY = 0.7
MAX_PAGES = 5
```

- `MAX_PAGES = 1`: yalnızca ilk liste sayfası.
- `MAX_PAGES = 5`: en fazla ilk beş sayfa.
- `MAX_PAGES = None`: yeni sayfa bulunmayana kadar devam eder.
- `REQUEST_DELAY`: siteye saygılı istek aralığıdır; düşürmeyin.

## Alanlar

- `title`: Kitabın ürün başlığı.
- `author`: Detail sayfasında görünen ana yazar/katkıda bulunan kişi.
- `current_price`: Seçili ISBN/format için güncel satış fiyatı (`float`).
- `original_price`: Ana ürün fiyat kutusunda gerçekten gösterilen, güncel fiyattan yüksek eski liste fiyatı; yoksa `null`.
- `discount_percent`: Eski fiyat doğrulanmışsa `(original_price - current_price) / original_price * 100`; yoksa `null`.
- `rating` ve `review_count`: Sayfadaki schema.org aggregate rating verisi; sırasıyla `float` ve `integer`, yoksa `null`.
- `format`: Seçili ISBN'nin formatı (örneğin `Hardcover` veya `Paperback`).
- `isbn`: ISBN-13/EAN ürün kimliği.
- `availability`: Ürünün çevrimiçi satın alma durumu. Sayfadaki açık stok metni ile `Add To Cart` / `Pre-Order` gibi gerçek satın alma aksiyonu önceliklidir; bulunamazsa JSON-LD değeri kullanılır.
- `bestseller_rank`: Liste yanıtında güvenilir bir satış sırası bulunursa sayı; aksi halde `null`.
- `category`: Detail sayfasındaki kategori bağlantıları, ` | ` ile birleştirilir.
- `product_url` ve `image_url`: Canonical ürün ve kapak görseli URL'leri.
- `scraped_at`: Tüm çalışma için bir kez üretilen UTC ISO-8601 toplama zamanı.

## Bilinen sınırlamalar

- Yalnızca herkese açık sayfalar okunur; CAPTCHA, giriş, hesap veya erişim kontrolü aşılmaz.
- B&N fiyat, stok, puan ve katalog bilgisini anlık değiştirebilir. Eksik veya güvenilir olmayan alanlar tahmin edilmez, `null` kalır.
- `original_price` yalnızca aynı ürünün ana fiyat kutusundan alınır; önerilen ürünlerin fiyatları hiç kullanılmaz.
- `MAX_PAGES = None` seçeneği yeni/benzersiz kitap kalmadığında durur; yine de günlük kullanımda sayısal bir sınır daha öngörülebilirdir.

## Site yapısı değişirse

Detay sayfası CSS seçicileri `scraper.py` içindeki `SELECTORS` sözlüğünde merkezi olarak tutulur. Başlık, yazar, fiyat, format veya kategori kaybolursa önce buradaki değerleri güncelleyin.

Liste sayfası güncel B&N altyapısında normal ürün kartı HTML'i yerine React Router veri akışı gönderdiği için liste kimlikleri `RSC_PRODUCT_PATTERN` ve `extract_listing_seeds()` ile alınır. Liste hiç ürün bulamazsa değişmesi en muhtemel bölüm burasıdır.

## Bağımlılıklar

- `httpx`: bağlantıları yeniden kullanan, timeout/redirect/retry kontrolü yapılan HTTP istekleri.
- `selectolax`: ürün detay HTML'i içindeki JSON-LD ve sayfa öğelerini hızlı CSS seçicileriyle ayrıştırmak için.

Playwright kullanılmaz. Gerekli ürün detay verileri normal HTTP ile gelen HTML'de zaten bulunduğundan tarayıcı açmak gereksizdir.
