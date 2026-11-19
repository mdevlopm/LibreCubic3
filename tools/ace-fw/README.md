# ACE Pro kurutucu soğutma süresi yaması (ileri düzey, isteğe bağlı)

ACE Pro (1. nesil) kurutmayı durdurduktan sonra ısıtıcı plakasını soğutmak için fanı yaklaşık **3 dakika** daha çalıştırır. Bu araçlar firmware'deki bu süreyi **1 dakikaya** indirir. kobra-screen yamalı firmware'i tanır ve ekranda 1 dakikalık geri sayım gösterir ("Isıtıcı plakası güvenlik için soğutuluyor").

> ⚠️ **Riskli.** ACE Pro'nun firmware'ini değiştirir. Yükleme yarıda kesilirse ACE Pro çalışmaz hale gelebilir; ACE'in önyükleyicisi için kurtarma yöntemi bilinmiyor. Yalnızca ne yaptığınızı biliyorsanız ve **SSH ile yazıcıya erişebiliyorsanız** kullanın. USB kurulum paketi bunu yapmaz.

## Ne gerekir

- ACE Pro 1. nesil, firmware **V1.3.863**. Başka sürüme yama uygulanmaz; araç reddeder.
- Anycubic'in V1.3.863 firmware dosyası. Depoda firmware yoktur; dosyayı kendiniz edinin. Beklenen SHA-256:
  `3f3676ba357749b3d0367922cb7f6b0dedc9b3d104c1c29e719ac3e563f5eb63`
- Yazıcıya SSH erişimi.

## Adımlar

1. Bilgisayarda yamayı uygulayın:
   ```sh
   python3 patch_cooldown.py ACE_V1.3.863_20250518.bin ACE_V1.3.86K.bin
   ```
   Değişen yalnızca soğutma karşılaştırma komutu (180 sn → 60 sn) ve sürüm dizgesidir (`V1.3.863` → `V1.3.86K`). Çıktının SHA-256'sı `0b50ec60e3a6e0c2eaaf1b0ba1fe31da2b9b9bae11f0c85491ec3a206bf7ef9c` olmalı.
2. `ace_iap.py` ve `ACE_V1.3.86K.bin` dosyalarını yazıcıya kopyalayın.
3. Yazıcıda Klipper'ı durdurun (ACE'in seri portunu başka bir program kullanmamalı). ACE kurutma yapıyorsa önce durdurun.
4. Önce deneme modunda çalıştırın, sonra yükleyin:
   ```sh
   python3 ace_iap.py --dry-run ACE_V1.3.86K.bin
   python3 ace_iap.py --expect-version V1.3.86K ACE_V1.3.86K.bin
   ```
   Yükleme yaklaşık 1 dakika sürer. Sonunda ACE yeniden başlar. "port not found after reboot" uyarısı zararsızdır: Klipper durduğu sürece ACE'in USB bağlantısı birkaç saniyede bir kopup gelir.
5. Yazıcıyı yeniden başlatın.

Geri dönmek için aynı aracı orijinal V1.3.863 dosyasıyla çalıştırın.

## Bilinen yan etki

Yüklemeden sonra ACE'in önyükleyicisi, `get_info` yanıtındaki `boot_firmware` alanına okunamayan baytlar yazar. Bu depodaki ACE sürücüsü ve ace-standalone bunu tolere eder (önyükleyici sürümü "?" görünür). Stok Anycubic yazılımının bu durumda nasıl davrandığı test edilmedi.

## Testler

```sh
python3 -m unittest test_ace_iap -v
```

Testler gerçek cihaz yerine sahte bir seri cihazla çalışır.
