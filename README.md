# open-kobra

**Anycubic Kobra 3 + ACE Pro için Rinkhals eklenti paketi.** Yazıcıya USB bellekle kurulur; SSH, bilgisayarda kurulum aracı veya kablo gerekmez.

*English summary at the bottom.*

> ⚠️ **Deneysel yazılım.** Yalnızca tek bir Kobra 3'te test edildi. Yazıcınızda kalıcı değişiklik yapar (geri alma paketi var). Hiçbir garanti verilmez; kullanım riski size aittir.

## Ne yapar?

| Bileşen | Ne işe yarar |
|---|---|
| **kobra-screen** | Yazıcının dokunmatik ekranı için yeni arayüz (Türkçe / İngilizce). Mainline Klipper + Moonraker ile çalışır: baskı, sıcaklık, eksenler, dosyalar, ACE Pro yuvaları ve kurutma, Wi-Fi, sihirbazlar (Z-offset ölçümü, nozul temizleme). Kurulumdan sonra **kapalı** gelir. |
| **ACE Pro sürücüsü** | Klipper için ACE Pro sürücüsü: filament yükleme/boşaltma, kurutma, RFID, dolaşma koruması. Bağlantı kopmalarına ve kurutma hatalarına karşı düzeltmeler içerir. |
| **LeviQ3** | Stok yazılımdaki otomatik Z-offset (nozul teması ile) ve tabla seviyeleme komutlarının mainline Klipper için yeniden yazılmış hali. |
| **numpy** | Klipper'ın rezonans ölçümü (`SHAPER_CALIBRATE`) için gereken numpy, yazıcının işlemcisi için derlenmiş olarak gelir. |
| **Açılış bekçisi** | Rinkhals veya Klipper güncellemesi LeviQ3 dosyalarını silerse açılışta geri yükler; yükleyemezse Klipper'ın yine de açılmasını sağlar. |
| **ace-standalone** | Klipper ACE sürücüsü kullanılmıyorken yalnızca ACE Pro'yu (kurutma, filament) yönetmek için küçük bir web paneli. Kapalı gelir. |

Paket Anycubic'in stok dosyalarına yazmaz; yalnızca Rinkhals'ın kullanıcı alanına ve vanilla-klipper uygulamasının içine dosya koyar.

## Gereksinimler

**Gerekli:**

- **Anycubic Kobra 3** (veya Kobra 3 Combo). Kurulum betiği model numarasını denetler; başka modelde hiçbir şeye dokunmadan durur.
- **Rinkhals** kurulu olmalı: <https://github.com/jbatonnet/Rinkhals>
- Rinkhals'ın **vanilla-klipper** uygulaması (ACE destekli sürüm) kurulu olmalı.
- Rinkhals'ın **Moonraker** uygulaması (`40-moonraker`). Rinkhals ile birlikte gelir.
- **FAT32** biçimli, **MBR** bölümlü bir USB bellek (GPT çalışmaz).
- Yazıcıda en az 10 MB boş alan.

**Test edilen sürümler:** Kobra 3 firmware 2.4.6.7, Rinkhals 20261101_01, vanilla-klipper 0.3, ACE Pro (1. nesil) firmware V1.3.863.

**İsteğe bağlı:**

- **ACE Pro.** ACE Pro yoksa paketin geri kalanı yine çalışır. ACE Pro 2. nesil test edilmedi.

**Gerekli değil:** SSH, ADB, Docker, bilgisayara program kurmak, internet bağlantısı (kurulum sırasında).

## Kurulum (USB)

1. [Releases](../../releases) sayfasından **`open-kobra-<sürüm>-install.zip`** dosyasını indirin.
2. Zip'i USB belleğin **köküne** açın. USB'de şu dosya olmalı: `aGVscF9zb3Nf/update.swu`
3. Yazıcı açıkken USB belleği takın.
   - **1 bip:** kurulum başladı.
   - **2. bip:** kurulum bitti.
   - **3 kısa bip:** hata oluştu. O çalışmadaki değişiklikler otomatik geri alınır. Ayrıntı USB'deki `aGVscF9zb3Nf/open-kobra-install.log` dosyasındadır.
4. USB belleği çıkarın ve **yazıcıyı yeniden başlatın.**
5. Yeni ekran arayüzünü kullanmak için: yazıcı ekranında **Settings → Rinkhals → Manage apps** menüsünde `kobra-screen` kutusunu işaretleyip yeniden başlatın.

Güncelleme için yeni sürümün zip'iyle aynı adımları izleyin. Ayarlarınız korunur.

> Rinkhals'ı veya vanilla-klipper uygulamasını güncelledikten sonra paketi bir kez daha kurun; bu güncellemeler Klipper'ın eklenti klasörünü sıfırlar. Açılış bekçisi LeviQ3'ü geri yükler, ama ACE sürücüsü ve numpy için yeniden kurulum gerekir.

## Kaldırma

1. USB bellekteki eski `aGVscF9zb3Nf` klasörünü silin.
2. **`open-kobra-<sürüm>-uninstall.zip`** dosyasını USB'nin köküne açın, yazıcıya takın ve 2. bipi bekleyin.

