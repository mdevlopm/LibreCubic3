"""English translation catalog for kobra-screen."""

CODE = "en"
NAME = "English"

STRINGS = {
    # Tabs & page titles
    "Ana": "Home",
    "Kontrol": "Control",
    "Makara": "Spools",
    "Araçlar": "Tools",
    "Ayar": "Settings",
    "Ekran kalibrasyonu": "Touch calibration",
    "Dosyalar": "Files",
    "Baskı": "Print",
    "Baskı ayarı": "Print tune",
    "Yuva": "Slot",
    "Yuva ayrıntısı": "Slot details",
    "ACE Pro": "ACE Pro",
    "Sınırlar": "Limits",
    "Wi-Fi": "Wi-Fi",
    "Wi-Fi ağı": "Wi-Fi network",
    "Dil / Language": "Dil / Language",
    "Baskı geçmişi": "Print history",
    "Baskı ayrıntısı": "Print details",
    "Nesneler": "Objects",
    "Ekran": "Display",

    # Header & status chips
    "DUR": "STOP",
    "Acil durdurma gönderildi": "Emergency stop sent",
    "Acil durdurma yapılsın mı?\n\nYazıcı hemen durur, ısıtıcılar kapanır.": (
        "Emergency stop?\n\nPrinter stops immediately, heaters turn off."
    ),
    "Acil durdurma yapılsın mı?\n\nYazıcı hemen durur, ısıtıcılar kapanır. Devam etmek için Klipper yeniden başlatılmalıdır.": (
        "Emergency stop?\n\nPrinter stops immediately, heaters turn off. Klipper must be restarted to continue."
    ),
    "Evet, durdur": "Yes, stop",
    "Bağlantı yok": "Offline",
    "Yazdırılıyor": "Printing",
    "Duraklatıldı": "Paused",
    "Hata": "Error",
    "Tamamlandı": "Complete",
    "İptal edildi": "Cancelled",
    "Hazır": "Ready",

    # Duration formats
    "%dsa": "%dh",
    "%dsa %02ddk": "%dh %02dm",
    "%ddk": "%dm",
    "%dsn": "%ds",

    # Offline & mode switching
    "Kobra Screen ve Vanilla Klipper devre dışı bırakılıp yazıcı stok moda yeniden başlatılsın mı?": (
        "Disable Kobra Screen and Vanilla Klipper and reboot into stock mode?"
    ),
    "Stok moda dön": "Return to stock",
    "Yeniden başlat": "Restart",
    "Klipper yeniden başlatıldı": "Klipper restarted",
    "Sınırları sıfırla ve başlat": "Reset limits & restart",
    "Özel sınırlar (open_kobra_limits.cfg) temizlenip Klipper yeniden başlatılsın mı?": (
        "Clear custom limits (open_kobra_limits.cfg) and restart Klipper?"
    ),
    "Baskı sırasında stok moda geçilemez": "Cannot switch to stock mode while printing",
    "Stok moda geçiliyor, yeniden başlatılıyor...": "Switching to stock mode, rebooting...",

    # Home tab
    "Nozul": "Nozzle",
    "Tabla": "Bed",
    "Hedef %.0f°C": "Target %.0f°C",
    "Kapalı": "Off",
    "ACE Pro bağlı değil": "ACE Pro not connected",
    "aktif: T%d": "active: T%d",
    "Kalan %s": "%s left",
    "Geçen %s": "%s elapsed",
    "Durdur": "Stop",
    "Baskı iptal edilsin mi?": "Cancel the current print?",
    "Duraklat": "Pause",
    "Devam": "Resume",
    "Baskı tamamlandı": "Print complete",
    "Baskı iptal edildi": "Print cancelled",
    "Baskı hatası": "Print error",
    "PLA ön ısıtma başladı": "PLA preheat started",
    "Seviyele": "Level",
    "Ön ısıt": "Preheat",
    "Yazdır": "Print",

    # Control tab
    "Isı": "Heat",
    "Eksen": "Move",
    "Ek": "Extra",
    "kapalı": "off",
    "Gir": "Set",
    "%s (en çok %d°C)": "%s (max %d°C)",
    "Kapat": "Off",
    "Hepsini kapat": "Turn all off",
    "Motor kapat": "Motors off",
    "Parça fanı": "Part fan",
    "Işık": "Lights",
    "Ekstruder": "Extruder",
    "nozul soğuk": "nozzle cold",
    "Geri 10": "Back 10",
    "İleri 10": "Feed 10",

    # Filament / ACE Pro tab & slot pages
    "ACE": "ACE",
    "ace-driver": "ace-driver",
    "ACE bağlı değil": "ACE not connected",
    "ACE ayarları": "ACE settings",
    "event_source_ace": "ACE",
    "event_source_gcode": "G-code",
    "event_source_klipper": "Klipper",
    "event_source_print": "Print",
    "event_source_notify": "Notice",
    "Kurutma": "Drying",
    "Maksimum kurutma sıcaklığı": "Max dryer temp",
    "Kurutma sıcaklığı": "Dryer temp",
    "Kurutma fanı": "Dryer fan",
    "Kurutucu fanı": "Dryer fan",
    "Kurutma fan hızı": "Dryer fan speed",
    "Dolaşma eşiği": "Tangle threshold",
    "Dolaşma eşiği (sn)": "Tangle threshold (s)",
    "Aralık: %d–%d": "Range: %d–%d",
    "Aralık: %d-%d": "Range: %d-%d",
    "Aralık: %s": "Range: %s",
    "Sınır dışı: %s": "Out of range: %s",
    "PLA": "PLA",
    "PETG": "PETG",
    "ABS": "ABS",
    "ASA": "ASA",
    "PA": "PA",
    "PC": "PC",
    "TPU": "TPU",
    "ABS/ASA": "ABS/ASA",
    "PA/PC": "PA/PC",
    "Silicon": "Silicon",
    "Silikon": "Silicon",
    "Elle yükle": "Manual load",
    "Elle çıkar": "Manual unload",
    "Boş": "Empty",
    "Yuva %d": "Slot %d",
    "aktif: %s": "active: %s",
    "yok": "none",
    "Kurutuyor": "Drying",
    "Nem %s%%": "Hum. %s%%",
    "Kurutma kapalı": "Dryer off",
    "En çok %d°C": "Max %d°C",
    "45° 4sa": "45° 4h",
    "55° 6sa": "55° 6h",
    "Kurutma sıcaklığı (en çok %d°)": "Dryer temp (max %d°)",
    "Süre (dakika)": "Duration (minutes)",
    "Kurutma başladı": "Drying started",
    "Kurutma önayarları": "Drying presets",
    "Daha fazla": "More",
    "Özel": "Custom",
    "Kurutucu %s sonra duracak": "Dryer stopping in %s",
    "Kurutucu hatası": "Dryer error",
    "Isıtıcı plakası güvenlik için soğutuluyor": "Cooling heater plate for safety",
    "Kurutucu hatası: %s": "Dryer error: %s",
    "ACE Pro ayarları": "ACE Pro settings",
    "Yuva bilgisi yok": "No slot info",
    "boş": "empty",
    "Ayrıntı": "Details",
    "Yükle": "Load",
    "Filament yüklendi": "Filament loaded",
    "Geri çek": "Unload",
    "Filament geri çekildi": "Filament unloaded",
    "Malzeme": "Material",
    "Renk": "Color",
    "Beyaz": "White",
    "Siyah": "Black",
    "Kırmızı": "Red",
    "Turuncu": "Orange",
    "Sarı": "Yellow",
    "Yeşil": "Green",
    "Mavi": "Blue",
    "Mor": "Purple",
    "Boş işaretle": "Mark empty",

    # Slot statuses, connection states, sensors, RFID
    "hazır": "ready",
    "sürülüyor": "feeding",
    "geri çekiliyor": "unwinding",
    "geçişte": "shifting",
    "ön yükleme": "preloading",
    "RFID okunuyor": "reading RFID",
    "dişli hatası": "gear error",
    "sürücü hatası": "driver error",
    "bağlı": "connected",
    "bağlanıyor": "connecting",
    "yeniden bağlanıyor": "reconnecting",
    "başlatılıyor": "initializing",
    "devre dışı": "disabled",
    "bilinmiyor": "unknown",
    "dolu": "loaded",
    "okundu": "ok",
    "okunuyor": "reading",
    "okunamadı": "read error",
    "etiket yok": "no tag",

    # Slot detail page
    "Isı (N/T)": "Temp N/B",
    "Önerilen": "Suggested",
    "Kayıtlı ısı: %s°C": "Saved temp: %s°C",
    "Ölçü": "Size",
    "T%d · Feed assist: %s": "T%d · Feed assist: %s",
    "açık": "on",
    "Besleme uzunluğu (mm)": "Feed length (mm)",
    "%d mm sürüldü": "Fed %d mm",
    "Çekme uzunluğu (mm)": "Retract length (mm)",
    "%d mm çekildi": "Retracted %d mm",
    "Sürme hızı (mm/s)": "Feed speed (mm/s)",
    "Sürme hızı %d mm/s": "Feed speed %d mm/s",
    "Çekme hızı (mm/s)": "Retract speed (mm/s)",
    "Çekme hızı %d mm/s": "Retract speed %d mm/s",
    "Feed assist kapatıldı": "Feed assist off",
    "Feed assist açıldı": "Feed assist on",
    "Yuva %d (T%d)": "Slot %d (T%d)",
    "Manuel Sürme": "Manual Feed",
    "50 mm sür": "Feed 50 mm",
    "Özel sür": "Custom feed",
    "50 mm çek": "Retract 50",
    "Özel çek": "Custom retr.",
    "Sürme dur": "Stop feed",
    "Çekme dur": "Stop retract",
    "Sürme hızı": "Feed speed",
    "Çekme hızı": "Retr. speed",

    # ACE Pro detail pages
    "Durum": "Status",
    "Özellik": "Features",
    "İşlem": "Actions",
    "kapalı (%s)": "off (%s)",
    "Yuva %d (%s)": "Slot %d (%s)",
    "aktif · %s° · %s": "on · %s° · %s",
    "Donanım": "Hardware",
    "birim #%s": "unit #%s",
    "Model": "Model",
    "Yazılım": "Firmware",
    "Bağlantı": "Link",
    "USB port": "USB port",
    "Kabin / Fan": "Bay / Fan",
    "Sensörler": "Sensors",
    "Nozul sensörü": "Nozzle sensor",
    "RDM (ayırıcı)": "RDM (hub)",
    "Kurutucu": "Dryer",
    "Yenile": "Refresh",
    "Durum sorgulandı": "Status queried",
    "Yeniden bağlan": "Reconnect",
    "ACE yeniden bağlanıyor": "Reconnecting ACE",
    "RFID eşitleme": "RFID sync",
    "RFID eşitleme kapatıldı": "RFID sync disabled",
    "RFID eşitleme açıldı": "RFID sync enabled",
    "Sonsuz makara": "Endless spool",
    "Sonsuz makara kapatıldı": "Endless spool disabled",
    "Sonsuz makara açıldı": "Endless spool enabled",
    "Özellikler": "Features",
    "Sonsuz makara eşleşme modu": "Endless spool match mode",
    "Tam": "Exact",
    "Sıradaki": "Next",
    "Mod: %s": "Mode: %s",
    "Sürücü Ayarları": "Driver Settings",
    "Sürme / çekme hızı": "Feed / retract speed",
    "Sürme / değişim": "Feed / toolchange",
    "Dolaşma / denetim": "Tangle / watchdog",
    "ACE sınırlarını düzenle": "Edit ACE limits",
    "Tüm dolu yuvalardaki filamentler geri çekilsin mi?": "Unload filament from all occupied slots?",
    "Tüm yuvalar boşaltıldı": "All slots unloaded",
    "Aktif araç ve feed assist durumu sıfırlansın mı?": "Reset active toolhead and feed assist state?",
    "Aktif araç sıfırlandı": "Active toolhead reset",
    "Kayıtlı yuva envanteri sıfırlansın mı?": "Reset persistent slot inventory?",
    "Envanter sıfırlandı": "Inventory reset",
    "Birim İşlemleri": "Unit Operations",
    "Akıllı yükle": "Smart load",
    "Akıllı yükleme bitti": "Smart load complete",
    "Akıllı çıkar": "Smart unload",
    "Akıllı çıkarma bitti": "Smart unload complete",
    "Tümünü çıkar": "Unload all",
    "Assist kapat": "Stop assist",
    "Envanter ve Durum": "Inventory & State",
    "Kaydet": "Save",
    "Envanter kaydedildi": "Inventory saved",
    "Aracı sıfırla": "Reset tool",
    "Envanter sil": "Clear inv.",
    "Diske yaz": "Flush disk",
    "Durum diske yazıldı": "State flushed to disk",

    # Files & print tune
    "Dosya yok": "No files",
    "Yeni": "Newest",
    "Eski": "Oldest",
    "Ad": "Name",
    "Katman %s mm": "Layer %s mm",
    "Baskı başlatıldı": "Print started",
    "Hız": "Speed",
    "Sessiz": "Quiet",
    "Normal": "Normal",
    "Sport": "Sport",
    "Çılgın": "Turbo",
    "Akış": "Flow",
    "Z ince ayar": "Baby step Z",

    # History & exclude object
    "Özet": "Summary",
    "%d iş": "%d jobs",
    "Baskı süresi": "Print time",
    "Toplam filament": "Total filament",
    "Henüz baskı geçmişi yok": "No print history yet",
    "Kayıt bulunamadı": "Record not found",
    "Toplam süre": "Total time",
    "Kullanılan filament": "Filament used",
    "Dosya durumu": "File status",
    "Mevcut": "Available",
    "Tekrar yazdır": "Print again",
    "Bu dosyada nesne bilgisi yok (dilimleyicide etiketlemeyi açın)": (
        "No object info in this file (enable labeling in slicer)"
    ),
    "Baskı nesneleri": "Print objects",
    "%d nesne · %d iptal": "%d objects · %d excluded",
    "Şu an basılıyor": "Currently printing",
    "Basılıyor": "Printing",
    "%s nesnesi iptal edilsin mi?\n\nBu baskıda bu nesne bir daha basılmayacak.": (
        "Exclude object %s?\n\nThis object will not be printed for the rest of this print."
    ),
    "%s iptal edildi": "Excluded %s",
    "Evet, iptal et": "Yes, exclude",

    # Settings tab, display & language page
    "Yazıcı": "Printer",
    "Otomatik Z-offset": "Auto Z-offset",
    "Klipper'ı yeniden başlat": "Restart Klipper",
    "Klipper yeniden başlatılsın mı?": "Restart Klipper?",
    "Yazıcıyı yeniden başlat": "Reboot printer",
    "Yazıcı yeniden başlatılsın mı?": "Reboot printer?",
    "Ekranı kalibre et": "Touch calibration",
    "Geçerli dil": "Current language",
    "Dil ayarı kaydedilemedi: %s": "Could not save language setting: %s",
    "Ekran ayarı kaydedilemedi: %s": "Could not save display setting: %s",
    "Parlaklık": "Brightness",
    "Ekran uykusu": "Screen sleep",
    "1 dk": "1 min",
    "5 dk": "5 min",
    "15 dk": "15 min",
    "Baskıda uyku": "Print sleep",
    "Onay ve hata pencerelerinde ekran açık kalır.": "Screen stays on for prompts and error dialogs.",
    "Ekranı şimdi uyut": "Sleep screen now",

    # Wizards (tools tab + wizard runner)
    "Cold pull": "Cold pull",
    "Filament yükle": "Load filament",
    "Filament çıkar": "Unload filament",
    "~Filament yükle": "Load",
    "~Filament çıkar": "Unload",
    "Nozul temizle": "Clean nozzle",
    "Tabla seviyele": "Level bed",
    "Z-offset ölç": "Z-offset",
    "PID ayarı": "PID tune",
    "Titreşim ayarı": "Input shaper",

    "Hazırlık": "Prepare",
    "Isıtma": "Heating",
    "Filamenti sür": "Push filament",
    "Soğutma": "Cooling",
    "Çek": "Pull",
    "Uç kontrolü": "Inspect",
    "Çıkar": "Unload",
    "Bitti": "Done",
    "Silme": "Wipe",
    "Seviyeleme": "Leveling",
    "Isıtıcı": "Heater",
    "Ayarlama": "Tuning",
    "Ölçüm": "Measuring",
    "Nozulda en son hangi filament vardı?": "Which filament was last in the nozzle?",
    "ACE kullanıyorsan filamenti geri çektir ve boruyu ekstruder girişinden ayır.\nElinde 30 cm kadar {material} filament olsun.": (
        "If using ACE, retract the filament and detach the PTFE tube.\nHave ~30 cm of {material} filament ready."
    ),
    "Nozul {hot}°'ye ısıtılıyor.": "Heating nozzle to {hot}°.",
    "Filamenti ekstrudere tak. Nozuldan temiz akana kadar sür.": (
        "Insert filament into the extruder. Push until it flows cleanly."
    ),
    "10 mm sür": "Push 10 mm",
    "Nozul {pull}°'ye soğutuluyor. Filamente dokunma.": (
        "Cooling nozzle to {pull}°. Do not touch the filament."
    ),
    "Ekstruder motoru serbest. Varsa ekstruder kolunu aç ve filamenti tek hamlede, sıkıca yukarı çek.": (
        "Extruder motor unlocked. Open the lever if any and pull the filament firmly in one motion."
    ),
    "Çıkan uç nozulun iç şeklini almış ve temiz olmalı. Üzerinde kir varsa tekrarla.": (
        "The tip should match the nozzle interior and be clean. Repeat if residue remains."
    ),
    "Hangi filament yüklenecek?": "Which filament to load?",
    "Filamenti ekstrudere tak ve nozuldan yeni renk temiz akana kadar sür.": (
        "Insert filament and extrude until the new color flows cleanly."
    ),
    "Nozul ucunu temizle. Isıtıcı kapatıldı.": "Clean the nozzle tip. Heater turned off.",
    "Nozulda hangi filament var?": "Which filament is in the nozzle?",
    "Filament geri çekiliyor.": "Unloading filament.",
    "Filamenti ekstruderden al. Isıtıcı kapatıldı.": "Remove filament from extruder. Heater turned off.",
    "Tablanın boş olduğundan emin ol. Nozul 170°'ye ısınıp silme pedinde silinecek.": (
        "Make sure the bed is clear. Nozzle will heat to 170° and wipe on the pad."
    ),
    "Nozul 170°C'ye ısıtılıyor.": "Heating nozzle to 170°C.",
    "Nozul siliniyor.": "Wiping nozzle.",
    "Isıtıcı kapatıldı.": "Heater turned off.",
    "Tabla boş ve temiz olmalı. Nozul ve tabla ısınacak, nozul silinecek ve tabla ağı ölçülecek. Birkaç dakika sürer.": (
        "Bed must be empty and clean. Nozzle and bed will heat, wipe, and probe the mesh. Takes a few minutes."
    ),
    "Nozul ısınıp silinecek, 140°C'ye soğuyacak ve tabla 25 noktada ölçülecek.": "The nozzle is heated and wiped, cooled to 140°C, then the bed is probed at 25 points.",
    "Tabla ısınıyor: %.0f°C / %.0f°C": "Bed heating: %.0f°C / %.0f°C",
    "Isıtılıyor": "Heating",
    "Nozul siliniyor": "Wiping the nozzle",
    "Nozul soğutuluyor": "Cooling the nozzle",
    "Z ekseni sıfırlanıyor": "Homing Z",
    "Tabla ağı ölçülüyor": "Probing the bed mesh",
    "Z-offset ölçülüyor": "Measuring Z-offset",
    "Tabla ağı ölçüldü.": "Bed mesh measured.",
    "Tabla boş ve temiz, nozul ucu temiz olmalı. Nozul tablaya dokunarak sıfır noktasını ölçecek.": (
        "Bed must be clear and nozzle tip clean. Nozzle will touch the bed to probe zero."
    ),
    "Otomatik z-offset ölçülüyor.": "Probing auto Z-offset.",
    "Yeni z-offset kaydedildi.": "New Z-offset saved.",
    "Eksenler sıfırlanıyor, nozul 170°C'ye ısıtılıyor.": 'Homing axes, heating nozzle to 170°C.',
    'Tabla boş ve temiz olmalı. Tabla 60°, nozul 170° ısınacak; nozul silinip z-offset ölçülecek.': 'Bed must be empty and clean. Bed heats to 60°, nozzle to 170°; nozzle is wiped, then Z-offset is measured.',
    'Eksenler sıfırlanıyor, tabla ve nozul ısıtılıyor.': 'Homing axes, heating bed and nozzle.',
    'Nozul siliniyor ve z-offset ölçülüyor.': 'Wiping nozzle and measuring Z-offset.',
    'Yeni z-offset kaydedildi. Isıtıcılar kapatıldı.': 'New Z-offset saved. Heaters off.',
    'Bed mesh': 'Bed mesh',
    'Z ölçümü': 'Z probe',
    'Baskı iptal ediliyor': 'Cancelling print',
    'Bekle': 'Wait',
    'Klipper meşgul, iptal hemen çalışmadı.\n\nAcil durdurma yapılsın mı? Yazıcı hemen durur, ısıtıcılar kapanır.': 'Klipper is busy, the cancel did not run yet.\n\nEmergency stop? The printer stops at once and the heaters turn off.',
    'Sistem durumu': 'System status',
    'Okunuyor...': 'Reading...',
    'Yazıcı bilgisayarı': 'Printer computer',
    'İşlemci': 'CPU',
    'Bellek': 'Memory',
    'Çalışma süresi': 'Uptime',
    'İşlemci payı': 'CPU share',
    'MCU yükü': 'MCU load',
    'Ana kart': 'Main board',
    'Nozul kartı': 'Nozzle board',
    'hata': 'errors',
    'Sıcaklık: kartların yazılımı desteklemiyor': 'Temperature: not available (firmware)',
    'Sistem izleme': 'System status',
    "Tabla %.0f°C": "Bed %.0f°C",
    "Nozul %.0f°C": "Nozzle %.0f°C",
    "Nozul ısınıyor: %.0f°C / %.0f°C": "Nozzle heating: %.0f°C / %.0f°C",
    "Nozul soğuyor: %.0f°C / %.0f°C": "Nozzle cooling: %.0f°C / %.0f°C",
    "Hangi ısıtıcı ayarlansın?": "Which heater to tune?",
    "Nozul 220°": "Nozzle 220°",
    "Tabla 60°": "Bed 60°",
    "PID ayarı yapılıyor. Birkaç dakika sürer, yazıcıya dokunma.": (
        "Running PID calibration. Takes a few minutes, do not touch printer."
    ),
    "Sonuç kaydedilince Klipper yeniden başlar.": "Klipper will restart when saving results.",
    "Tabla boş olmalı. Yazıcı eksenleri titreştirerek rezonansı ölçecek; ses normaldir.": (
        "Bed must be clear. Axes will vibrate to measure resonance; noise is normal."
    ),
    "Rezonans ölçülüyor. Birkaç dakika sürer.": "Measuring resonance. Takes a few minutes.",
    "Çalışıyor...": "Running...",
    "Adım %d / %d": "Step %d / %d",
    "Yeniden dene": "Retry",
    "hedef %s": "target %s",
    "Bitir": "Finish",
    "İptal": "Cancel",
    "Tekrarla": "Repeat",
    "İleri": "Next",

    # Limits page & limits.py
    " sn": " s",
    "Nozul en çok": "Nozzle max temp",
    "Tabla en çok": "Bed max temp",
    "Soğuk ekstrüzyon alt": "Min extrude temp",
    "Tek ekstrüzyon en çok": "Max extrude dist",
    "En yüksek hız": "Max velocity",
    "En yüksek ivme": "Max accel",
    "En yüksek Z hızı": "Max Z velocity",
    "Otomatik Z kalkış": "Auto Z lift",
    "Sensör gürültü eşiği": "Noise threshold",
    "Hata eşiği": "Max error",
    "Kontrol süresi": "Gain time",
    "Tolerans (hysteresis)": "Hysteresis",
    "En az artış": "Heating gain",
    "Kurutma en çok": "Dryer max temp",
    "Gövde ısı en çok": "Body max temp",
    "Toplam sürme sınırı": "Max feed length",
    "Zaman aşımı çarpanı": "Timeout mult.",
    "Dolaşma algılama": "Tangle detection",
    "Dolaşma uzunluğu": "Tangle length",
    "Bitiş süzme sayısı": "Runout debounce",
    "Bağlantı denetimi": "Link watchdog",

    "Nozul sınırı artarsa yüksek sıcaklıkta termal veya donanım hasarı oluşabilir.": (
        "Raising nozzle max temp may cause thermal or hardware damage."
    ),
    "Tabla sınırı artarsa yatak ısıtıcısı ve tesisat aşırı ısınabilir.": (
        "Raising bed max temp may overheat the bed heater and wiring."
    ),
    "Alt sınır düşürülürse soğuk nozulla filament itilip dişli veya hotend zorlanabilir.": (
        "Lowering min extrude temp may push filament into a cold nozzle."
    ),
    "Tek seferlik ekstrüzyon sınırı büyürse denetimsiz uzun itme yapılabilir.": (
        "Raising single extrude limit allows long unguarded extrusions."
    ),
    "Hız sınırı artarsa adım kaçırma veya mekanik zorlanma oluşabilir.": (
        "Raising max velocity may cause skipped steps or mechanical strain."
    ),
    "İvme sınırı artarsa kayış ve motor yükü artar, baskı kalitesi düşebilir.": (
        "Raising max accel increases belt/motor load and may lower print quality."
    ),
    "Z hızı artarsa Z ekseni adım kaçırabilir veya tablaya çarpabilir.": (
        "Raising Z velocity may skip Z steps or crash into the bed."
    ),
    "Kalkış sınırı artarsa sensör arızasında nozul gereğinden fazla yükselir.": (
        "Raising auto lift increases Z travel on sensor fault."
    ),
    "Gürültü eşiği artarsa temas algılama hassasiyeti düşer.": (
        "Raising noise threshold reduces contact detection sensitivity."
    ),
    "Nozul hata eşiği büyürse termal kaçak daha geç algılanır.": (
        "Raising nozzle max error delays thermal runaway detection."
    ),
    "Nozul kontrol süresi uzarsa arızalı ısıtıcı geç durdurulur.": (
        "Raising nozzle gain time delays faulty heater shutdown."
    ),
    "Nozul sıcaklık bandı genişlerse ısıl sapmalar gizlenebilir.": (
        "Widening nozzle hysteresis band may mask thermal drift."
    ),
    "Nozul en az artış düşürülürse yavaş ısıtma arızası yakalanmayabilir.": (
        "Lowering nozzle heating gain may miss slow-heating faults."
    ),
    "Tabla hata eşiği büyürse termal kaçak daha geç algılanır.": (
        "Raising bed max error delays thermal runaway detection."
    ),
    "Tabla kontrol süresi uzarsa arızalı ısıtıcı geç durdurulur.": (
        "Raising bed gain time delays faulty heater shutdown."
    ),
    "Tabla sıcaklık bandı genişlerse ısıl sapmalar gizlenebilir.": (
        "Widening bed hysteresis band may mask thermal drift."
    ),
    "Tabla en az artış düşürülürse yavaş ısıtma arızası yakalanmayabilir.": (
        "Lowering bed heating gain may miss slow-heating faults."
    ),
    "Kurutma üst sınırı artarsa kurutucu ve makara aşırı ısınabilir.": (
        "Raising dryer max temp may overheat the dryer and spools."
    ),
    "ACE gövde sıcaklık sınırı artarsa aşırı ısınma daha geç durdurulur.": (
        "Raising ACE body max temp delays overheat shutdown."
    ),
    "Sürme sınırı aşırı büyürse sensöre ulaşmayan filament uzun süre itilir.": (
        "Raising feed limit pushes filament longer if sensor is not reached."
    ),
    "Çarpan artarsa takılan ACE hareketi daha geç zaman aşımına düşer.": (
        "Raising multiplier delays timeout on stalled ACE moves."
    ),
    "Dolaşma algılama kapatılırsa sıkışan filament baskıyı durdurmaz.": (
        "Disabling tangle detection stops pausing on jammed filament."
    ),
    "Dolaşma mesafesi büyürse sıkışma daha geç fark edilir.": (
        "Raising tangle length delays filament jam detection."
    ),
    "Süzme sayısı çok büyürse filament bitişi gecikmeli algılanır.": (
        "Raising debounce count delays filament runout detection."
    ),
    "Bağlantı denetimi kapatılırsa kopan USB bağlantısı baskıyı durdurmaz.": (
        "Disabling link watchdog will not pause on USB disconnect."
    ),

    "Desteklenmeyen sınır anahtarı: %s.%s": "Unsupported limit key: %s.%s",
    "%s için geçersiz mantıksal değer.": "Invalid boolean value for %s.",
    "%s için geçerli bir sayı girin.": "Enter a valid number for %s.",
    "%s en az %s olabilir.": "%s must be at least %s.",
    "%s %s değerinden büyük olmalıdır.": "%s must be greater than %s.",
    "%s tam sayı olmalıdır.": "%s must be an integer.",
    "En yüksek hız (%s), en yüksek Z hızından (%s) küçük olamaz.": (
        "Max velocity (%s) cannot be less than max Z velocity (%s)."
    ),
    "En yüksek ivme (%s), en yüksek Z ivmesinden (%s) küçük olamaz.": (
        "Max accel (%s) cannot be less than max Z accel (%s)."
    ),
    "Nozul en çok (%s°), min_temp (%s°) değerinden büyük olmalıdır.": (
        "Nozzle max (%s°) must be greater than min_temp (%s°)."
    ),
    "Soğuk ekstrüzyon alt (%s°), min_temp (%s°) değerinden küçük olamaz.": (
        "Min extrude (%s°) cannot be less than min_temp (%s°)."
    ),
    "Soğuk ekstrüzyon alt (%s°), nozul üst sınırından (%s°) büyük olamaz.": (
        "Min extrude (%s°) cannot exceed nozzle max (%s°)."
    ),
    "Tabla en çok (%s°), min_temp (%s°) değerinden büyük olmalıdır.": (
        "Bed max (%s°) must be greater than min_temp (%s°)."
    ),
    "Gövde ısı en çok (%s°), min_temp (%s°) değerinden büyük olmalıdır.": (
        "Body max (%s°) must be greater than min_temp (%s°)."
    ),

    "config dosyasından": "from config file",
    "%s ısıtıcı denetimi kapatılsın mı?\n\nKoruma eşikleri en gevşek değerlere çekilir; termal kaçak algılanmayabilir.": (
        "Disable %s heater verification?\n\nProtection limits will be relaxed; thermal runaway may not be detected."
    ),
    "Denetim durumu": "Verification",
    "Devre dışı": "Bypassed",
    "Açık": "Enabled",
    "Sıcaklık": "Temperature",
    "Hareket": "Motion",
    "Nozul ısı denetimi": "Nozzle heater verify",
    "Tabla ısı denetimi": "Bed heater verify",
    "ACE Isı ve Besleme": "ACE Temp & Feed",
    "ACE Algılama": "ACE Sensors",
    "Tüm özel sınırlar silinip varsayılanlara dönülsün mü?": (
        "Clear all custom limits and restore defaults?"
    ),
    "Değişiklikler için Klipper yeniden başlatılsın mı?": (
        "Restart Klipper to apply changes?"
    ),
    "Varsayılana dön": "Reset defaults",
    "Geçersiz sınır nedeniyle önceki ayarlara dönüldü": "Reverted to previous settings due to invalid limit",
    "Sınır kaydedildi. Etkili olması için Klipper yeniden başlatılsın mı?": (
        "Limit saved. Restart Klipper to apply?"
    ),
    "Özel sınırlar temizlendi. Varsayılanlar için Klipper yeniden başlatılsın mı?": (
        "Custom limits cleared. Restart Klipper for defaults?"
    ),
    "Sınırlar sıfırlandı ve Klipper yeniden başlatıldı": "Limits reset and Klipper restarted",

    # Touch calibration
    "Sol üst": "Top-left",
    "Sağ üst": "Top-right",
    "Sol alt": "Bottom-left",
    "Sağ alt": "Bottom-right",
    "Orta": "Center",
    "Köşe kalibrasyonu": "Corner calibration",
    "%s hedefine dokunun": "Tap %s target",
    "Sarı halkanın ortasına tek kez dokunun ve parmağınızı kaldırın.": (
        "Tap the center of the yellow ring once and lift your finger."
    ),
    "30 sn dokunulmazsa iptal edilir": "Cancels after 30 s with no touch",
    "Doğrulama": "Verification",
    "Orta hedef": "Center target",
    "Yeni kalibrasyonu doğrulamak için ortadaki sarı hedefe dokunun.": (
        "Tap the yellow center target to verify the new calibration."
    ),
    "En çok %d px sapma kabul edilir": "Max %d px offset accepted",
    "Yeni kalibrasyon": "New calibration",
    "Test ediliyor": "Testing",
    "Değerler": "Values",
    "Eksen takası": "Swap axes",
    "Evet": "Yes",
    "Hayır": "No",
    "Merkez sapma": "Center error",
    "Yeni ayar uygulanıyor. Kaydetmek için Onayla düğmesine dokunun.": (
        "New calibration active. Tap Confirm to save."
    ),
    "Otomatik geri dönüş: %d sn": "Auto revert in: %d s",
    "Onayla": "Confirm",
    "Vazgeç": "Cancel",
    "Mevcut ayar": "Current config",
    "Takaslı": "Swapped",
    "Kalibrasyon": "Calibration",
    "4 köşedeki hedeflere ve doğrulama için orta hedefe sırayla dokunun.": (
        "Tap the 4 corner targets in order, then the center target to verify."
    ),
    "Kalibrasyonu başlat": "Start calibration",
    "Varsayılan dokunmatik kalibrasyona dönülsün mü?": "Restore default touch calibration?",
    "Geçersiz hedef koordinatları": "Invalid target coordinates",
    "Köşe noktaları yeterince ayrı değil; tekrar deneyin": "Corner taps are too close; try again",
    "Kalibrasyon için 4 köşe dokunuşu gerekli": "4 corner taps required for calibration",
    "Sapma yüksek (%.0f px > %.0f px). Tekrar deneyin.": "Offset too high (%.0f px > %.0f px). Try again.",
    "Dokunmatik kalibrasyon kaydedildi": "Touch calibration saved",
    "Onaylanmadı, önceki kalibrasyona dönüldü": "Not confirmed, reverted to previous calibration",
    "Varsayılan kalibrasyona dönüldü": "Restored default touch calibration",

    # Wi-Fi & keyboard & modals
    "Taranıyor": "Scanning",
    "Bağlı": "Connected",
    "Bağlanıyor": "Connecting",
    "Bağlı değil": "Not connected",
    "Şifre: %s": "Password: %s",
    "Bağlan": "Connect",
    "Ağ": "Network",
    "IP / Sinyal": "IP / Signal",
    "Unut": "Forget",
    "%s ağı unutulsun mu?": "Forget network %s?",
    "Ağlar": "Networks",
    "Taranıyor...": "Scanning...",
    "%d ağ": "%d nets",
    "Ağlar taranıyor...": "Scanning networks...",
    "Ağ bulunamadı": "No networks found",
    "Şifreli": "Secured",
    "Kayıtlı": "Saved",
    "%s (açık ağ) ağına bağlanılsın mı?": "Connect to open network %s?",
    "Kayıtlı değil": "Not saved",
    "Güvenlik": "Security",
    "Sinyal": "Signal",
    "Şifre gir": "Enter password",
    "Boşluk": "Space",
    "Şifre girin": "Enter password",
    "Metin girin": "Enter text",
    "Onaylamak istediğinizde yapılacak işlem iptal edilmiştir.": (
        "The action when confirming has been cancelled."
    ),
    "Tamam": "OK",
    "%s ağına bağlanılıyor": "Connecting to %s",
    "%s ağı unutuldu": "Forgot network %s",

    # Network & Moonraker exact error strings
    "Wi-Fi yöneticisi etkin değil": "Wi-Fi manager is disabled",
    "Ağ adı (SSID) boş olamaz": "Network name (SSID) cannot be empty",
    "Ağ adı geçersiz karakter içeriyor": "Network name contains invalid characters",
    "Ağ adı en fazla 32 bayt olabilir": "Network name cannot exceed 32 bytes",
    "Şifre boş olamaz": "Password cannot be empty",
    "Şifre geçersiz karakter içeriyor": "Password contains invalid characters",
    "WPA şifresi 8 ile 63 karakter arasında olmalıdır": "WPA password must be 8 to 63 characters",
    "wpa_supplicant çalışmıyor": "wpa_supplicant is not running",
    "Ağ taraması başlatılamadı": "Failed to start network scan",
    "wpa_supplicant başlatılamadı": "Failed to start wpa_supplicant",
    "Wi-Fi yapılandırması yüklenemedi": "Failed to reload Wi-Fi config",
    "Ağ seçilemedi": "Failed to select network",

    # Tur 2: events, temperature chart, maintenance, updates, sound, filament tracking
    "Olay geçmişi": "Event log",
    "Bakım": "Maintenance",
    "Bakım (%d)": "Maint. (%d)",
    "Güncelleme": "Updates",
    "Ses": "Sound",
    "Sesli uyarı": "Sound alerts",
    "Kalan filament": "Remaining filament",
    "Olay geçmişi temizlendi": "Event log cleared",
    "Ses ayarı kaydedilemedi: %s": "Could not save sound setting: %s",
    "Bakım ayarı kaydedilemedi: %s": "Could not save maintenance: %s",
    "%s yapıldı olarak işaretlendi": "%s marked done",
    "Bakım eşiği güncellendi": "Maintenance threshold updated",
    "Makara ayarı kaydedilemedi: %s": "Could not save spool setting: %s",
    "Düşük filament: Harici makara (%.0f g)": "Low filament: External (%.0f g)",
    "Düşük filament: Yuva %d (%.0f g)": "Low filament: Slot %d (%.0f g)",
    "Harici: %s · %.0fg": "External: %s · %.0fg",
    "Az: Y%d": "Low: S%d",
    "Y%d · %.0fg!": "S%d · %.0fg!",
    "Y%d · %.0fg": "S%d · %.0fg",
    "Y%d: %s": "S%d: %s",
    "Assist: Y%d": "Assist: S%d",
    "Açık ağ": "Open",
    "Bakım: %s (+%d)": "Maint.: %s (+%d)",
    "Bakım: %s": "Maint.: %s",
    "Düşük filament: Harici (%.0fg)": "Low filament: Ext (%.0fg)",
    "Düşük filament: Yuva %d (%.0fg)": "Low filament: S%d (%.0fg)",
    "5 dk · %.0f°": "5m · %.0f°",
    "Ayarlanmadı": "Not set",
    "Harici makara": "External spool",
    "Kalan": "Remaining",
    "Miktar ayarla": "Set amount",
    "Tahmini filament (%.0f g / %.1f m) kalan miktardan (%.0f g / %.1f m) fazla. Başlatılsın mı?": (
        "Estimated filament (%.0f g / %.1f m) exceeds remaining (%.0f g / %.1f m). Start anyway?"
    ),
    "Filament yetersiz: kalan %.0f g": "Low filament: %.0f g left",
    "Kalan: %.0f g (%.1f m)": "Left: %.0f g (%.1f m)",
    "Kalan: ayarsız": "Left: not set",
    "Bildirim": "Notice",
    "G-code": "G-code",
    "Klipper": "Klipper",
    "Sistem": "System",
    "Kayıtlar": "Events",
    "%d kayıt": "%d entries",
    "Henüz olay kaydı yok": "No events recorded yet",
    "Temizle": "Clear",
    "Toplam": "Totals",
    "%.1f sa · %d iş": "%.1f h · %d jobs",
    "sa": "h",
    "baskı": "prints",
    "saat, 0=kapalı": "hours, 0=off",
    "baskı, 0=kapalı": "prints, 0=off",
    "Eşik": "Threshold",
    "Yapıldı": "Done",
    "Nozul kontrolü": "Nozzle check",
    "Kayış gerginliği": "Belt tension",
    "Eksen yağlama": "Axis lubrication",
    "ACE kurutucu filtresi": "ACE dryer filter",
    "Sürümler": "Versions",
    "Denetleniyor...": "Checking...",
    "Güncelleme yöneticisi yapılandırılmamış": "Update manager not configured",
    "Bileşen bilgisi yok": "No component info",
    "%d paket": "%d packages",
    "Güncel": "Up to date",
    "ace_driver_version": "ace-driver",
    "Ses yöntemi": "Audio method",
    "Baskı bitince": "On print complete",
    "Hata olunca": "On error",
    "Onayda bip": "On confirm",
    "Test bip": "Test beep",
    "Başlangıç miktarı (g)": "Initial amount (g)",
    "Başlangıç uzunluğu (m)": "Initial length (m)",
    "Az filament eşiği (g)": "Low filament threshold (g)",
    "Başlangıç": "Initial",
    "Gram gir": "Enter g",
    "Metre gir": "Enter m",
    "Eşik: %.0f g": "Threshold: %.0f g",
    "Sıfırla": "Reset",

    # Tur 3: horizontal buttons, summary/detail split, ACE connection state
    "Sıcaklık grafiği": "Temperature chart",
    "Klipper hazır değil": "Klipper not ready",
    "bulunamadı": "not found",
    "Kurutuyor · %s°": "Drying · %s°",
    "Kalan: %.0fg": "Left: %.0fg",

    # Tur 4: bed mesh, realistic button language, fast ACE stop, heating lock
    "Tabla seviyeleme": "Bed leveling",
    "Ön ısıtma": "Preheat",
    "Hedef": "Target",
    "XY sıfırla": "Home XY",
    "Motorları kapat": "Steppers off",
    "Adım: %d mm": "Step: %d mm",
    "Ekstrüde": "Extrude",
    "Baskı hızı": "Print speed",
    "Akış oranı": "Flow rate",
    "Manuel besleme": "Manual feed",
    "Besle": "Feed",
    "Besleme dur": "Stop feed",
    "Durduruluyor…": "Stopping…",
    "Isıtma kilidi": "Heating lock",
    "Isıtma kilitli": "Heat locked",
    "Isıtma kilidi açık": "Heating lock enabled",
    "extrusion_lock": "Extrusion lock",
    "extrusion_lock_desc": "Blocks nozzle extrusion",
    "extrusion_locked_msg": "Extrusion is locked",
    "confirm_disable_extrusion_lock": "Disable extrusion lock?\n\nIs the hotend cooling fan installed?",
    "confirm_extrusion_unlock": "Disable extrusion lock?\n\nIs the hotend cooling fan installed?",
    "Hotend soğutma fanı takılı mı?": "Is the hotend cooling fan installed?",
    "hotend_cooling_fan_attached": "Is the hotend cooling fan installed?",
    "Ekstrüzyon kilidi": "Extrusion lock",
    "Ekstrüzyon kilitli": "Extrusion locked",
    "Ekstrüzyon kilidi açık": "Extrusion lock enabled",
    "Nozul ekstrüzyonunu engeller": "Blocks nozzle extrusion",
    "Ekstrüzyon kilidi kapatılsın mı?\n\nHotend soğutma fanı takılı mı?": "Disable extrusion lock?\n\nIs the hotend cooling fan installed?",
    "Ayar kaydedilemedi: %s": "Could not save setting: %s",
    "Nozul ve tabla ısıtmayı engeller": "Blocks nozzle & bed heating",
    "Tabla haritası": "Bed mesh",
    "Yeniden ölç": "Re-measure",
    "Kaydedildi, Klipper yeniden başlıyor": "Saved, Klipper is restarting",
    "kapatıldı": "shut down",
    "Klipper yeniden başlatılmalı": "Restart Klipper first",
    "Dolaşma koruması": "Tangle protection",
    "Dolaşma koruması açıldı": "Tangle protection on",
    "Dolaşma koruması kapatıldı": "Tangle protection off",
    "Dolaşma eşiği %d sn": "Tangle threshold %d s",
    "Isıtma başlatılsın mı? (nozul %d°C, tabla %d°C)": "Start heating? (nozzle %d°C, bed %d°C)",
    "Isıtma başlatılsın mı? (nozul %d°C)": "Start heating? (nozzle %d°C)",
    "Isıtma başlatılsın mı? (tabla %d°C)": "Start heating? (bed %d°C)",
    "Isıt": "Heat",
    "Bir daha sorma": "Don't ask again",
    "Isıtmadan önce sor": "Ask before heating",
    "Tabla ısıtılacak, ölçüm yaklaşık %d dk sürer. Tabla boş ve temiz olsun. Başlatılsın mı?": (
        "The bed and nozzle will be heated; measuring takes about %d min. Keep the bed empty and clean. Start?"),
    "Başlat": "Start",
    "Tabla haritası yükleniyor...": "Loading bed mesh...",
    "Kayıtlı tabla haritası yok": "No saved bed mesh",
    "Stok kayıt · Klipper'da yok": "Stock mesh · not in Klipper",
    "İyi": "Good",
    "~Orta": "Fair",  # bed mesh rating ("Orta" alone is "Center" in the touch calibration)
    "Kötü": "Poor",
    "~Kapat": "Close",  # modal close button ("Kapat" alone is "Off" for heaters)
    # ACE slot mapping (print screen)
    'Yuva eşleştirme': 'Slot mapping',
    'ACE: T%d (%s) için dolu yuva yok': 'ACE: no loaded slot for T%d (%s)',
    'ACE yuva eşleştirmesi gönderilemedi: %s': 'ACE slot mapping could not be sent: %s',
    'Yuva yok': 'No slot',
    '%s: dolu yuva yok': '%s: no loaded slot',
    '%d renk: Yuva %s': '%d colours: slot %s',
    'Malzeme ve renge göre seçildi.': 'Picked by material and colour.',
    'Değiştirmek için yuvaya dokun.': 'Tap a slot to change it.',
    'Elle seçim var.': 'Changed by hand.',
    'Otomatik': 'Auto',
}

