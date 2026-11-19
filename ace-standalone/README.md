# ace-standalone

Anycubic ACE Pro'yu (1. nesil, JSON protokolü) yazıcı olmadan, bir bilgisayardan web paneliyle kullanır: kurutma başlat/durdur, kabin sıcaklığı, yuvalar, filament besle/geri sar. Yalnızca Python standart kütüphanesi gerekir (Linux).

## Çalıştırma

```bash
python3 server.py --fake          # donanımsız dene (yazılım ACE)
python3 server.py                 # gerçek ACE, port otomatik bulunur
python3 server.py --port /dev/ttyACM0 --host 0.0.0.0   # ağdan erişim (erişim anahtarı gerekir)
```

Panel: http://127.0.0.1:8090/ (komut satırı varsayılanı 8090; Rinkhals uygulamasında `http_port` varsayılanı 8890). Varsayılan dinleme adresi yalnızca bu bilgisayardır (Rinkhals uygulamasında da `bind_host` varsayılanı `127.0.0.1`). Ağdan erişmek için `--host 0.0.0.0` (Rinkhals'ta `bind_host`) verilir; bu durumda **erişim anahtarı zorunludur**: `--access-key ANAHTAR` ya da config dosyasındaki `access_key`; ikisi de yoksa sunucu rastgele bir anahtar üretir, config dosyasına yazar ve günlüğe bir kez basar. Tüm `/api/*` istekleri anahtarı `X-Access-Key` başlığıyla göndermelidir; web sayfası anahtarı bir kez sorar ve tarayıcıda hatırlar. Loopback'te anahtar istenmez (anahtar tanımlıysa yine denetlenir).

Rinkhals'ta `vanilla-klipper` etkinken ve yapılandırması ACE sürücüsünü kullanıyorken uygulama başlamayı reddeder (açılışta bu uygulama klippy'den ~15 sn önce başlar; iki süreç aynı seri portu paylaşamaz).

Testler: `python3 -m unittest -v`

## Bağlantı

ACE'nin kendi şebeke beslemesi vardır; bilgisayara yalnızca USB veri hattı gider. ACE tarafı 4 pinli Molex Micro-Fit, ACEResearch'e göre: 1 = 3,5 V (amacı bilinmiyor), 2 = GND, 3 = D-, 4 = D+. Kabloda yalnızca GND, D-, D+ bağlanır; 5 V (VBUS) bağlanmaz. Pin sırası kaynakta "test edilmedi" notuyla verilmiş, kabloyu yapmadan önce multimetreyle doğrula.

## Sınırlar

En yüksek sıcaklık ve süre panelin Ayarlar bölümünden değiştirilir (varsayılan 55 °C, 1440 dk; `max_temp` en çok 100 °C olabilir; dosya: `~/.config/ace-standalone/config.json`). Bunlar yalnızca panelin gönderdiği değeri kısıtlar.

## API

`GET /api/state`, `POST /api/dry {temp, duration[, fan_speed]}`, `/api/dry/stop`, `/api/feed`, `/api/unwind` `{index, length, speed}`, `/api/feed/stop`, `/api/unwind/stop`, `/api/feed_assist/start|stop {index}`, `/api/filament_info {index}`, `/api/config`, `/api/raw {method, params}` (herhangi bir ACE komutu; `drying`/`set_fan_speed` ve `temp`/`duration`/`fan_speed` alanları dedicated rotalarla aynı sınırlardan geçer). Config dosyası 0600 izniyle yazılır.

## Doğrulanmamış noktalar

Kod gerçek bir ACE ile denenmedi, yalnızca `fake_ace.py` ile. Bilinmeyenler:

- ACE yazılımının kendi sıcaklık üst sınırı var mı, yüksek hedefi reddediyor mu yoksa kırpıyor mu.
- USB ürün adı gerçekten "ACE" içeriyor mu (otomatik port bulma buna dayanıyor; bulamazsa `--port` ver).
- Bilgisayar bağlantısı koparsa süren kurutma devam ediyor mu.
- `drying` komutunda `fan_speed` alanının etkisi (panel varsayılan olarak göndermez).

## Kaynaklar

- Protokol: [printers-for-people/ACEResearch](https://github.com/printers-for-people/ACEResearch) (CC0)
- Çapraz kontrol: [ACEPRO Klipper sürücüsü](https://github.com/justinh-rahb/ACEPRO) (GPL-3.0; kod kopyalanmadı, CRC sonucu ve komut biçimleri karşılaştırıldı)
