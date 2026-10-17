import os
import shutil
import subprocess
import glob
import streamlit as st

# --- AYARLAR (Kendi bilgilerine göre doldur) ---
AG_KLASORU = r"\\192.168.1.100\OrtakKlasor\Uretim"  # Şirketin kilitli klasör yolu
KULLANICI_ADI = "kullanici_adin"                    # Klasör şifresi kullanıcı adı
SIFRE = "sifren123"                                 # Klasör şifresi

HEDEF_DOSYA = r"C:\Users\Akaero\Documents\newakaeroproject1 - Kopya\puredata1.xlsx"
ETL_SCRIPT_ADI = "etl.py"                           # Çalıştırdığın ETL dosyasının tam adı

def veriyi_cek_ve_guncelle():
    try:
        # 1. Şifreli ağ klasörüne arka planda otomatik bağlan
        baglanti_komutu = f'net use "{AG_KLASORU}" "{SIFRE}" /user:{KULLANICI_ADI}'
        subprocess.run(baglanti_komutu, shell=True, check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

        # 2. Klasördeki Excel dosyasını bul (İsmi her gün değişiyorsa bile en son güncellenen .xlsx dosyasını otomatik bulur)
        # Eğer dosya adı hep sabitse (örn: "Giris_Tablosu.xlsx") doğrudan os.path.join(AG_KLASORU, "Giris_Tablosu.xlsx") yazabilirsin.
        excel_dosyalari = [
            f for f in glob.glob(os.path.join(AG_KLASORU, "*.xlsx"))
            if not os.path.basename(f).startswith("~$")  # Arkadaşında dosya açıksa oluşan gizli kilit dosyalarını yoksay
        ]
        
        if not excel_dosyalari:
            return False, "Klasörde Excel dosyası bulunamadı veya ağ bağlantısı kurulamadı!"

        # En son kaydedilen (en güncel) Excel dosyasını seç
        kaynak_dosya = max(excel_dosyalari, key=os.path.getmtime)

        # 3. Kendi klasörüne "puredata1.xlsx" adıyla kopyala (Üzerine yazar)
        shutil.copy2(kaynak_dosya, HEDEF_DOSYA)

        # 4. Python ETL scriptini çalıştır
        etl_sonuc = subprocess.run(["python", ETL_SCRIPT_ADI], capture_output=True, text=True)
        if etl_sonuc.returncode != 0:
            return False, f"ETL çalışırken hata oluştu:\n{etl_sonuc.stderr}"

        return True, f"Başarılı! '{os.path.basename(kaynak_dosya)}' çekildi, puredata1.xlsx yapıldı ve veritabanı güncellendi."

    except Exception as e:
        return False, f"Hata: {str(e)}"