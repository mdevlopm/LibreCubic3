# Üçüncü taraf bileşenler ve kaynaklar

Bu depodaki kendi kodumuz GPLv3 lisanslıdır ([LICENSE](LICENSE)). Aşağıdakiler kendi lisanslarıyla dağıtılır veya başka projelerden türetilmiştir.

| Bileşen | Konum | Lisans / not |
|---|---|---|
| ACE Pro sürücüsü | `ace-driver/` | Rinkhals vanilla-klipper uygulamasının (0.3) içinde gelen Klipper ACE sürücüsünden türetildi ve büyük ölçüde değiştirildi. O pakette sürücünün yazarı ve lisansı belirtilmiyordu. Klipper eklentisi olduğu için burada GPLv3 ile dağıtılıyor. Özgün yazarı sizseniz lütfen bir Issue açın; atıf ve lisansı düzeltelim. |
| numpy 1.24.4 | `open-kobra/vendor/numpy-1.24.4-cp311-cp311-linux_armv7l.whl` | BSD-3-Clause, © NumPy Developers. Lisans metni wheel'in içinde (`numpy/LICENSE.txt`). Derleme tarifi: `tools/numpy-uclibc/`. |
| Material Icons yazı tipi | `kobra-screen/fonts/MaterialIcons-Regular.ttf` | Apache License 2.0, © Google. Metin: `kobra-screen/fonts/LICENSE-MaterialIcons.txt`. Yazıcıda Rinkhals'ın kendi kopyası kullanılır; bu dosya yalnızca bilgisayarda önizleme ve test içindir. |
| Kobra 3 stok `start.sh` (firmware 2.4.6.7) | `open-kobra/rescue/manifests/K3_2.4.6.7.start.sh` | Anycubic'in açılış betiğinin birebir kopyası. Kurtarma katmanı, bozulan bir `start.sh`'yi tanıyıp onarmak için bu referansı kullanır. © Anycubic. |
| Rinkhals | <https://github.com/jbatonnet/Rinkhals> | MIT, © Julien Batonnet. Bu paket bir Rinkhals eklentisidir; Rinkhals'ın uygulama yapısını ve SWU kurulum biçimini kullanır, Rinkhals kodu içermez. |
| Klipper | <https://github.com/Klipper3d/klipper> | GPLv3. `leviq3-klipper/` ve `ace-driver/` Klipper eklentisidir. |

`leviq3-klipper/leviq3.py`, stok firmware'deki LEVIQ komutlarının tersine mühendislikle incelenen davranışına göre sıfırdan yazılmıştır; stok koddan kopya içermez.
