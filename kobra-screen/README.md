# kobra-screen

Anycubic Kobra 3 (272×480 dikey dokunmatik ekran) ve ACE Pro için Moonraker
tabanlı dokunmatik arayüz. Yazıcı üzerinde Rinkhals uygulama sözleşmesine
(`app.sh` + `app.json`) uygun bir uygulama olarak çalışır; geliştirme
bilgisayarında ise aynı ekran ağacı Pillow ile bire bir PNG önizlemelerine ve
birim testlerine dönüştürülür.

## Çalışma Zamanı Mimarisi (Kobra 3 / Rinkhals)

- **Yorumlayıcı ve LVGL bağlayıcısı**: Rinkhals'ın yerleşik CPython 3.11
  yorumlayıcısı (`/useremain/rinkhals/.current/usr/bin/python3`) ve Rinkhals
  arayüzünün kullandığı CFFI tabanlı LVGL 9 bağlayıcısı
  (`/useremain/rinkhals/.current/opt/rinkhals/ui/lvgl/__init__.py` +
  `lvgl-arm-linux-uclibc.so`) tek süreçte (`python3 -m kobra_screen`) çalışır.
- **Ekran ve dokunmatik**: `/dev/fb0` (`lv.DISPLAY_ROTATION._270`, 130 DPI) ve
  `/dev/input/event0` (`lv.evdev_grab_device`, kalibrasyon `(25, 235, 460, 25)`).
  Arka ışık (`/sys/class/backlight/backlight/brightness`) dokunmayla otomatik
  uyandırılır.
- **Açık Mod ve Ekran Sahipliği (`K3SysUi` ve `rinkhals-ui.py` uyumu)**:
  - `kobra-screen` **Açık Mod** içinde (`vanilla-klipper` + `kobra-screen`) çalışır; başlarken stok `K3SysUi` süreci `SIGTERM`/`SIGKILL` ile tamamen durdurulur (`SIGSTOP` kullanılmaz).
  - Çalışırken `rinkhals-ui.py` başlatılırsa dokunmatik kilidi (`evdev`) ve çizim döngüsü geçici olarak bırakılır; `rinkhals-ui.py` kapandığında `K3SysUi` durdurulup ekran geri alınır.
  - `app.sh` gözcüsü ve `kobra_screen/network.py`, `K3SysUi` durduktan sonra `wlan0`, `wpa_supplicant` ve `udhcpc` bağlantısını ayakta tutar; bağlantı koparsa otomatik yeniden bağlanır (`10-hostname-dns` / `dhcp_watchdog` ile çakışmadan).
  - `kobra-screen` başlatılamazsa veya üst üste çökerse gözcü hem `kobra-screen` hem `vanilla-klipper` uygulamalarını devre dışı bırakıp `K3SysUi`'yi geri başlatarak sonraki açılışta **Stok Mod**'u garanti eder.
  - **Ayar → Stok moda dön** (veya bağlantı yok ekranındaki **Stok moda dön**) onaylıdır: hem `kobra-screen` hem `vanilla-klipper` uygulamalarını devre dışı bırakıp yazıcıyı **Stok Mod**'a (`gklib` + `K3SysUi`) yeniden başlatır.

## Paket Yapısı

