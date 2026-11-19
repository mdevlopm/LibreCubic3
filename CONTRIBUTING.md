# Katkı

Hata bildirimleri, öneriler ve kod katkıları memnuniyetle karşılanır.

- **Hata ve öneri:** [Issues](../../issues) sayfasında uygun şablonu seçin. Soru ve fikir tartışması için Discussions.
- **Güvenlik açığı:** [SECURITY.md](SECURITY.md).

## Kod katkısı

1. Depoyu fork'layın, bir dal açın.
2. Değişikliğinizin testini ekleyin ve testleri çalıştırın:
   ```sh
   cd open-kobra && ./test.sh
   cd kobra-screen && python3 -m unittest discover -p 'test_*.py'
   cd ace-driver && python3 -m unittest discover -s tests
   cd leviq3-klipper && python3 -m unittest test_leviq3
   ```
3. Kabuk betikleri yazıcıdaki BusyBox `sh` ile çalışır: bash'e özgü sözdizimi kullanmayın (`test.sh` bunu denetler).
4. Yazıcıda Python 3.11 (uClibc) çalışır; standart kütüphane dışında bağımlılık eklemeyin.
5. Pull request açın; ne değiştiğini ve yazıcıda denenip denenmediğini yazın.

Katkılar GPLv3 altında kabul edilir.
