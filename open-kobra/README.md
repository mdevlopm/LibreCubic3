# open-kobra — Kobra 3 USB Kurulum Paketi

Anycubic Kobra 3 (+ ACE Pro) üzerinde **Rinkhals** + **vanilla-klipper** + **Moonraker** ortamı için `kobra-screen`, `ace-standalone`, `leviq3` ve açılış güvenlik bekçisi (`05-open-kobra-guard`) bileşenlerini tek bir Rinkhals SWU paketi olarak derler, kurar ve kaldırır.

> **Önemli:** Bu paket Anycubic'in stok sistem bölümlerine veya ikili dosyalarına (`/userdata/app/gk/K3SysUi`, `gklib`, `gkapi`, stok `printer.cfg`) **kesinlikle yazmaz**. Yalnızca `/useremain/home/rinkhals/` kullanıcı dizinine ve `vanilla-klipper` uygulamasının `klippy/extras` + `printer_data/config` alanına yazar.

---

## Paket İçeriği

| Bileşen | Hedef Konum | Açıklama |
|---|---|---|
| `kobra-screen` | `/useremain/home/rinkhals/apps/kobra-screen` | Mainline Klipper + Moonraker için dokunmatik ekran arayüzü; yazı tipleri Rinkhals `opt/rinkhals/ui/assets/` içinden kullanılır, pakete kopyalanmaz (varsayılan: **kapalı**) |
| `ace-standalone` | `/useremain/home/rinkhals/apps/ace-standalone` | Yazıcısız ACE Pro kurutma ve filament web paneli, Rinkhals uygulamasında port `8890`, yalnızca yazıcı üzerinde (`127.0.0.1`; ağ erişimi için `bind_host` + erişim anahtarı) (varsayılan: **kapalı**) |
| `05-open-kobra-guard` | `/useremain/home/rinkhals/apps/05-open-kobra-guard` | `vanilla-klipper` öncesi çalışan hafif açılış bekçisi; `leviq3.py` veya `open_kobra_homing.cfg` silinirse/bozulursa kalıcı kopyadan geri yükler, yükleyemezse ilgili `[include ...]` satırını geçici kapatır (UI/ekran/port açmaz; varsayılan: **etkin**) |
| `leviq3.py` | `<vanilla-klipper>/klippy/extras/leviq3.py` | Klipper LeviQ3 eklentisi (`probe` / `probe_ks1` ve isteğe bağlı `cs1237` ile çalışır) |
| Kalıcı LeviQ3 kopyası | `/useremain/home/rinkhals/open-kobra/leviq3.py`, `leviq3.cfg`, `restore-leviq3.sh` | `vanilla-klipper` veya Rinkhals güncellendiğinde geri yükleme kaynağı |
| `leviq3.cfg` | `/useremain/home/rinkhals/printer_data/config/leviq3.cfg` | Kobra 3 varsayılan `[leviq3]` yapılandırması (`state_file`: `/useremain/home/rinkhals/printer_data/config/leviq3_state.json`) |
| `open_kobra_homing.cfg` | `/useremain/home/rinkhals/printer_data/config/open_kobra_homing.cfg` | Sensorless XY homing koruması: zaten home edilmiş eksende yeniden sürüş engeli, homing öncesi/sonrası geri çekilme, stall-clear beklemesi ve TMC akım koruması (`[safe_z_home]` ile uyumlu `[gcode_macro G28]`) |
| `open_kobra_limits.cfg` | `/useremain/home/rinkhals/printer_data/config/open_kobra_limits.cfg` | `kobra-screen` tarafından yönetilen kullanıcı sınırları (başlangıçta boş; yeniden kurulumda korunur) |
| `open_kobra_limits_zz_ekstruzyon_yasak.cfg` | `/useremain/home/rinkhals/printer_data/config/open_kobra_limits_zz_ekstruzyon_yasak.cfg` | **Artık oluşturulmaz.** Eski kurulumlarda `[extruder] min_extrude_temp: 320` güvenlik varsayılanı vardı; yükseltmede şablon dosyası silinir, kullanıcı düzenlemesi olan dosya korunur. |
| Include bloğu | `/useremain/home/rinkhals/printer_data/config/printer.klipper.cfg` | Yalnızca `vanilla-klipper` tarafından okunan `printer.klipper.cfg` içindeki `SAVE_CONFIG` bloğunun hemen üstüne `[include leviq3.cfg]`, `[include open_kobra_homing.cfg]` ve `[include open_kobra_limits*.cfg]` eklenir |
| Sürüm işareti | `/useremain/home/rinkhals/.open-kobra-version` | Kurulu paket sürümü (`VERSION` dosyasından) |

---

## Gereksinimler (Ön Koşullar)