ERROR_PATTERNS = (
    (r"^Moonraker'a ulaşılamıyor: (.*)$", r"Cannot reach Moonraker: \1"),
    (r"^Moonraker geçersiz yanıt verdi \(HTTP (\d+)\)$", r"Invalid Moonraker response (HTTP \1)"),
    (r"^Yeniden başlatılamadı: (.*)$", r"Reboot failed: \1"),
    (r"^Uygulama devre dışı bırakılamadı: (.*)$", r"Could not disable app: \1"),
    (r"^Kalibrasyon kaydedilemedi: (.*)$", r"Could not save calibration: \1"),
    (r"^Varsayılan kalibrasyon yazılamadı: (.*)$", r"Could not write default calibration: \1"),
    (r"^Dil ayarı kaydedilemedi: (.*)$", r"Could not save language setting: \1"),
    (r"^Ekran ayarı kaydedilemedi: (.*)$", r"Could not save display setting: \1"),
    (r"^Ses ayarı kaydedilemedi: (.*)$", r"Could not save sound setting: \1"),
    (r"^Bakım ayarı kaydedilemedi: (.*)$", r"Could not save maintenance: \1"),
    (r"^Makara ayarı kaydedilemedi: (.*)$", r"Could not save spool setting: \1"),
    (r"^Ayar kaydedilemedi: (.*)$", r"Could not save setting: \1"),
    (r"^Isıtma kilidi açıldı$", r"Heating lock enabled"),
    (r"^Isıtma kilidi kapatıldı$", r"Heating lock disabled"),
    (r"^Ekstrüzyon kilidi açıldı$", r"Extrusion lock enabled"),
    (r"^Ekstrüzyon kilidi kapatıldı$", r"Extrusion lock disabled"),
    (r"^Moonraker bağlantısı kuruldu$", r"Moonraker connected"),
    (r"^Moonraker bağlantısı kesildi: (.*)$", r"Moonraker disconnected: \1"),
    (r"^Klipper hazır$", r"Klipper ready"),
    (r"^Klipper: (.*)$", r"Klipper: \1"),
)
