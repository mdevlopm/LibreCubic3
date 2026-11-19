# Güvenlik politikası

## Güvenlik açığı bildirme

Güvenlik açıklarını **herkese açık Issue olarak açmayın.**

Bu depo sayfasında **Security → Report a vulnerability** düğmesiyle GitHub üzerinden gizli bildirim gönderin. Bildirimi yalnızca proje sahibi görür.

Bildirime şunları ekleyin:

- Etkilenen bileşen (kobra-screen, ACE sürücüsü, kurulum paketi, ace-standalone…) ve sürüm (`open-kobra/VERSION`)
- Sorunu yeniden üretme adımları
- Olası etki (ör. ağdan yetkisiz erişim, yazıcıyı tehlikeli sıcaklığa çıkarma, dosya silme)

Günlüklerden Wi-Fi adı, IP adresi, seri numarası ve parola gibi bilgileri çıkarın.

## Kapsam

Özellikle şunlar önemlidir:

- Ağdan erişilebilen servisler: ace-standalone web paneli (varsayılan yalnızca `127.0.0.1`), Moonraker ile konuşan bileşenler
- Isıtıcıların veya motorların güvenlik sınırlarını aşan davranışlar
- Kurulum ve kaldırma betiklerinde yanlış dosyanın silinmesi veya üzerine yazılması
- Kurulum paketinin bütünlük denetimlerinin atlatılması

Rinkhals'ın, Klipper'ın, Moonraker'ın veya Anycubic stok yazılımının kendi açıklarını ilgili projelere bildirin.

## Destek

Bu gönüllü bir projedir; yanıt süresi garanti edilmez. Doğrulanan açıklar için düzeltme yayınlanır ve sürüm notlarında belirtilir.