Kurulum betiği yazıcıda çalışmadan önce aşağıdaki koşulları denetler; herhangi biri sağlanmazsa **hiçbir dosyaya dokunmadan** hata vererek çıkar:

1. **Rinkhals kurulu olmalı** (`/useremain/rinkhals/.version` ve çalışma dizini mevcut).
2. **Yazıcı modeli Anycubic Kobra 3 olmalı** (`/userdata/app/gk/config/api.cfg` içinde tırnaklı `"modelId": "20024"` veya sayısal `"modelId": 20024`).
3. **`vanilla-klipper` uygulaması kurulu olmalı** (`klippy/extras` ve `klippy/klippy.py` mevcut).
4. **Moonraker uygulaması (`40-moonraker`) kurulu olmalı**.
5. **`/useremain` üzerinde yeterli boş alan** (en az 10 MB) bulunmalı.

---

## `leviq3` Kalıcılığı ve Açılış Bekçisi (`05-open-kobra-guard`) Sınırları

Mainline Klipper (`klippy.py`) eklentileri yalnızca kendi `klippy/extras/` dizininden yükler. `vanilla-klipper` başlangıçta bu dizini çalışma anında kurmaz (`klippy.sh` doğrudan `<APP_ROOT>/klippy` ile başlar); bu nedenle:
- `vanilla-klipper` güncellendiğinde `apps/vanilla-klipper/klippy/extras/leviq3.py` silinir,
- `printer_data/config/` altındaki `printer.klipper.cfg`, `leviq3.cfg`, `open_kobra_limits.cfg` ve `leviq3_state.json` ise bind-mount üzerinden korunur.

Eğer `leviq3.py` yokken `printer.klipper.cfg` içinde `[include leviq3.cfg]` etkin kalırsa Klipper açılışta `Unknown config object 'leviq3'` hatasıyla durur. Bunu önlemek için `05-open-kobra-guard` kurulur:
- Rinkhals (`tools.sh:list_apps`) uygulamaları `sort -uV` ile sıralar ve `start.sh` bu sırayla başlatır; `05-open-kobra-guard` adı gereği `vanilla-klipper`'dan önce çalışır (`vanilla-klipper` ayrıca `./start.sh` altından 15 sn gecikmeli başlar).
- Açılışta `<vanilla-klipper>/klippy/extras/leviq3.py` yoksa veya paketteki beklenen MD5 özeti (`leviq3.py.md5`) ile eşleşmiyorsa kalıcı `/useremain/home/rinkhals/open-kobra/leviq3.py` kopyasından atomik olarak geri yükler ve gerekirse `[include leviq3.cfg]` ile `open_kobra_limits.cfg` içindeki `[leviq3]` bölümünü yeniden açar.
- Geri yükleme başarısız olursa (ör. kalıcı kopya yoksa, özeti bozuksa veya dizin yazılabilir değilse) `printer.klipper.cfg` içindeki `[include leviq3.cfg]` satırını `# [include leviq3.cfg] # open-kobra/guard-disabled` şeklinde, `open_kobra_limits.cfg` içindeki `[leviq3]` bölümünü de `# open-kobra/guard-disabled-leviq3 ...` şeklinde yorumlayarak Klipper'ın sağlam açılmasını sağlar.
- Bekçi kendi içinde zaman aşımına uğrarsa veya hata alırsa günlük yazar ve `0` çıkış koduyla çıkarak Rinkhals başlangıcını asla bloklamaz.

**Tasarımın Sınırları:**
1. Yazıcıyı yeniden başlatmadan `vanilla-klipper` paketini güncelleyip Manage Apps menüsünden doğrudan `vanilla-klipper`'ı başlatırsanız bekçi henüz yeniden çalışmamış olur; bu durumda `sh /useremain/home/rinkhals/open-kobra/restore-leviq3.sh` çalıştırın veya yazıcıyı yeniden başlatın.
2. Kullanıcı **Manage apps** üzerinden `05-open-kobra-guard` uygulamasını bilinçli olarak kapatırsa açılışta otomatik koruma çalışmaz.
3. `[leviq3]` bölümünü `[include leviq3.cfg]` yerine doğrudan `printer.klipper.cfg` içine elle yazarsanız bekçi bu bölümü otomatik devre dışı bırakamaz.

---

## Derleme ve Test (Bilgisayarda)

Docker gerekmez; standart Linux araçları (`sh`, `tar`, `gzip`, `zip`, `md5sum`, `python3`) yeterlidir.

```sh
./build.sh
```

Üretilen dosyalar:
- Kurulum paketi: `dist/install/aGVscF9zb3Nf/update.swu`
- Kaldırma paketi: `dist/uninstall/aGVscF9zb3Nf/update.swu`

Test paketini çalıştırmak için:

```sh
./test.sh
```

---

## USB Bellek Hazırlama ve Kurulum