| Dosya | Görev |
|---|---|
| `kobra_screen/moonraker.py` | `urllib.request` tabanlı Moonraker HTTP istemcisi |
| `kobra_screen/network.py` | `wpa_supplicant`/`wpa_cli`/`udhcpc` Wi-Fi yönetimi, `.bak` yedeği ve kopma gözcüsü |
| `kobra_screen/touch_cal.py` | Dokunmatik kalibrasyon matematiği, ham `/dev/input/event0` okuyucu ve `.config` kaydı |
| `kobra_screen/i18n.py`, `kobra_screen/lang_en.py` | Türkçe/İngilizce çeviri katmanı, kalıcı `language` ayarı ve dil dosyası keşfi |
| `kobra_screen/app.py` | Ana uygulama cephesi (`App`, gezinme, çerçeve, `GOTO_FILE` ve eski sembollerin yeniden dışa aktarımı) |
| `kobra_screen/app_models.py` | Ortak sabitler (`POLL_OBJECTS`, vb.), veri sınıfları (`Keypad`, `Confirm`, `Keyboard`, `TouchCalibration`, `State`) ve `disable_rinkhals_app` |
| `kobra_screen/app_settings.py`, `app_spools.py`, `app_polling.py`, `app_ace.py`, `app_system.py`, `gcode_safety.py`, `app_touch_cal.py` | `App` mixin'leri: ayar/güç/olay/bip, bakım/güncelleme/makara, Moonraker yoklama/önbellek, ACE Pro durumu, sınır/sistem/Wi-Fi, G-code güvenlik kilitleri ve dokunmatik kalibrasyon |
| `kobra_screen/screens.py` | 5 ana sekme (`TABS`), alt sayfalar (`PAGES`) cephesi ve eski sembollerin yeniden dışa aktarımı |
| `kobra_screen/screens_common.py`, `screens_ace.py`, `screens_ace_detail.py`, `screens_home.py`, `screens_files.py`, `screens_tools.py`, `screens_settings.py`, `screens_modals.py` | Ekran modülleri: ortak sabit/yardımcılar, ACE/kurutma/makara, yuva/ACE ayarları, ana/kontrol/nesne/sıcaklık, dosya/geçmiş/tabla haritası, araçlar/sınırlar/olay/bakım/güncelleme, ayar/sistem/Wi-Fi/kalibrasyon ve modal diyaloglar |
| `kobra_screen/limits.py` | `open_kobra_limits.cfg` ayrıştırıcı/yazıcı ve güvenlik doğrulaması |
| `kobra_screen/wizards.py` | Adım adım sihirbazlar (cold pull, yükle/çıkar, nozul temizle, LeviQ, Z-offset, PID, shaper) |
| `kobra_screen/widgets.py` | 272×480 yerleşim motoru ve pencere öğeleri (`Row`, `Col`, `Card`, `Button`, `Label`, `Bar`, `Ring`, `Picture`) |
| `kobra_screen/render_lvgl.py` | Yazıcı üzerinde LVGL 9 (`lvgl-arm-linux-uclibc.so`) çizici, `LvglScreen` ve donma gözcüsü (eski içe aktarma yollarını yeniden dışa aktarır) |
| `kobra_screen/lvgl_sys.py` | `render_lvgl.py` yardımcıları: dokunma/`QT_QPA_PLATFORM` ayrıştırma, `/proc` PID taraması, arka ışık, `_WATCHDOG_SH`, evdev paket ayrıştırma |
| `kobra_screen/lvgl_widgets.py` | `_LvglWidgetsMixin`: LVGL widget oluşturma/güncelleme, mesh yüzeyi ve grafik çizimi |
| `kobra_screen/touch_diag.py` | `touch-diag` / `touch-raw` tanılama komutları (`python3 -m kobra_screen.render_lvgl touch-diag`) |
| `kobra_screen/render_pil.py` | Bilgisayarda Pillow ile PNG önizleme çizicisi (yazıcıda yüklenmez) |
| `app.sh`, `app.json` | Rinkhals uygulama yaşam döngüsü (`status`, `start`, `stop`, `trial`, `disable`, `version`), çökme gözcüsü ve ayar şeması |
| `fake_moonraker.py` | Sahte Klipper + ACE Pro + LeviQ3 Moonraker sunucusu ve sahte Wi-Fi yöneticisi |
| `test_screen.py`, `test_ace.py`, `test_touch.py`, `test_wifi.py`, `test_i18n.py` | HTTP, yerleşim (TR/EN sığma), i18n, Wi-Fi/mod geçişi ve sıkı LVGL 9 sözleşmesi (`StrictFakeLvgl`) birim testleri |

## Bilgisayarda Çalıştırma ve Test

```sh
# Birim testleri (sahte Moonraker + sıkı LVGL 9 bağlayıcı doğrulaması)
python3 -m unittest -v

# Tüm ekranların Türkçe ve İngilizce PNG önizlemelerini preview/ dizinine üret
python3 make_preview.py

# Sahte Moonraker ile tek bir ekranı PNG olarak kaydet (--lang en ile İngilizce)
python3 fake_moonraker.py &
python3 -m kobra_screen --port 7125 --preview out.png --tab filament --lang en
```

## Yazıcıya Kurulum (Rinkhals)

Uygulama klasörünü yazıcıda `/useremain/home/rinkhals/apps/kobra-screen/` altına
kopyalayın ve etkinleştirin:

```sh
mkdir -p /useremain/home/rinkhals/apps/kobra-screen
# kobra_screen/, app.sh, app.json dosyalarını kopyalayın
chmod +x /useremain/home/rinkhals/apps/kobra-screen/app.sh
touch /useremain/home/rinkhals/apps/kobra-screen/.enabled
/useremain/home/rinkhals/apps/kobra-screen/app.sh start
```

