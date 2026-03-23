# Binance Teknik Analiz (Windows)

**Windows** için masaüstü uygulamasıdır (PyQt5 arayüzü). Binance Futures (USDT-M) piyasalarında teknik analiz, grafik, göstergeler ve backtest sunar; yerel bildirimler **plyer** ile Windows’ta çalışır.

**Uyarı:** Bu yazılım yatırım tavsiyesi değildir. Kripto işlemleri yüksek risk taşır.

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

Proje klasöründe:

```bash
python app.py
```

Uygulama açıldığında grafik ve analiz sekmelerini kullanabilirsiniz. Veriler Binance **public** API ile çekilir; ekstra API anahtarı gerekmez.

---

## Sorun giderme

- **`pip` bulunamadı:** Python’u PATH’e ekleyin veya `py -m pip install -r requirements.txt` deneyin.
- **PyQt / grafik hatası:** `pip install --upgrade PyQt5 matplotlib` ile güncelleyin (gerekirse `requirements.txt` içindeki sürümleri birlikte yükseltin).
- **Bağlantı hatası:** İnternet ve firewall ayarlarınızı kontrol edin; `fapi.binance.com` erişilebilir olmalıdır.
- **Paket uyumsuzluğu:** Farklı bir Python sürümünde sorun olursa, temiz bir `venv` oluşturup yalnızca `requirements.txt` ile kurun.