1. Bir USB belleği **MBR** bölümleme tablosuyla **FAT32** olarak biçimlendirin (GPT desteklenmez).
2. `dist/install/aGVscF9zb3Nf` klasörünü olduğu gibi USB belleğin kök dizinine kopyalayın (USB'de `aGVscF9zb3Nf/update.swu` dosyası bulunmalı).
3. USB belleği yazıcıya takın:
   - İlk bip sesi yazıcının `update.swu` paketini algılayıp işlemi başlattığını gösterir.
   - İşlem tamamlandığında **ikinci bir bip** duyulur.
   - **3 kısa/ardışık bip** duyulursa ön koşul veya kurulum hatası oluşmuştur; ayrıntılar için USB bellekteki `aGVscF9zb3Nf/open-kobra-install.log` dosyasını inceleyin. Hata durumunda o çalışmadaki değişiklikler otomatik olarak geri alınır.
4. USB belleği çıkarın.
5. **Klipper'ı yenileme:** Kurulum sırasında `vanilla-klipper` zaten çalışıyorsa yeni `leviq3.py` eklentisinin ve `[include ...]` satırlarının yüklenmesi için Mainsail/Fluidd üzerinden **`FIRMWARE_RESTART`** uygulayın veya yazıcıyı yeniden başlatın.

> Her kurulum çalışmasında tarihli yedek `/useremain/bak/open-kobra-<tarih>/` altına alınır ve en fazla son 2 tarihli yedek tutulur. `ace-standalone/config.json`, `open_kobra_limits.cfg`, `open_kobra_limits_zz_ekstruzyon_yasak.cfg`, `*.config` (`kobra-screen.config`) ve uygulama açık/kapalı işaretçileri (`.enabled`/`.disabled` ve üst seviye `<uygulama>.enabled`/`.disabled`) yeniden kurulumlarda (ve `*.config` dosyaları kaldırmada da) korunur. Temiz kurulumda `kobra-screen.config` boş `{}` olarak oluşturulur (extrusion_lock varsayılan kapalı).

### Paket 5 Değişiklik Notları (2026-11-18)
- **REV-04 (güncellendi):** Ekstrüzyon güvenlik kilidi artık **varsayılan olarak kapalı**. Temiz kurulumda `open_kobra_limits_zz_ekstruzyon_yasak.cfg` ve `kobra-screen.config` içine `extrusion_lock=true` **oluşturulmaz**. Eski kurulumlardaki şablon dosyası (`open-kobra varsayilan guvenlik kilidi` yorumu + `min_extrude_temp: 320`) yükseltmede silinir; kullanıcı düzenlemesi olan dosya korunur. Kilit isteyenler ekrandan veya `kobra-screen.config`'te `extrusion_lock: true` / ace-driver config'te `toolhead_extrusion_allowed: False` yaparak etkinleştirebilir.
- **REV-27 / REV-30:** `install/update.sh` tüm `.py` derleme denetimini tek Python sürecinde yapar; `rescue_prev` (`stock/`, `start.sh.orig*`, `binary_repair.disabled`) ve `open-kobra-targets.list` yükseltme rollback'inde geri yüklenir; `start.sh` kapısında mevcut `start.sh.orig` sağlamlığı da doğrulanır; `uninstall/update.sh` `*.config` (`kobra-screen.config`) dosyalarını silmez; `rescue.sh` POSIX dışı `-nt/-ot` yerine `stat`/`ls`+`find -newer` kullanır; `05-open-kobra-guard/app.sh` `open_kobra_homing.cfg` içindeki `#`/`;` yorum satırlarını yok sayar; `update.sh` / `uninstall/update.sh` içindeki `find -maxdepth`/`-path` kabuk glob döngülerine çevrildi.
- **REV-26:** `kobra-screen/app.sh` içinde `restart_stock_ui` çağrısı `STOP_MARKER`/`is_self_disabled` ve tekil çökme yeniden başlatma yolundan çıkarıldı; `start`/`stop` supervisor `sleep 1` penceresindeki yarış kapatıldı; `app.json` varsayılanı `touch_swap_axes="false"` `.config` geçersiz kılması olmadan ortama aktarılmaz.

---

## Uygulamaları Etkinleştirme

Kullanıcı onayı olmadan stok arayüzün veya USB seri bağlantının devre dışı kalmaması için `kobra-screen` ve `ace-standalone` **kurulu ama kapalı** durumda başlar.

Etkinleştirmek için:
1. Yazıcının dokunmatik ekranında **Ayarlar (Settings) → Rinkhals → Manage apps** menüsüne girin.
2. Kullanmak istediğiniz uygulamanın (`kobra-screen` veya `ace-standalone`) yanındaki onay kutusunu işaretleyin:
   - **`kobra-screen`**: `vanilla-klipper` ve `40-moonraker` etkin olmalıdır. Rinkhals açılışta `start.sh` içinde stok `K3SysUi` arayüzünü başlattığından, `kobra-screen` stok `K3SysUi` ile aynı anda ekran ve dokunmatik paylaşımı yapmamalıdır (`K3SysUi` veya `rinkhals-monitor` ile çakışma durumu `kobra-screen` paketi içinde yönetilmelidir).
   - **`ace-standalone`**: Yazıcı olmadan yalnızca ACE Pro'yu yönetmek içindir; web paneline varsayılan olarak yalnızca yazıcının kendisinden (`http://127.0.0.1:8890/`) erişilir; ağdan erişmek için `bind_host` değerini `0.0.0.0` yapın (erişim anahtarı zorunlu hale gelir, günlükte/`config.json` içinde görünür). `ace-standalone/app.json` içindeki `serial_port` ve `bind_host` alanları `"enum"` tipinde olduğundan Rinkhals dokunmatik arayüzünden (`rinkhals-ui.py`) seçilebilir; `http_port` ve `max_temp` alanları ise `"number"` tipinde olduğundan dokunmatik arayüzde düzenleme düğmesi devre dışı görünür, bu değerleri değiştirmek için `/useremain/home/rinkhals/apps/ace-standalone.config` (veya `apps/ace-standalone/config.json`) dosyasını düzenleyin.

---

## Kaldırma

1. USB bellekteki eski `aGVscF9zb3Nf` klasörünü silin.
2. `dist/uninstall/aGVscF9zb3Nf` klasörünü USB belleğin kök dizinine kopyalayın (`aGVscF9zb3Nf/update.swu`).
3. USB belleği yazıcıya takın ve ikinci bip sesini bekleyin.
4. Kaldırma betiği önce kaldırma öncesi güvenlik yedeği (`open-kobra-pre-uninstall-<tarih>`, `ace-standalone/config.json` ve `*.config` dosyaları dahil) alır, çalışan `open-kobra` uygulamalarını durdurur, **ilk olarak `printer.klipper.cfg` içindeki `# open-kobra/begin ... # open-kobra/end` bloğunu çıkarır**, ardından `leviq3.py`, `leviq3.cfg`, `leviq3_state.json`, `open_kobra_homing.cfg`, `open_kobra_limits.cfg`, `/useremain/home/rinkhals/open-kobra`, `.open-kobra-version` dosyalarını ve `kobra-screen`, `ace-standalone`, `05-open-kobra-guard` uygulamalarını kaldırır. **`printer.klipper.cfg` dosyasını silmez ve eski yedekle ezmez**; kurulum sonrasında yaptığınız `SAVE_CONFIG` (Z-offset, bed mesh, PID) ve elle yaptığınız yapılandırma değişikliklerini korur. Başarılı kurulum/kaldırma sonunda USB bellekteki `aGVscF9zb3Nf/update.swu` dosyası temizlenir; günlükler `aGVscF9zb3Nf/open-kobra-uninstall.log` dosyasına yazılır.

---

## Sorun Olursa Geri Dönüş (Kurtarma)

Rinkhals veya bir uygulama yüzünden yazıcı açılışta takılırsa ya da stok yazılıma dönmek isterseniz:

- **Rinkhals'ı geçici olarak devre dışı bırakma (`.disable-rinkhals`):**
  - FAT32 USB belleğin kök dizinine `.disable-rinkhals` adlı boş bir dosya koyup yazıcıyı yeniden başlatın.
  - Rinkhals açılışta bu dosyayı görürse başlatmayı durdurur ve stok Anycubic yazılımı açılır (yedek erişim olarak `adbd` başlatılır).
  - Ayrıca Rinkhals açılışı kendi içinde başarısız olursa otomatik olarak `/useremain/rinkhals/.disable-rinkhals` dosyasını bırakır; sonraki açılışta stok yazılıma dönersiniz.
- **Rinkhals'ı yeniden etkinleştirme (`.enable-rinkhals`):**
  - Sorunu giderdikten sonra USB belleğin kök dizinine `.enable-rinkhals` adlı boş bir dosya koyup yeniden başlatarak Rinkhals'ı tekrar başlatabilirsiniz.
- **SSH / ADB ile müdahale:**
  - Rinkhals çalışırken SSH veya ADB (`5555` portu) üzerinden bağlanıp `/useremain/home/rinkhals/apps/<uygulama>/.disabled` dosyası oluşturarak sorunlu uygulamayı kapatabilirsiniz.
- **Yapılandırma sıfırlama veya Rinkhals kaldırma:**
  - Rinkhals dokunmatik menüsü veya Rinkhals'ın `config-reset` / `rinkhals-uninstall` SWU araçları ile tüm Rinkhals yapılandırması sıfırlanabilir.