Kaldırma, `printer.klipper.cfg` dosyanızı silmez. Z-offset, tabla haritası ve PID gibi kayıtlı ayarlar kalır.

## Sorun olursa

- **Yazıcı açılmıyor veya takılıyor:** USB belleğin köküne `.disable-rinkhals` adlı boş bir dosya koyup yazıcıyı yeniden başlatın. Stok Anycubic yazılımı açılır. Sorunu giderdikten sonra `.enable-rinkhals` dosyasıyla Rinkhals'ı geri açabilirsiniz.
- **Ekran arayüzü açılmıyor:** `kobra-screen` art arda çökerse kendini kapatır ve stok ekranı geri getirir. Ekrandan **Ayarlar → Stok moda dön** ile de stok moda dönebilirsiniz.
- Ayrıntılı kurulum ve kurtarma notları: [open-kobra/README.md](open-kobra/README.md)

## Kaynaktan derleme

Bir Linux bilgisayarda `sh`, `tar`, `gzip`, `zip`, `md5sum` ve `python3` yeterli:

```sh
cd open-kobra
./build.sh
```

Çıktı `open-kobra/dist/install/aGVscF9zb3Nf/update.swu` dosyasıdır; bu klasörü USB'nin köküne kopyalayın.

Testler:

```sh
cd open-kobra && ./test.sh
cd kobra-screen && python3 -m unittest discover -p 'test_*.py'   # Pillow gerekir
cd ace-driver && python3 -m unittest discover -s tests
cd leviq3-klipper && python3 -m unittest test_leviq3
```

Arayüzdeki metin sığma testleri yazıcının yazı tipini (`AlibabaSans-Regular.ttf`) ister. Bu dosya lisansı belirsiz olduğu için depoda yok; Rinkhals'tan alıp `kobra-screen/fonts/` altına koyarsanız bu testler de çalışır, yoksa atlanır.

## Klasörler

| Klasör | İçerik |
|---|---|
| `open-kobra/` | USB kurulum paketi: derleme betiği, kurulum/kaldırma betikleri, açılış bekçisi, testler |
| `kobra-screen/` | Dokunmatik ekran arayüzü |
| `ace-driver/` | Klipper ACE Pro sürücüsü |
| `leviq3-klipper/` | LeviQ3 Klipper eklentisi |
| `ace-standalone/` | Bağımsız ACE Pro web paneli |
| `tools/ace-fw/` | İleri düzey: ACE Pro kurutucu soğutma süresi yaması (ayrıntı içinde; SSH gerektirir) |
| `tools/numpy-uclibc/` | Paketteki numpy'ın nasıl derlendiği |

## Öneri, hata bildirimi, güvenlik

- **Hata bildirimi ve öneriler:** [Issues](../../issues) sayfasından yeni kayıt açın. Fikirler ve sorular için [Discussions](../../discussions) da açık.
- **Güvenlik açığı:** herkese açık kayıt açmayın; [SECURITY.md](SECURITY.md) dosyasındaki yolu izleyin.
- Katkı: [CONTRIBUTING.md](CONTRIBUTING.md)

Kayıt açarken günlük dosyalarındaki Wi-Fi adınızı, IP adreslerinizi ve seri numaranızı silin.

## Lisans

GPLv3, bkz. [LICENSE](LICENSE). Üçüncü taraf bileşenler: [NOTICE.md](NOTICE.md).

Bu proje Anycubic ile bağlantılı değildir. "Anycubic", "Kobra" ve "ACE Pro" sahiplerinin markalarıdır.

---

## English summary

**open-kobra** is a Rinkhals add-on package for the **Anycubic Kobra 3 (+ ACE Pro)**, installed from a USB stick (no SSH needed). It adds a new touchscreen UI for mainline Klipper/Moonraker (`kobra-screen`), an improved Klipper ACE Pro driver, a LeviQ3 (auto Z-offset) port for mainline Klipper, numpy for resonance measurement, and a boot guard. **Experimental, tested on a single printer, no warranty.**

**Requirements:** Kobra 3, [Rinkhals](https://github.com/jbatonnet/Rinkhals), Rinkhals' vanilla-klipper app (ACE-enabled build) and Moonraker app, a FAT32/MBR USB stick. Tested on firmware 2.4.6.7, Rinkhals 20261101_01, vanilla-klipper 0.3, ACE Pro Gen1 V1.3.863.

**Install:** download `open-kobra-<version>-install.zip` from Releases, unzip it to the USB root (`aGVscF9zb3Nf/update.swu`), plug it into the printer, wait for the second beep (three short beeps = error, changes rolled back, see `aGVscF9zb3Nf/open-kobra-install.log`), then reboot. Enable `kobra-screen` under *Settings → Rinkhals → Manage apps*. Uninstall the same way with the uninstall zip.

**Bugs and ideas:** Issues / Discussions. **Security issues:** see [SECURITY.md](SECURITY.md). **License:** GPLv3.
