# Binance Teknik Analiz (Windows)

**Windows** için masaüstü uygulamasıdır (PyQt5 arayüzü). Binance Futures (USDT-M) piyasalarında teknik analiz, grafik, göstergeler ve backtest sunar; yerel bildirimler **plyer** ile Windows’ta çalışır.

**Uyarı:** Bu yazılım yatırım tavsiyesi değildir. Kripto işlemleri yüksek risk taşır.

**Marka:** Bu proje **bağımsız** bir kişisel/portfolyo çalışmasıdır; **Binance** veya ilgili şirketlerle bağlantılı, onaylı veya sponsorlu değildir. “Binance” adı, yalnızca verinin hangi **halka açık API** üzerinden alındığını açıklamak için kullanılır. Resmi logo, ticari unvan taklidi veya “resmi uygulama” izlenimi verilmemelidir.

> **Not:** Linux/macOS’ta çalıştırmayı deneyebilirsiniz; geliştirme ve test **Windows 10/11** üzerinde yapılmıştır.

---

## İndirme

### Seçenek A — Git ile klonla

```bash
git clone https://github.com/emirseren100/Crypto-Technical-Analysis.git
cd Crypto-Technical-Analysis
```

### Seçenek B — ZIP

GitHub sayfasında **Code → Download ZIP** ile indirin, klasörü çıkarın ve terminalde o klasöre gidin (`cd Crypto-Technical-Analysis`).

---

## Kurulum

1. **Python** yüklü olsun (önerilen: **3.10 veya üzeri**, 64 bit).  
   [python.org](https://www.python.org/downloads/) — kurulumda **“Add Python to PATH”** işaretli olsun.

2. Bağımlılıkları yükleyin (proje klasöründeyken). `requirements.txt` içindeki sürümler **sabitlenmiştir** (`pip freeze` ile uyumlu); aynı ortamı yeniden kurmak için:

```bash
pip install -r requirements.txt
```

İsterseniz sanal ortam kullanın:

```bash
python -m venv venv
venv\Scripts\activate
pip install -r requirements.txt
```

---

## Çalıştırma

**Önerilen (Windows):** `calistir.bat` dosyasına çift tıklayın — klasörü doğru ayarlar ve hata olursa pencere kapanmadan mesaj gösterir.

Veya proje klasöründe terminalde:

```bash
python app.py
```

> **`app.py`’ye çift tıklayınca açılmıyorsa:** Windows genelde `.py` dosyasını düzenleyiciyle veya yanlış Python ile ilişkilendirir. `calistir.bat` kullanın veya `cmd` / PowerShell’de yukarıdaki komutu çalıştırın.

Uygulama açıldığında grafik ve analiz sekmelerini kullanabilirsiniz. Veriler Binance **public** API ile çekilir; ekstra API anahtarı gerekmez.

### TP profili (sol panel)

**Ayarlar** içinde **TP Profili** menüsü, sinyal mantığını ve stop-loss’u değiştirmeden yalnızca **TP1 / TP2 / TP3** mesafelerini (risk çarpanı) değiştirir:

| Seçenek | Anlamı |
|--------|--------|
| **Normal** | Varsayılan hedefler (mevcut davranış). |
| **Yüksek hedef (daha riskli)** | Daha uzak TP’ler; tam isabet olursa potansiyel kâr artar, hedefe ulaşma ihtimali genelde düşer. |
| **Muhafazakar (yakın TP)** | Daha yakın TP’ler; hedefe gelme ihtimali genelde artar, hedef başına kâr potansiyeli düşer. |

**Trade Setup** özetinde ve tabloda **TP profili** satırı gösterilir (analiz `indicators` ile uyumlu). **Paper Trading**, **Öneriler** ve **Backtest** (kısmi TP merdiveni) aynı **TP Profili** seçimini kullanır; seçim **QSettings** ile (`BinanceTA` / `TeknikAnaliz`, anahtar `tp_profile`) uygulama kapanınca da hatırlanır. Profili değiştirdikten sonra **Analiz Et**, önerileri **yeniden çekin** veya **Backtest**’i tekrar çalıştırın. Backtest sekmesinde yeşil satır, o anki profile göre **TP1/TP2/TP3 R çarpanlarını** gösterir; **SL (ATR x)** stop mesafesini, **Optimize** ise SL + min sinyal için grid arar (TP profili optimize sırasında sabittir).

## Lisans

Bu proje **MIT License** ile yayınlanır — detay için kökteki [`LICENSE`](LICENSE) dosyasına bakın.

---

## Sorun giderme

- **`pip` bulunamadı:** Python’u PATH’e ekleyin veya `py -m pip install -r requirements.txt` deneyin.
- **PyQt / grafik hatası:** `pip install --upgrade PyQt5 matplotlib` ile güncelleyin (gerekirse `requirements.txt` içindeki sürümleri birlikte yükseltin).
- **Bağlantı hatası:** İnternet ve firewall ayarlarınızı kontrol edin; `fapi.binance.com` erişilebilir olmalıdır.
- **Paket uyumsuzluğu:** Farklı bir Python sürümünde sorun olursa, temiz bir `venv` oluşturup yalnızca `requirements.txt` ile kurun.