Moonraker adresi, portu ve arayüz dili (`language`: `"tr"` / `"en"`) Rinkhals uygulama ayarlarından (`app.json`), dokunmatik arayüzdeki **Ayar → Dil / Language** ekranından veya `/useremain/home/rinkhals/apps/kobra-screen.config` dosyasından değiştirilebilir.

## Yeni Dil Ekleme

`kobra_screen/lang_<kod>.py` (ör. `lang_de.py`) dosyası içinde `CODE`, `NAME`, `STRINGS` (Türkçe kaynak anahtar -> çeviri sözlüğü) ve isteğe bağlı `ERROR_PATTERNS` tanımlanır. `kobra_screen/i18n.py` başlangıçta `lang_*.py` modüllerini otomatik keşfeder; yeni dil **Dil / Language** listesinde görünür ve `open-kobra/build.sh` tarafından otomatik paketlenir.

## Değişiklikler (Özet)

- **ERR-15 ACE Etiketi**: Olay geçmişinde `source="ace"` olayları "ACE" olarak etiketlendi.
- **ERR-20 ace-driver Sürümü**: Güncellemeler sayfasına `ace-driver` sürüm satırı eklendi.
- **Kurutma Önayarları ve Kırpma**: PETG, ABS, ASA, PA, PC ve TPU kurutma önayarları ile sınır kırpma mantığı eklendi.
- **Ön Isıtma ASA/TPU**: Kontrol ısıtma ekranına ASA ve TPU ön ısıtma önayarları eklendi.
- **ERR-22 Ana Ekran ve Baskı Kartı Ayrıntıları**: Ana ekran ve baskı kartında `z_offset` yanında sıfır olmayan canlı Z ince ayarı (`homing_origin[2]`), baskı sırasında dokunulabilir sıcaklık şeridi (`Kontrol > Isı`), aktif ACE yuvası renk/ad göstergesi ve `extrusion_lock` açıkken ana ekran `Yazdır` düğmesinde kilit simgesi/alt metni eklendi.
- **ERR-13 ACE Dolaşma Eşiği ve Canlı Hızlar**: ACE Özellik sayfasına `ACE_TANGLE_PROTECTION THRESHOLD=<sn>` (2–60 sn keypad) ayarı eklendi; `ace_instance_0.feed_speed`/`retract_speed` alanları bildirildiğinde öncelikli gösterildi.
- **ERR-25 Grafik Artımlı Güncelleme**: `LvglScreen` `Chart` güncellemesi yalnızca değişen eksen aralığı ve değişen seri noktalarını yazacak şekilde optimize edildi.
- **ERR-14 Klipper `action:prompt_*` Diyalogları**: `kobra_screen/prompts.py` ayrıştırıcısı ve modal bileşeni eklendi; `gcode_store` üzerinden gelen istemler `prompt_show` ile açılır, düğmeler `app.gcode()` üzerinden gönderilir (ekstrüzyon kilidi açıkken ekstrüzyon düğmeleri kilitli/pasif kalır), `prompt_end` diyaloğu kapatır.
- **Kurutma Önayarları Sadeleştirme**: `DRY_PRESETS` 5 malzemeye (`PLA`, `PETG`, `ABS/ASA`, `PA/PC`, `TPU`) sadeleştirildi; Makara kurutma kartında `PLA`, `PETG` ve tüm 5 önayara ulaşan `Daha fazla` (`dry_presets` sayfası) düğmeleri kullanıldı (`max_dryer_temperature` kırpması korundu).
- **Kurutma Önayarları Sadeleştirme (`ui-f`)**: `DRY_PRESETS` 5 malzemeye sadeleştirildi (`PLA`, `PETG`, `ABS/ASA`, `PA/PC`, `TPU`); Makara (Spools) kurutma kartı `PLA`, `PETG` ve tüm 5 önayarı açan `Daha fazla` (`dry_presets`) sayfası ile güncellendi, `max_dryer_temperature` sınır kontrolü/kırpması korundu.
- **Klipper Diyaloğu Cihaz Düzeltmeleri (`ui-g`, C1/C2)**: Açılışta ilk `gcode_store` okumasındaki eski `action:prompt_*` satırları görülmüş sayılır; `Kapat`/`Close` diyaloğu `prompt_begin` zaman damgasıyla kalıcı kapatır (yeni `prompt_begin` gelmedikçe tekrar açılmaz); boşta `gcode_store` yoklaması `count=20` ile en fazla 2 sn aralıkla yapılır.
- **Ekstrüzyon Kilidi ve Yapılandırma Koruması (REV-02, REV-03, REV-04)**: Duraklatılmış baskıda `Devam` (`App.resume_print`) düğmesi `extrusion_lock` denetimine bağlandı; `App.script_extrudes` içine `START_PRINT`, `RESUME`, `M23`/`M24`, `T0`–`T9`, `G00`/`G01`/`G2`/`G3`/`G11`, `N<satır>` ön eki, boşluksuz `G1 X10E5` ve `FORCE_MOVE STEPPER=extruder` eklendi; `/tmp/rinkhals/apps/<app>.config` dosyasındaki `extrusion_lock` anahtarının varsayılan veya kalıcı ayarı ezmesi engellendi (`limits.load_config`, `App._load_persistent_settings`).
- **Çekirdek Kilit, Ağ, Dil ve Takip Düzeltmeleri (Paket 3: REV-09, REV-18, REV-21, REV-24, REV-25)**: `Moonraker` acil/kısa kontrol istekleri (`emergency_stop`, `machine_reboot`, `firmware_restart`, `print_pause`, `print_cancel`) ve uzun `gcode()` çağrıları `_lock`/`_conn` dışındaki izole bağlantılara ayrıldı; `i18n.load_language()` kalıcı `.config` dosyasını `/tmp/rinkhals/apps/<app>.config` öncesinde okuyacak şekilde düzeltildi; `network.WifiManager.ensure_connected()` ASCII dışı SSID'lerde `decode_wpa_ssid` ile DHCP yeniler ve başarısız ağ seçiminde `enable_network all` + `reconnect` ile kayıtlı ağlara döner (`_ESCAPED_HEX_RE` ve `bool(wpa_pids) or True` temizlendi); `tracker.PrintStatsTracker` baskı ortasında başlatıldığında başlangıç `filament_used` değerini baz alarak önceki filamenti tekrar düşmez ve `load_event_log_state` açık kaydedilmiş `watermark` değerini yerel uyarılarla ezmez.
- **Davranış Değiştirmeyen Bölme (`r3`)**: `kobra_screen/render_lvgl.py` (2330 satır) üç modüle bölündü: `lvgl_sys.py` (yapılandırma/PID/arka ışık/evdev yardımcıları), `lvgl_widgets.py` (`_LvglWidgetsMixin` widget çizimi) ve `touch_diag.py` (`touch-diag`/`touch-raw`). `LvglScreen` mixin'i devralır, `render_lvgl` tüm eski adları yeniden dışa aktarır; `render_lvgl.lv` tek bağlayıcı olarak kalır ve testlerin onu değiştirmesi alt modüllere de yansır. Testler değişmedi (308/308 geçer).
- **Kurutma Sonrası Soğutma Satırı (`E1`)**: `ace_instance_0.dryer_status.status` alanının `"drying"` durumundan `"stop"` (veya `"drying"` dışı herhangi bir değere) geçişi `app.py` durum yoklama yolunda (`_update_dryer_cooling_locked`) yakalanarak tek saat kaynağı (`app._clock`) ile `_dryer_stopped_ts` zaman damgası kaydedilir; `screens.py` aynı saat kaynağını kullanarak 10 dakika boyunca `"ACE fanı ısıtıcıyı soğutuyor"` bilgi satırını gösterir, yeni kurutma başladığında temizler ve açılışta zaten `"stop"` ise göstermez (`test_dryer_cooling.py`).
- **Davranış Değiştirmeyen Bölme (`r1`)**: `kobra_screen/app.py` (3171 satır) 9 modüle bölündü: `app_models.py` (sabitler, `disable_rinkhals_app`, `Keypad`, `Confirm`, `Keyboard`, `TouchCalibration`, `State`), `app_settings.py` (`_AppSettingsMixin`), `app_spools.py` (`_AppSpoolsMixin`), `app_polling.py` (`_AppPollingMixin`), `app_ace.py` (`_AppAceMixin`), `app_system.py` (`_AppSystemMixin`), `gcode_safety.py` (`_AppGcodeMixin`), `app_touch_cal.py` (`_AppTouchCalMixin`) ve cephe `app.py` (`App` + `GOTO_FILE` + eski adların yeniden dışa aktarımı).
- **Davranış Değiştirmeyen Bölme (`r2`)**: `kobra_screen/screens.py` (3014 satır) 9 modüle bölündü: `screens_common.py` (175), `screens_ace.py` (518), `screens_ace_detail.py` (395), `screens_home.py` (547), `screens_files.py` (291), `screens_tools.py` (421), `screens_settings.py` (409), `screens_modals.py` (289) ve cephe `screens.py` (51 satır; `TABS`, `PAGES` ve tüm eski sembollerin yeniden dışa aktarımı).


