"""
etl.py — Üretim Verisi Temizleme ve OEE Analiz Motoru

INCOMING_DIR klasöründeki Excel dosyalarının "Giriş" sayfasını okur, gelişmiş
temizlik + kategorizasyon (Durum tespiti, Duruş kategorisi, Vardiya sınıfı,
CT sapması, Emsal karşılaştırması) adımlarından geçirir, sonra üç tabloyu
yazar:

  1. detay_uretim_loglari  — satır bazlı, tüm feature'ların olduğu ana tablo
  2. tezgah_oee             — tezgah bazlı Kullanılabilirlik / Kalite özeti
  3. operator_karne         — operatör bazlı performans karnesi

Nereye yazılır? config.DATABASE_URL (.env) TANIMLIYSA Postgres'e (ANA kaynak)
+ SQLite'a (yedek/mirror); TANIMLI DEĞİLSE sadece yerel SQLite'a.

Çalıştırma: python etl.py
"""
from __future__ import annotations

import json
import logging
import re
import shutil
import sqlite3
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

import config

# BUG DÜZELTMESİ: bu dosya turkce_temizle / categorize_reason / vardiya_saat /
# vardiya_sinifi / vardiya_ici_pozisyon / _fix_turkish_decimal / _fix_mixed_dates /
# _get_or_assign_operator_kodlari fonksiyonlarını KULLANIYORDU ama HİÇBİRİNİ
# import ETMİYORDU — bu yüzden `python etl.py` çalıştırıldığında _clean()
# içinde NameError ile çöküyordu. Hepsi temizlik_kurallari.py'de tanımlı;
# aşağıda tek seferde import ediliyor.
from temizlik_kurallari import (
    turkce_temizle,
    categorize_reason,
    vardiya_saat,
    vardiya_sinifi,
    vardiya_ici_pozisyon,
    _fix_turkish_decimal,
    _fix_mixed_dates,
    _get_or_assign_operator_kodlari,
)

# ---------------------------------------------------------------------------
# LOGGING
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-7s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("etl")




# ---------------------------------------------------------------------------
# DOSYA BULMA / OKUMA
# ---------------------------------------------------------------------------
def _find_excel_files(folder: Path) -> list[Path]:
    if not folder.exists():
        log.error("INCOMING_DIR bulunamadı: %s", folder)
        return []
    files = sorted(folder.glob("*.xlsx")) + sorted(folder.glob("*.xls"))
    files = [f for f in files if not f.name.startswith("~$")]
    return files


def _move_to(path: Path, target_dir: Path):
    try:
        target_dir.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, target_dir / path.name)
    except Exception as e:
        log.warning("Dosya arşivlenemedi (%s): %s", path.name, e)


def _read_one(path: Path) -> pd.DataFrame | None:
    """Tek bir Excel dosyasının 'Giriş' sayfasını okur.

    dtype=str kullanıyoruz: pandas, '1.422' gibi metin görünümlü bir binlik-
    ayraçlı sayıyı SESSİZCE 1.422 (küçük bir ondalık) sanıp okuyabilir —
    bu, süre/miktar değerlerini 1000 kat küçük hesaplamak gibi ciddi bir
    hataya yol açar. Her şeyi önce metin okuyup dönüşümü kendi kontrollü
    fonksiyonlarımızla (_fix_turkish_decimal, _fix_mixed_dates) yapmak bu
    riski ortadan kaldırır.
    """
    try:
        df = pd.read_excel(
            path, sheet_name=config.SHEET_NAME, header=config.HEADER_ROW,
            engine="openpyxl", dtype=str,
        )
    except Exception as e:
        log.error(
            "Dosya okunamadı, atlanıyor ('%s' sayfası eksik/bozuk olabilir): %s (%s)",
            config.SHEET_NAME, path.name, e,
        )
        _move_to(path, config.ERROR_DIR)
        return None

    if df.empty:
        log.warning("Boş dosya, atlanıyor: %s", path.name)
        return None

    # Kolon başlıklarındaki satır içi \n karakterlerini _ ile değiştiriyoruz
    # (örn. "ÜRETİM\nDURUŞ\nSET-UP\nYEMEK" -> "ÜRETİM_DURUŞ_SET-UP_YEMEK")
    # ki koddan referans vermek pratik olsun.
    df.columns = [str(c).replace("\n", "_").strip() for c in df.columns]
    df["__kaynak_dosya"] = path.name
    return df


# ---------------------------------------------------------------------------
# TEMİZLİK + FEATURE ENGINEERING (ANA MANTIK)
# ---------------------------------------------------------------------------
def _clean(df: pd.DataFrame) -> pd.DataFrame:
    stats = {"baslangic": len(df)}

    # "SÜRE_SAAT-1" -> "Sure_Saat" (asıl kullanılacak süre kolonu ismi)
    if "SÜRE_SAAT-1" in df.columns:
        df = df.rename(columns={"SÜRE_SAAT-1": "Sure_Saat"})

    # NOT: "Sure_Saat"in erken (dedup-öncesi) tek kolonluk dönüşümü kaldırıldı —
    # aşağıdaki for-döngüsü zaten dedup'tan hemen önce hem Sure_Saat hem
    # ÜRETİM_C/T'yi dönüştürüyordu, bu satır sadece tekrar eden/vestigial bir
    # adımdı (ve eski hâliyle `fix_turkish_decimal` diye var olmayan bir isim
    # kullandığı için zaten çalışmıyordu).
    if "TARİH" in df.columns:
        df["TARİH"] = _fix_mixed_dates(df["TARİH"])  # BUG DÜZELTMESİ: fonksiyon adı `_` ile başlıyor

    # Tekrar-tespitinde KULLANILACAK kritik alanları (Sure_Saat, ÜRETİM_C/T)
    # dedup'tan ÖNCE sayıya çeviriyoruz — aksi halde '1.5' ile '1,5' gibi
    # aynı değerin farklı yazılmış hallerini "farklı satır" sanıp gerçek bir
    # tekrarı atlayabiliriz. DİĞER sayısal kolonları (ÜRETİM_SÜRESİ,
    # YEMEK_MOLA_SÜRESİ, SET-UP_SÜRESİ, İMALAT YAPILAN SÜRE-1) BİLEREK
    # dedup'tan SONRAYA erteliyoruz: bunlar formül kaynaklı ikincil alanlardır
    # ve "0" / "0.0" gibi salt biçim farklarını erkenden eşitlemek, aslında
    # BAŞKA kolonlarda (S/N, saat vb.) farklı olan gerçek/farklı satırları
    # yanlışlıkla "tam tekrar" sanıp silme riskini artırır.
    for col in ["Sure_Saat", "ÜRETİM_C/T"]:
        if col in df.columns:
            df[col] = _fix_turkish_decimal(df[col])

    # Tam tekrar eden satırları at
    before = len(df)
    df = df.drop_duplicates()
    stats["tam_tekrar_atilan"] = before - len(df)

    # Şimdi geri kalan sayısal alanları dönüştür (dedup sonrası, güvenli)
    for col in ["ÜRETİM_SÜRESİ", "YEMEK_MOLA_SÜRESİ", "SET-UP_SÜRESİ", "İMALAT YAPILAN SÜRE-1"]:
        if col in df.columns:
            df[col] = _fix_turkish_decimal(df[col])

    # Negatif/hatalı süreli satırları at (bir olay negatif süremez)
    if "Sure_Saat" in df.columns:
        before = len(df)
        df = df[(df["Sure_Saat"].isna()) | (df["Sure_Saat"] >= 0)]
        stats["negatif_sure_atilan"] = before - len(df)

    # Tezgahı boş olan "hayalet" (boş şablon) satırlarını at.
    # NOT: Bu satırlardan bazıları tamamen boş değil (formül hücreleri 0
    # döndürüyor olabilir) — bu yüzden "tamamen boş satır" testi yerine
    # doğrudan TEZGAH boşluğuna bakıyoruz. Ayrıca TEZGAH boşken başka
    # gerçek veri (operatör/tarih) taşıyan satırları ayrıca logluyoruz;
    # bunlar muhtemelen veri girişi hatasıdır ve elle kontrol edilmelidir.
    if "TEZGAH" in df.columns:
        eksik_tezgah = df["TEZGAH"].isna()
        if "OPERATÖR" in df.columns:
            supheli = df[eksik_tezgah & df["OPERATÖR"].notna()]
            if not supheli.empty:
                log.warning(
                    "%d satırda TEZGAH boş ama OPERATÖR/diğer veriler dolu — "
                    "veri girişi hatası olabilir, bu satırlar ATILDI, elle kontrol edin. "
                    "Örnek satır(lar): %s",
                    len(supheli), supheli.head(3).to_dict("records"),
                )
                # KÜÇÜK İYİLEŞTİRME: konsol logu çoğu zaman kaybolur/görülmez —
                # bu şüpheli satırları ayrıca data/errors/ altına bir CSV olarak
                # da yazıyoruz ki biri "elle kontrol et" dediğinde gerçekten
                # açıp bakabileceği bir dosya olsun.
                try:
                    config.ERROR_DIR.mkdir(parents=True, exist_ok=True)
                    rapor_yolu = config.ERROR_DIR / f"tezgah_bos_supheli_satirlar_{datetime.now():%Y%m%d_%H%M%S}.csv"
                    supheli.to_csv(rapor_yolu, index=False, encoding="utf-8-sig")
                    log.info("Şüpheli satırlar ayrıca şuraya yazıldı: %s", rapor_yolu)
                except Exception as e:
                    log.warning("Şüpheli satırlar CSV'ye yazılamadı: %s", e)
        before = len(df)
        df = df[~eksik_tezgah].copy()
        stats["tezgah_bos_atilan"] = before - len(df)

    # ---- Operatör normalizasyonu + KALICI kod ataması ----
    if "OPERATÖR" in df.columns:
        df["OPERATÖR_norm"] = df["OPERATÖR"].apply(turkce_temizle).replace(config.OPERATOR_DUZELTME)
        op_map = _get_or_assign_operator_kodlari(df["OPERATÖR_norm"])
        df["Operator_Kod"] = df["OPERATÖR_norm"].map(op_map)

    # ---- Durum tespiti (Üretim / Plansız_Duruş / Set_Up / Yemek_Mola / Rework) ----
    if "ÜRETİM_DURUŞ_SET-UP_YEMEK" in df.columns:
        df["Durum"] = df["ÜRETİM_DURUŞ_SET-UP_YEMEK"].map(config.DURUM_MAP).fillna("Sınıflandırılmamış")

        if "KOD & OPE." in df.columns:
            kod_metin = df["KOD & OPE."].astype(str)
            for pattern, hedef_durum in config.DURUM_DUZELTME_KURALLARI:
                mask = (df["Durum"] == "Üretim") & kod_metin.str.contains(pattern, na=False)
                df.loc[mask, "Durum"] = hedef_durum

    # ---- Duruş sebebi kategorizasyonu ----
    df["Durus_Sebebi_Ham"] = np.where(
        df.get("Durum") == "Plansız_Duruş", df.get("OPERASYON_NO", np.nan), np.nan
    )
    df["Durus_Kategorisi"] = df["Durus_Sebebi_Ham"].apply(categorize_reason)

    # ---- Vardiya hesaplamaları ----
    if "VARDİYA" in df.columns:
        df["Vardiya_Uzunluk_Saat"] = df["VARDİYA"].apply(vardiya_saat)
        df["Vardiya_Sinifi"] = df["VARDİYA"].apply(vardiya_sinifi)
        if "BAŞLANGIÇ_SAAT" in df.columns:
            df["Vardiya_Ici_Pozisyon_Yuzde"] = df.apply(vardiya_ici_pozisyon, axis=1)

    # ---- Tarih düzeltmesi + takvim feature'ları ----
    if "TARİH" in df.columns:
        df["TARİH"] = _fix_mixed_dates(df["TARİH"])
        df["Haftanin_Gunu"] = df["TARİH"].dt.day_name()
        df["Hafta_Sonu"] = df["TARİH"].dt.dayofweek.isin([5, 6]).astype(int)

    # ---- Son operasyon mu (parçanın bitip bitmediği) ----
    if "FINISH" in df.columns:
        df["Son_Operasyon_mu"] = df["FINISH"].astype(str).str.strip().str.upper().eq("F")

    # ---- Üretim C/T referansını doldur + CT sapması ----
    if all(c in df.columns for c in ["ÜRETİM_C/T", "PARÇA NO_ÜRETİM_GİRİLEN", "OPERASYON_NO"]):
        df["ÜRETİM_C/T"] = df["ÜRETİM_C/T"].fillna(
            df.groupby(["PARÇA NO_ÜRETİM_GİRİLEN", "OPERASYON_NO"])["ÜRETİM_C/T"].transform("median")
        )
        uretim_mask = (df["Durum"] == "Üretim") & (df["ÜRETİM_C/T"] > 0)
        df["Sapma_Saat"] = np.where(uretim_mask, df["Sure_Saat"] - df["ÜRETİM_C/T"], np.nan)
        df["Sapma_Yuzdesi"] = np.where(
            uretim_mask, (df["Sapma_Saat"] / df["ÜRETİM_C/T"]) * 100, np.nan
        )

    stats["bitis"] = len(df)
    log.info("Temizlik özeti: %s", stats)

    df["kaynak"] = "excel"
    df["sisteme_kayit_zamani"] = pd.Timestamp.now()

    return df


def _select_canonical(detay: pd.DataFrame) -> pd.DataFrame:
    """detay_uretim_loglari tablosuna YAZILACAK kolon kümesini
    config.CANONICAL_COLUMNS'a göre seçer. Bunun dışında kalan (S/N, parça,
    ham SÜRE_SAAT, İMALAT YAPILAN SÜRE-1, OPERATÖR, OPERATÖR_norm vb.)
    kolonlar ana fact tablosuna YAZILMAZ — Postgres tablosu zaten bunları
    kabul etmez (kolon hatası), ayrıca OPERATÖR/OPERATÖR_norm gibi ham isim
    kolonlarının her satırda tekrarlanmasını istemiyoruz (bkz. gizlilik notu)."""
    eksik = [c for c in config.CANONICAL_COLUMNS if c not in detay.columns]
    if eksik:
        log.warning("detay_uretim_loglari'nde beklenen ama veride olmayan kolonlar: %s", eksik)
    mevcut = [c for c in config.CANONICAL_COLUMNS if c in detay.columns]
    return detay[mevcut].copy()




# ---------------------------------------------------------------------------
# PARÇA-TEZGAH UYUM MATRİSİ + FIRSAT MALİYETİ
# ---------------------------------------------------------------------------
def compute_parca_tezgah_matrisi(df: pd.DataFrame) -> pd.DataFrame:
    """Aynı 'KOD & OPE.' (parça+operasyon) kodunu işleyen FARKLI tezgahları
    birbirine göre kıyaslar (emsal bazlı benchmark — sadece katalog C/T'ye
    değil, tezgahların/operatörlerin GERÇEK fiili sürelerine dayanır).

    Mantık (Üretim Verimliliği sunumundaki "Parça-Tezgah Uyum Matrisi" ile
    birebir aynı metodoloji):
      1. Her (KOD & OPE., TEZGAH) kombinasyonu için medyan süre hesaplanır.
      2. En az config.EMSAL_MIN_TEKRAR (varsayılan 3) tekrarı olmayan
         kombinasyonlar elenir (istatistiksel olarak güvenilmez).
      3. Sadece EN AZ 2 FARKLI TEZGAHTA işlenmiş kodlar tutulur — kıyaslama
         ancak böyle mümkündür.
      4. O kod için TÜM tezgahlar arasındaki EN HIZLI medyan referans alınır;
         diğer tezgahların bu referansa göre dakika/yüzde sapması hesaplanır.
      5. Fırsat Maliyeti = (bu tezgahtaki işlem sayısı) x (referansa göre
         kaybedilen dakika) — yani "bu parça hep en hızlı tezgahta işlenseydi
         ne kadar saat kazanılırdı" sorusunun cevabı.

    Referans C/T sapması (Sapma_Yuzdesi, satır bazlı) de ortalanıp eklenir —
    böylece hem EMSAL hem CT bazlı karşılaştırma aynı tabloda görülür.
    """
    gerekli = ["KOD & OPE.", "TEZGAH", "Sure_Saat", "Durum"]
    if df.empty or not all(c in df.columns for c in gerekli):
        return pd.DataFrame()

    u = df[df["Durum"] == "Üretim"].dropna(subset=["KOD & OPE.", "Sure_Saat"]).copy()
    if u.empty:
        return pd.DataFrame()

    grup = u.groupby(["KOD & OPE.", "TEZGAH"]).agg(
        Medyan_Saat=("Sure_Saat", "median"),
        Islem_Sayisi=("Sure_Saat", "count"),
    ).reset_index()
    grup = grup[grup["Islem_Sayisi"] >= config.EMSAL_MIN_TEKRAR]
    if grup.empty:
        return pd.DataFrame()

    # En az 2 farklı tezgahta işlenmiş kodlar (kıyaslama ancak böyle mümkün)
    kod_tezgah_sayisi = grup.groupby("KOD & OPE.")["TEZGAH"].nunique()
    karsilastirilabilir = kod_tezgah_sayisi[kod_tezgah_sayisi >= 2].index
    grup = grup[grup["KOD & OPE."].isin(karsilastirilabilir)].copy()
    if grup.empty:
        return pd.DataFrame()

    en_hizli = grup.groupby("KOD & OPE.")["Medyan_Saat"].min().rename("En_Hizli_Medyan_Saat")
    grup = grup.merge(en_hizli, on="KOD & OPE.")
    grup["Emsal_Sapma_Dakika"] = ((grup["Medyan_Saat"] - grup["En_Hizli_Medyan_Saat"]) * 60).round(1)
    grup["Emsal_Sapma_Yuzde"] = np.where(
        grup["En_Hizli_Medyan_Saat"] > 0,
        ((grup["Medyan_Saat"] - grup["En_Hizli_Medyan_Saat"]) / grup["En_Hizli_Medyan_Saat"] * 100).round(1),
        0,
    )
    grup["Firsat_Maliyeti_Saat"] = (grup["Islem_Sayisi"] * grup["Emsal_Sapma_Dakika"] / 60).round(2)

    # CT bazlı ortalama sapma da (varsa) aynı tabloya eklenir
    if "Sapma_Yuzdesi" in u.columns:
        ct = u.groupby(["KOD & OPE.", "TEZGAH"])["Sapma_Yuzdesi"].mean().round(1).rename("CT_Sapma_Yuzde")
        grup = grup.merge(ct, on=["KOD & OPE.", "TEZGAH"], how="left")

    return grup.sort_values("Firsat_Maliyeti_Saat", ascending=False).reset_index(drop=True)


# ---------------------------------------------------------------------------
# VARDİYA YORGUNLUK ENDEKSİ
# ---------------------------------------------------------------------------
def compute_vardiya_yorgunluk(df: pd.DataFrame) -> pd.DataFrame:
    """Vardiya içindeki başlangıç pozisyonuna (çeyreklik dilim) göre hata
    (Rework) oranını hesaplar: (İlgili dilimdeki Rework / o dilimdeki toplam
    Üretim+Rework işlem sayısı) x 100. Az üretimde az hata da çıkacağı için
    ham hata SAYISINA değil ORANINA bakılır — sunumdaki yöntemle birebir aynı.
    """
    gerekli = ["Vardiya_Ici_Pozisyon_Yuzde", "Durum"]
    if df.empty or not all(c in df.columns for c in gerekli):
        return pd.DataFrame()

    ilgili = df[
        df["Durum"].isin(["Üretim", "Rework"]) & df["Vardiya_Ici_Pozisyon_Yuzde"].notna()
    ].copy()
    # Anomalileri (vardiya dışına taşan, muhtemelen saat girişi hatası olan
    # >100 veya <0 değerleri) bu özel analizden dışarıda bırakıyoruz.
    ilgili = ilgili[ilgili["Vardiya_Ici_Pozisyon_Yuzde"].between(0, 100)]
    if ilgili.empty:
        return pd.DataFrame()

    ilgili["Dilim"] = pd.cut(
        ilgili["Vardiya_Ici_Pozisyon_Yuzde"], bins=[0, 25, 50, 75, 100],
        labels=["1. Çeyrek (0-25%)", "2. Çeyrek (25-50%)", "3. Çeyrek (50-75%)", "4. Çeyrek (75-100%)"],
        include_lowest=True,
    )

    ozet = ilgili.groupby("Dilim", observed=True).apply(
        lambda g: pd.Series({
            "Toplam_Islem": len(g),
            "Hata_Sayisi": int((g["Durum"] == "Rework").sum()),
        }),
        include_groups=False,
    ).reset_index()
    ozet["Hata_Orani_%"] = np.where(
        ozet["Toplam_Islem"] > 0, (ozet["Hata_Sayisi"] / ozet["Toplam_Islem"] * 100).round(2), 0.0
    )
    return ozet


# ---------------------------------------------------------------------------
# TEZGAH OEE ÖZETİ
# ---------------------------------------------------------------------------
def compute_tezgah_oee(df: pd.DataFrame) -> pd.DataFrame:
    """Tezgah bazlı Kullanılabilirlik ve Kalite oranlarını hesaplar.

    Kullanılabilirlik = (Üretim + Rework) / (Üretim + Plansız_Duruş + Rework)
      -> Set-up ve yemek molası paydaya dahil edilmez, çünkü bunlar planlı
         (kaçınılmaz) süredir, OEE'nin "kayıp" bacağı değildir.
    Kalite_Orani = Üretim / (Üretim + Rework)
      -> Bu veri setinde fire miktarı yok; Rework (yeniden işleme gereken
         parça) süresi kalite kaybının vekil (proxy) göstergesidir.
    """
    if df.empty or "TEZGAH" not in df.columns:
        return pd.DataFrame()

    ozet = df.groupby("TEZGAH").apply(
        lambda g: pd.Series({
            "Uretim_Saat": g.loc[g["Durum"] == "Üretim", "Sure_Saat"].sum(),
            "Plansiz_Durus_Saat": g.loc[g["Durum"] == "Plansız_Duruş", "Sure_Saat"].sum(),
            "Set_Up_Saat": g.loc[g["Durum"] == "Set_Up", "Sure_Saat"].sum(),
            "Yemek_Saat": g.loc[g["Durum"] == "Yemek_Mola", "Sure_Saat"].sum(),
            "Rework_Saat": g.loc[g["Durum"] == "Rework", "Sure_Saat"].sum(),
            "Kayit_Sayisi": len(g),
        }),
        include_groups=False,
    ).reset_index()

    ozet["Planlanan_Sure"] = ozet["Uretim_Saat"] + ozet["Plansiz_Durus_Saat"] + ozet["Rework_Saat"]
    ozet["Kullanilabilirlik"] = np.where(
        ozet["Planlanan_Sure"] > 0,
        ((ozet["Uretim_Saat"] + ozet["Rework_Saat"]) / ozet["Planlanan_Sure"]).round(4),
        0,
    )
    ozet["Kalite_Orani"] = np.where(
        (ozet["Uretim_Saat"] + ozet["Rework_Saat"]) > 0,
        (ozet["Uretim_Saat"] / (ozet["Uretim_Saat"] + ozet["Rework_Saat"])).round(4),
        0,
    )
    ozet["OEE_Yaklasik"] = (ozet["Kullanilabilirlik"] * ozet["Kalite_Orani"]).round(4)

    # ---- PERFORMANS ORANI (OEE'nin 3. bacağı) ----
    # Performans = Toplam Teorik (C/T) Süre / Toplam Gerçek Süre. Bu, klasik
    # "Gerçekleşen Miktar / Hedeflenen Miktar" formülünün MATEMATİKSEL
    # EŞDEĞERİDİR — burada "miktar" verisi (adet) YOK ama her satır zaten
    # tek bir işlemin C/T'sini taşıyor, dolayısıyla süre bazında hesaplamak
    # adet bazında hesaplamakla aynı sonucu verir (adet=1 varsayımıyla).
    #
    # ⚠️ ÖNEMLİ SINIRLAMA: Bu oran, referans C/T'nin GÜVENİLİR olduğu
    # varsayımına dayanır. `analytics`/CT-Emsal doğrulamasında (bkz.
    # compute_ct_guvenilirlik) gösterildiği gibi, katalog C/T bu veri
    # setinde muhtemelen GÜNCEL DEĞİL — operatörlerin ~%90'ı C/T'den "hızlı"
    # görünüyor. Bu yüzden Performans_Orani muhtemelen sistematik olarak
    # ŞİŞİRİLMİŞ (yüksek) çıkacaktır — "gerçekten performans yüksek" değil,
    # "standart eski/gevşek" anlamına gelebilir. Mutlak değerine değil,
    # TEZGAHLAR ARASI KIYASLAMASINA (hangisi göreceli düşük) güvenin.
    if "ÜRETİM_C/T" in df.columns:
        perf = df[df["Durum"] == "Üretim"].groupby("TEZGAH").apply(
            lambda g: pd.Series({
                "Teorik_Sure_Toplam": g["ÜRETİM_C/T"].fillna(0).sum(),
                "Gercek_Sure_Toplam": g["Sure_Saat"].sum(),
            }),
            include_groups=False,
        ).reset_index()
        perf["Performans_Orani"] = np.where(
            perf["Gercek_Sure_Toplam"] > 0,
            (perf["Teorik_Sure_Toplam"] / perf["Gercek_Sure_Toplam"]).round(4),
            0,
        )
        ozet = ozet.merge(perf[["TEZGAH", "Performans_Orani"]], on="TEZGAH", how="left")
        ozet["Gercek_OEE"] = (ozet["Kullanilabilirlik"] * ozet["Performans_Orani"] * ozet["Kalite_Orani"]).round(4)

    # Her tezgah için en büyük 2 plansız duruş kategorisini metne çevirip ekle
    # (örn. "Arıza / Ekipman Sorunu (4.3sa), Servis (2.5sa)") — "kullanılabilirlik
    # %94-99 diye her şey yolunda sanma" uyarısının somut karşılığı budur.
    if "Durus_Kategorisi" in df.columns:
        durus = df[df["Durum"] == "Plansız_Duruş"].dropna(subset=["Durus_Kategorisi"])
        if not durus.empty:
            kat_ozet = (
                durus.groupby(["TEZGAH", "Durus_Kategorisi"])["Sure_Saat"].sum()
                .reset_index().sort_values(["TEZGAH", "Sure_Saat"], ascending=[True, False])
            )
            en_buyuk_2 = (
                kat_ozet.groupby("TEZGAH").head(2)
                .groupby("TEZGAH")
                .apply(lambda g: ", ".join(f"{r.Durus_Kategorisi} ({r.Sure_Saat:.1f}sa)" for r in g.itertuples()),
                       include_groups=False)
                .rename("En_Buyuk_Kayip_Kategorileri")
                .reset_index()
            )
            ozet = ozet.merge(en_buyuk_2, on="TEZGAH", how="left")
    if "En_Buyuk_Kayip_Kategorileri" not in ozet.columns:
        ozet["En_Buyuk_Kayip_Kategorileri"] = None

    return ozet.sort_values("Uretim_Saat", ascending=False).reset_index(drop=True)


# ---------------------------------------------------------------------------
# C/T GÜVENİLİRLİK ANALİZİ (Emsal mi C/T mi doğru — kontrol)
# ---------------------------------------------------------------------------
def compute_ct_guvenilirlik(df: pd.DataFrame) -> dict:
    """Katalog C/T'nin bugünkü gerçek üretim hızını ne kadar iyi temsil
    ettiğini test eder. İki bulgu üretir:

    1) sistemsel_kayma: operatörlerin ortalama CT-bazlı sapması ile
       ortalama Emsal-bazlı sapması arasındaki fark. Sağlıklı bir C/T
       referansında bu fark küçük olmalı (yarı yarıya insan C/T'nin
       üstünde, yarısı altında olur, sistematik bir kayma OLMAMALI).
       Büyük ve TEK YÖNLÜ bir fark (örn. -18% vs -11%, hepsi negatif),
       C/T'nin güncel gerçek hıza göre YAVAŞ (gevşek/eski) kaldığının
       işaretidir.
    2) tezgah_agnostik_kodlar: aynı KOD & OPE.'nin FARKLI tezgahlarda C/T
       değeri hep AYNI ama gerçek medyan süresi tezgaha göre ciddi
       değiştiği kod listesi — yani C/T, hangi makinenin daha hızlı/yavaş
       olduğunu hiç hesaba katmıyor. Bu, operatörü değil, ATANDIĞI
       TEZGAHI ölçmüş oluyorsunuz demektir.
    """
    sonuc = {"gecerli": False}
    if df.empty or not all(c in df.columns for c in ["Durum", "ÜRETİM_C/T", "Sure_Saat", "KOD & OPE.", "TEZGAH", "Operator_Kod"]):
        return sonuc

    u = df[(df["Durum"] == "Üretim") & df["ÜRETİM_C/T"].notna() & (df["ÜRETİM_C/T"] > 0)].copy()
    if u.empty:
        return sonuc

    # --- 1) Sistemsel kayma: CT-sapma vs Emsal-sapma (operatör ortalaması) ---
    karne = compute_operator_karne(df, emsal_referans_df=df)
    kayma = None
    korelasyon = None
    negatif_ct_orani = None
    if not karne.empty and "CT_Bazli_Sapma_Yuzde" in karne.columns and "Emsal_Bazli_Sapma_Yuzde" in karne.columns:
        k = karne.dropna(subset=["CT_Bazli_Sapma_Yuzde", "Emsal_Bazli_Sapma_Yuzde"])
        if not k.empty:
            kayma = round(k["CT_Bazli_Sapma_Yuzde"].mean() - k["Emsal_Bazli_Sapma_Yuzde"].mean(), 1)
            korelasyon = round(k["CT_Bazli_Sapma_Yuzde"].corr(k["Emsal_Bazli_Sapma_Yuzde"]), 3) if len(k) > 2 else None
            negatif_ct_orani = round((k["CT_Bazli_Sapma_Yuzde"] < 0).mean() * 100, 1)

    # --- 2) Tezgah-agnostik kod tespiti ---
    grup = u.groupby(["KOD & OPE.", "TEZGAH"]).agg(
        n=("Sure_Saat", "size"), medyan_sure=("Sure_Saat", "median"), ct=("ÜRETİM_C/T", "median"),
    ).reset_index()
    grup = grup[grup["n"] >= config.EMSAL_MIN_TEKRAR]
    coklu_tezgah = grup.groupby("KOD & OPE.").filter(lambda g: g["TEZGAH"].nunique() >= 2)

    agnostik_kodlar = []
    for kod, alt in coklu_tezgah.groupby("KOD & OPE."):
        ct_sabit = alt["ct"].nunique() == 1
        sure_farki_yuzde = (alt["medyan_sure"].max() - alt["medyan_sure"].min()) / alt["medyan_sure"].min() * 100 if alt["medyan_sure"].min() > 0 else 0
        if ct_sabit and sure_farki_yuzde >= 15:  # C/T sabit ama gerçek süre tezgaha göre >=%15 degisiyor
            agnostik_kodlar.append({
                "KOD & OPE.": kod, "C/T (sabit)": alt["ct"].iloc[0],
                "En hızlı tezgah": alt.loc[alt["medyan_sure"].idxmin(), "TEZGAH"],
                "En hızlı medyan (sa)": round(alt["medyan_sure"].min(), 2),
                "En yavaş tezgah": alt.loc[alt["medyan_sure"].idxmax(), "TEZGAH"],
                "En yavaş medyan (sa)": round(alt["medyan_sure"].max(), 2),
                "Tezgahlar arası fark %": round(sure_farki_yuzde, 1),
            })
    agnostik_df = pd.DataFrame(agnostik_kodlar).sort_values("Tezgahlar arası fark %", ascending=False) if agnostik_kodlar else pd.DataFrame()

    sonuc.update({
        "gecerli": True,
        "sistemsel_kayma_puan": kayma,
        "korelasyon": korelasyon,
        "negatif_ct_sapma_orani": negatif_ct_orani,
        "operator_sayisi": len(karne) if not karne.empty else 0,
        "tezgah_agnostik_kodlar": agnostik_df,
    })
    return sonuc


# ---------------------------------------------------------------------------
# MTBF / MTTR (bakım mühendisliği KPI'ları)
# ---------------------------------------------------------------------------
def compute_mtbf_mttr(df: pd.DataFrame) -> pd.DataFrame:
    """Tezgah bazlı MTBF (Arızalar arası ortalama süre) ve MTTR (ortalama
    tamir süresi). Sadece 'Arıza / Ekipman Sorunu' kategorisindeki plansız
    duruşlar 'arıza' sayılır (Bekleme/Temizlik/İdari gibi kategoriler hariç
    — bunlar arıza değil, organizasyonel duruştur).

    MTTR = arıza kayıtlarının ortalama süresi (saat) — bakım ekibinin
           müdahale HIZINI ölçer.
    MTBF = (tezgahın toplam ÇALIŞIR DURUMDAKİ süresi) / (arıza sayısı)
           — tezgahın GÜVENİLİRLİĞİNİ ölçer. Çalışır durum = Üretim +
           Set-up + Rework (arıza HARİÇ her şey — tezgah "ayakta"ydı).

    ⚠️ SINIRLAMA: Veri saat-bazlı (en küçük birim ~5 dk) olay kayıtlarından
    geliyor, sürekli (saniye bazlı) makine-durumu logundan değil. Bu yüzden
    MTBF/MTTR burada YAKLAŞIKTIR — gerçek bir SCADA/PLC veri kaynağıyla
    hesaplanan değerlerden sapabilir, ama tezgahlar ARASI KIYASLAMA için
    (hangisi sık bozuluyor, hangisinde tamir uzun sürüyor) güvenilirdir.
    """
    gerekli = ["TEZGAH", "Durum", "Sure_Saat"]
    if df.empty or not all(c in df.columns for c in gerekli):
        return pd.DataFrame()

    ariza_kategori = "Arıza / Ekipman Sorunu"
    if "Durus_Kategorisi" in df.columns:
        ariza = df[(df["Durum"] == "Plansız_Duruş") & (df["Durus_Kategorisi"] == ariza_kategori)]
    else:
        ariza = df[df["Durum"] == "Plansız_Duruş"]

    calisir_durum = df[df["Durum"].isin(["Üretim", "Set_Up", "Rework"])]

    ariza_ozet = ariza.groupby("TEZGAH").agg(
        Ariza_Sayisi=("Sure_Saat", "count"), MTTR_Saat=("Sure_Saat", "mean"),
        Toplam_Ariza_Saat=("Sure_Saat", "sum"),
    ).reset_index()
    calisir_ozet = calisir_durum.groupby("TEZGAH")["Sure_Saat"].sum().rename("Calisir_Sure_Saat").reset_index()

    ozet = calisir_ozet.merge(ariza_ozet, on="TEZGAH", how="left")
    ozet[["Ariza_Sayisi", "MTTR_Saat", "Toplam_Ariza_Saat"]] = ozet[["Ariza_Sayisi", "MTTR_Saat", "Toplam_Ariza_Saat"]].fillna(0)
    ozet["MTBF_Saat"] = np.where(ozet["Ariza_Sayisi"] > 0, (ozet["Calisir_Sure_Saat"] / ozet["Ariza_Sayisi"]).round(1), np.nan)
    ozet["MTTR_Saat"] = ozet["MTTR_Saat"].round(2)

    return ozet.sort_values("Ariza_Sayisi", ascending=False).reset_index(drop=True)


# ---------------------------------------------------------------------------
# TAKIM / UÇ KAYNAKLI DURUŞ ANALİZİ
# ---------------------------------------------------------------------------
_TAKIM_KATEGORILERI = ["Takım/Ekipman Ayarı", "Bekleme (Takım/Malzeme)"]
_TAKIM_KIRILMA_ANAHTAR = ["INSERT KIRILMASI", "KIRILMASI"]


def compute_takim_analizi(df: pd.DataFrame) -> pd.DataFrame:
    """Parça/tezgah bazında takım/uç kaynaklı duruş süresini VE aynı
    parça/tezgahtaki Rework oranını bir arada gösterir — "bu parça sürekli
    rework'e giriyor ya da sık duruyorsa, sorun operatörden değil, o
    parçayı işleyen ucun/takımın kalitesinden kaynaklanıyor olabilir"
    hipotezini test etmek için. Takım kırılması özellikle ayrı sayılır
    (KOD & OPE. metninde "KIRILMASI" geçen satırlar).
    """
    gerekli = ["PARÇA NO_ÜRETİM_GİRİLEN", "TEZGAH", "Durum", "Sure_Saat"]
    if df.empty or not all(c in df.columns for c in gerekli):
        return pd.DataFrame()

    takim_durus = df[
        (df["Durum"] == "Plansız_Duruş") & df.get("Durus_Kategorisi", pd.Series(dtype=str)).isin(_TAKIM_KATEGORILERI)
    ]
    kirilma_mask = df["Durus_Sebebi_Ham"].astype(str).str.contains("|".join(_TAKIM_KIRILMA_ANAHTAR), na=False) if "Durus_Sebebi_Ham" in df.columns else pd.Series(False, index=df.index)
    kirilma = df[kirilma_mask]
    rework = df[df["Durum"] == "Rework"]

    grup_kolon = ["PARÇA NO_ÜRETİM_GİRİLEN", "TEZGAH"]
    a = takim_durus.groupby(grup_kolon)["Sure_Saat"].agg(Takim_Durus_Saat="sum", Takim_Durus_Adet="count").reset_index() if not takim_durus.empty else pd.DataFrame(columns=grup_kolon + ["Takim_Durus_Saat", "Takim_Durus_Adet"])
    b = kirilma.groupby(grup_kolon)["Sure_Saat"].agg(Kirilma_Saat="sum", Kirilma_Adet="count").reset_index() if not kirilma.empty else pd.DataFrame(columns=grup_kolon + ["Kirilma_Saat", "Kirilma_Adet"])
    c = rework.groupby(grup_kolon)["Sure_Saat"].agg(Rework_Saat="sum", Rework_Adet="count").reset_index() if not rework.empty else pd.DataFrame(columns=grup_kolon + ["Rework_Saat", "Rework_Adet"])

    from functools import reduce
    ozet = reduce(lambda l, r: l.merge(r, on=grup_kolon, how="outer"), [a, b, c])
    if ozet.empty:
        return pd.DataFrame()
    ozet = ozet.fillna(0)
    ozet["Toplam_Takim_Iliskili_Saat"] = ozet["Takim_Durus_Saat"] + ozet["Kirilma_Saat"] + ozet["Rework_Saat"]
    return ozet.sort_values("Toplam_Takim_Iliskili_Saat", ascending=False).reset_index(drop=True)


# ---------------------------------------------------------------------------
# MİKRO DURUŞ (CHOKOTEI) ADAYLARI — YAKLAŞIK/PROXY
# ---------------------------------------------------------------------------
def compute_mikro_durus_adaylari(df: pd.DataFrame, esik_dakika: float = 10.0) -> pd.DataFrame:
    """⚠️ BU BİR PROXY'DİR, GERÇEK MİKRO-DURUŞ TESPİTİ DEĞİLDİR.

    Gerçek "1-2 dakikalık chokotei" tespiti, saniye bazlı sürekli makine
    sinyali (PLC/SCADA) gerektirir — bu veri setinde en küçük zaman birimi
    ~5 dakikadır (satır bazlı, olay-sonu kaydı). Dolayısıyla GERÇEKTEN kısa
    (1-2 dk) mikro-duruşlar bu veride GÖRÜLEMEZ; sisteme hiç girilmemiş
    olabilirler ya da bir sonraki işlemin süresine "gizlenmiş" olabilirler.

    Bu fonksiyon bunun yerine en iyi ELİMİZDEKİ VERİYLE yapılabilecek
    yaklaşıklığı sunar: bir "Üretim" kaydı hem referans C/T'den HEM emsal
    medyanından belirgin şekilde (esik_dakika kadar) uzun sürmüş ama
    resmi bir "Plansız_Duruş" kaydı YOKSA, bu fazladan süre muhtemelen
    kayıt dışı kalmış küçük bir kesintiyi (mikro-duruş, sohbet, malzeme
    arama vb.) İÇİNDE BARINDIRIYOR olabilir. Kesin değil, sadece bir
    'inceleme adayı' listesidir.
    """
    gerekli = ["Durum", "Sure_Saat", "ÜRETİM_C/T", "KOD & OPE."]
    if df.empty or not all(c in df.columns for c in gerekli):
        return pd.DataFrame()

    u = df[(df["Durum"] == "Üretim") & df["ÜRETİM_C/T"].notna() & (df["ÜRETİM_C/T"] > 0)].copy()
    if u.empty:
        return pd.DataFrame()

    emsal_medyan = u.groupby("KOD & OPE.")["Sure_Saat"].transform("median")
    esik_saat = esik_dakika / 60
    u["Fazla_Sure_CT_Saat"] = (u["Sure_Saat"] - u["ÜRETİM_C/T"]).clip(lower=0)
    u["Fazla_Sure_Emsal_Saat"] = (u["Sure_Saat"] - emsal_medyan).clip(lower=0)

    aday = u[(u["Fazla_Sure_CT_Saat"] >= esik_saat) & (u["Fazla_Sure_Emsal_Saat"] >= esik_saat)].copy()
    if aday.empty:
        return pd.DataFrame()

    aday["Olasi_Gizli_Sure_Dk"] = (aday[["Fazla_Sure_CT_Saat", "Fazla_Sure_Emsal_Saat"]].min(axis=1) * 60).round(0)
    kolonlar = ["TARİH", "TEZGAH", "Operator_Kod", "KOD & OPE.", "Sure_Saat", "ÜRETİM_C/T", "Olasi_Gizli_Sure_Dk"]
    mevcut = [c for c in kolonlar if c in aday.columns]
    return aday[mevcut].sort_values("Olasi_Gizli_Sure_Dk", ascending=False).reset_index(drop=True)


# ---------------------------------------------------------------------------
# ZAMAN ÇİZELGESİ (GANTT) — bir günün/tezgahın saat saat akışı
# ---------------------------------------------------------------------------
def compute_zaman_cizelgesi(df: pd.DataFrame) -> pd.DataFrame:
    """Her satıra gerçek Baslangic_Zaman / Bitis_Zaman (datetime) ekler —
    Gantt chart ve kronolojik "pat diye gör" tabloları için gerekli.

    YÖNTEM (BAŞLANGIÇ_SAAT/BİTİŞ_SAAT saat metnini ayrıştırmak yerine
    ZİNCİRLEME kullanılır): bir (TEZGAH, TARİH, VARDİYA) grubundaki İLK
    kaydın başlangıcı TARİH + BAŞLANGIÇ_SAAT'e sabitlenir; sonraki her
    kaydın başlangıcı, bir ÖNCEKİ kaydın bitişine eşitlenir (bitiş =
    başlangıç + Sure_Saat). Bu, gece yarısını geçen vardiyalarda saat
    metnini yanlış yorumlama riskini TAMAMEN ortadan kaldırır — çünkü
    hiç "00:00'ı geçti mi" sorusu sormaya gerek kalmaz, süre zaten
    Sure_Saat'ten (zaten doğrulanmış) geliyor.

    ⚠️ SINIRLAMA: Kayıtlar arasında loglanmamış bir BOŞLUK varsa (bir olay
    hiç sisteme girilmemişse), bu noktadan sonraki saatler gerçek duvar
    saatinden kayabilir. Sıra/görece süre doğru kalır, sadece MUTLAK saat
    (örn. "tam 14:32'de") o an itibarıyla yaklaşık olabilir.
    """
    gerekli = ["TEZGAH", "TARİH", "VARDİYA", "BAŞLANGIÇ_SAAT", "Sure_Saat"]
    if df.empty or not all(c in df.columns for c in gerekli):
        return pd.DataFrame()

    df = df.copy()
    df["TARİH"] = pd.to_datetime(df["TARİH"])

    def _grup_zincirle(grup: pd.DataFrame) -> pd.DataFrame:
        grup = grup.copy()
        ilk = grup.iloc[0]
        try:
            h, m, s = map(int, str(ilk["BAŞLANGIÇ_SAAT"]).split(":"))
            su_an = ilk["TARİH"].normalize() + pd.Timedelta(hours=h, minutes=m, seconds=s)
        except Exception:
            su_an = ilk["TARİH"].normalize()

        baslangiclar, bitisler = [], []
        for _, row in grup.iterrows():
            baslangiclar.append(su_an)
            sure = row["Sure_Saat"] if pd.notna(row["Sure_Saat"]) else 0
            su_an = su_an + pd.Timedelta(hours=float(sure))
            bitisler.append(su_an)
        grup["Baslangic_Zaman"] = baslangiclar
        grup["Bitis_Zaman"] = bitisler
        return grup

    sonuc = df.groupby(["TEZGAH", "TARİH", "VARDİYA"], group_keys=False).apply(_grup_zincirle)
    return sonuc.sort_values(["TEZGAH", "Baslangic_Zaman"]).reset_index(drop=True)


# ---------------------------------------------------------------------------
# SET-UP — PARÇA KIRILIMI (kim, hangi parçanın set-up'ını, ne kadar yapmış)
# ---------------------------------------------------------------------------
def compute_setup_parca_kirilimi(df: pd.DataFrame) -> pd.DataFrame:
    """Operatör × Parça bazında set-up süresi/adedi. 'Cihan M92'de 3 kere,
    toplam 2.5 saat set-up yapmış, ortalama 50 dk' gibi soruları cevaplar."""
    gerekli = ["Durum", "Operator_Kod", "PARÇA NO_ÜRETİM_GİRİLEN", "Sure_Saat"]
    if df.empty or not all(c in df.columns for c in gerekli):
        return pd.DataFrame()

    setup = df[df["Durum"] == "Set_Up"].dropna(subset=["PARÇA NO_ÜRETİM_GİRİLEN"])
    if setup.empty:
        return pd.DataFrame()

    ozet = setup.groupby(["Operator_Kod", "PARÇA NO_ÜRETİM_GİRİLEN"]).agg(
        Toplam_Setup_Saat=("Sure_Saat", "sum"),
        Setup_Adet=("Sure_Saat", "count"),
        Ort_Setup_Dakika=("Sure_Saat", lambda s: round(s.mean() * 60, 1)),
    ).reset_index()
    return ozet.sort_values("Toplam_Setup_Saat", ascending=False).reset_index(drop=True)


# ---------------------------------------------------------------------------
# AYLIK ÖZET (tezgah / operatör / parça — ay bazında karşılaştırma)
# ---------------------------------------------------------------------------
def compute_aylik_ozet(df: pd.DataFrame, boyut: str = "TEZGAH") -> pd.DataFrame:
    """boyut: 'TEZGAH' | 'Operator_Kod' | 'PARÇA NO_ÜRETİM_GİRİLEN'.
    Her (Ay, boyut) için Üretim/Plansız Duruş/Set-up/Rework saatlerini ve
    Kullanılabilirlik %'sini özetler. Ay = TARİH'in yıl-ay'ı ('2026-08' gibi)."""
    if df.empty or "TARİH" not in df.columns or boyut not in df.columns:
        return pd.DataFrame()

    df = df.copy()
    df["Ay"] = pd.to_datetime(df["TARİH"]).dt.to_period("M").astype(str)

    ozet = df.groupby(["Ay", boyut]).apply(
        lambda g: pd.Series({
            "Uretim_Saat": g.loc[g["Durum"] == "Üretim", "Sure_Saat"].sum(),
            "Plansiz_Durus_Saat": g.loc[g["Durum"] == "Plansız_Duruş", "Sure_Saat"].sum(),
            "Set_Up_Saat": g.loc[g["Durum"] == "Set_Up", "Sure_Saat"].sum(),
            "Rework_Saat": g.loc[g["Durum"] == "Rework", "Sure_Saat"].sum(),
            "Kayit_Sayisi": len(g),
        }),
        include_groups=False,
    ).reset_index()
    planlanan = ozet["Uretim_Saat"] + ozet["Plansiz_Durus_Saat"] + ozet["Rework_Saat"]
    ozet["Kullanilabilirlik_%"] = ((ozet["Uretim_Saat"] + ozet["Rework_Saat"]) / planlanan * 100).where(planlanan > 0, 0).round(1)
    return ozet.sort_values(["Ay", "Uretim_Saat"], ascending=[True, False]).reset_index(drop=True)


# ---------------------------------------------------------------------------
# OPERATÖR KARNESİ
# ---------------------------------------------------------------------------
def compute_operator_karne(df: pd.DataFrame, emsal_referans_df: pd.DataFrame | None = None) -> pd.DataFrame:
    """Operatör bazlı performans karnesi: hacim, hız, referans C/T'ye göre
    sapma, emsallere (aynı işi yapan diğer operatörlere) göre sapma ve
    'düşük güvenilirlik' oranı (muhtemelen hatalı zaman girişi sinyali).

    df: HACİM ve HIZ buradan hesaplanır — dashboard'da seçili tarih
        aralığına göre filtrelenmiş veri (örn. sadece bu hafta).
    emsal_referans_df: Emsal medyanının ve "n>=EMSAL_MIN_TEKRAR" güvenilirlik
        eşiğinin hesaplanacağı referans veri. None ise df kullanılır (eski
        davranışla geriye dönük uyumlu).

    NEDEN AYRI PARAMETRE (gerçek veriyle doğrulanmış bug düzeltmesi):
    Emsal medyanını SADECE dar bir tarih filtresinden (örn. 1 hafta/1 gün)
    hesaplarsak, bir "KOD & OPE." o dar pencerede yeterli tekrara (n>=3)
    ulaşamayabilir — bu da çoğu operatörün emsal kıyaslamasından tamamen
    düşmesine ve kalan referansın istatistiksel olarak kararsız olmasına yol
    açar. Üretim Verimliliği sunumundaki doğrulanmış rakamlarla (örn. bir
    operatörün emsallerine göre "-%22.2" sapması) karşılaştırmalı test
    ettiğimde: emsal referansı TÜM (filtresiz) veriden hesaplanınca sonuç
    BİREBİR eşleşti; sadece o haftadan hesaplanınca sapma anlamlı ölçüde
    farklı çıktı. Bu yüzden emsal referansı için varsayılan olarak TÜM
    veriyi kullanmanız önerilir (bkz. dashboard sayfasındaki kullanım).
    """
    if emsal_referans_df is None:
        emsal_referans_df = df

    if df.empty or "Durum" not in df.columns or "Operator_Kod" not in df.columns:
        return pd.DataFrame()

    u = df[df["Durum"] == "Üretim"].copy()
    if u.empty or "KOD & OPE." not in u.columns:
        return pd.DataFrame()

    u_ref = emsal_referans_df[emsal_referans_df["Durum"] == "Üretim"].dropna(subset=["KOD & OPE.", "Sure_Saat"])
    kod_sayim = u_ref.groupby("KOD & OPE.").size()
    guvenilir_kodlar = kod_sayim[kod_sayim >= config.EMSAL_MIN_TEKRAR].index
    emsal_medyan = u_ref[u_ref["KOD & OPE."].isin(guvenilir_kodlar)].groupby("KOD & OPE.")["Sure_Saat"].median().rename("Emsal_Medyan_Saat")

    u_g = u[u["KOD & OPE."].isin(emsal_medyan.index)].dropna(subset=["Sure_Saat"]).copy()
    u_g = u_g.merge(emsal_medyan, on="KOD & OPE.")
    u_g["Emsal_Sapma_Yuzde"] = np.where(
        u_g["Emsal_Medyan_Saat"] > 0,
        ((u_g["Sure_Saat"] - u_g["Emsal_Medyan_Saat"]) / u_g["Emsal_Medyan_Saat"]) * 100,
        0,
    )

    hacim = u.groupby("Operator_Kod").agg(
        Toplam_Saat=("Sure_Saat", "sum"), Islem_Sayisi=("Sure_Saat", "count")
    ).reset_index()
    hacim["Islem_Saat_Hizi"] = np.where(
        hacim["Toplam_Saat"] > 0, hacim["Islem_Sayisi"] / hacim["Toplam_Saat"], 0
    )

    karne = hacim.copy()

    if "Sapma_Yuzdesi" in u.columns:
        ct_sapma = u[u["Sapma_Yuzdesi"].notna()].groupby("Operator_Kod")["Sapma_Yuzdesi"].mean().reset_index()
        ct_sapma.columns = ["Operator_Kod", "CT_Bazli_Sapma_Yuzde"]
        karne = karne.merge(ct_sapma, on="Operator_Kod", how="left")

    if "ÜRETİM_C/T" in u.columns:
        u = u.copy()
        u["CT_Dusuk_Guven_mi"] = u["Sure_Saat"] < (config.CT_DUSUK_GUVEN_ORANI * u["ÜRETİM_C/T"].fillna(0))
        dusuk_guven = u.groupby("Operator_Kod")["CT_Dusuk_Guven_mi"].sum().reset_index()
        dusuk_guven.columns = ["Operator_Kod", "CT_Dusuk_Guven_Satir_Sayisi"]
        karne = karne.merge(dusuk_guven, on="Operator_Kod", how="left")
        karne["CT_Dusuk_Guven_Satir_Sayisi"] = karne["CT_Dusuk_Guven_Satir_Sayisi"].fillna(0)
        karne["CT_Dusuk_Guven_Yuzde"] = np.where(
            karne["Islem_Sayisi"] > 0,
            (karne["CT_Dusuk_Guven_Satir_Sayisi"] / karne["Islem_Sayisi"] * 100).round(1),
            0,
        )

    if not u_g.empty:
        emsal_sapma = u_g.groupby("Operator_Kod")["Emsal_Sapma_Yuzde"].mean().reset_index()
        emsal_sapma.columns = ["Operator_Kod", "Emsal_Bazli_Sapma_Yuzde"]
        karne = karne.merge(emsal_sapma, on="Operator_Kod", how="left")

    # ---- DİNAMİK KARNE EŞİĞİ (gerçek veriyle kalibre edilmiş bug düzeltmesi) ----
    # config.KARNE_MIN_SAAT (varsayılan 50 saat) çok haftalık/aylık analizler
    # için tasarlanmış sabit bir eşiktir. Dashboard'da tarih aralığını tek bir
    # haftaya (hatta tek bir güne) daralttığınızda bu sabit eşik neredeyse
    # HERKESİ eler — 6 günlük gerçek test verisinde 19 aktif operatörden
    # sadece 3'ü 50 saati geçiyordu, oysa sunumda örnek verilen operatörlerin
    # hepsi haftalık 35-48 saat aralığındaydı. Eşiği, seçili aralığın gün
    # sayısına göre ölçekliyoruz: günde ortalama KARNE_MIN_SAAT_GUNLUK saat
    # (varsayılan 5 — bu veri setinde 6 günlük gerçek haftada 19 operatörden
    # 18'ini içeride tutup sadece gerçekten çok az çalışan 1 kişiyi dışarıda
    # bırakan, ampirik olarak kalibre edilmiş bir değer). Uzun aralıklarda
    # (örn. 2+ ay) eşik orijinal KARNE_MIN_SAAT'i aşmaz (min() ile sınırlanır).
    # NOT: config.py'de KARNE_MIN_SAAT_GUNLUK tanımlı değilse (henüz
    # eklenmediyse) varsayılan olarak 5 kullanılır, kod çökmez.
    karne_min_saat_gunluk = getattr(config, "KARNE_MIN_SAAT_GUNLUK", 5)
    if "TARİH" in df.columns and df["TARİH"].notna().any():
        gun_sayisi = df["TARİH"].dt.date.nunique()
    else:
        gun_sayisi = 30  # tarih yoksa eski (aylık) davranışa düş
    efektif_esik = min(config.KARNE_MIN_SAAT, karne_min_saat_gunluk * gun_sayisi)

    karne_guvenilir = karne[karne["Toplam_Saat"] >= efektif_esik].copy()
    dusuk_veri_sayisi = len(karne) - len(karne_guvenilir)
    if dusuk_veri_sayisi:
        log.info(
            "%d operatör, karneye girmek için gereken min. %g saatlik veriye sahip değil "
            "(seçili %d günlük aralık için efektif eşik: %g sa; karneden hariç tutuldu).",
            dusuk_veri_sayisi, efektif_esik, gun_sayisi, efektif_esik,
        )

    karne_guvenilir.attrs["efektif_esik_saat"] = efektif_esik
    karne_guvenilir.attrs["gun_sayisi"] = gun_sayisi
    return karne_guvenilir.sort_values("Toplam_Saat", ascending=False).reset_index(drop=True)


# ---------------------------------------------------------------------------
# SQLITE'A YAZ (yerel/offline fallback — DATABASE_URL tanımlı değilken
# db.py'nin ANA okuma kaynağı da budur; tanımlıyken sadece yedek/mirror'dur)
# ---------------------------------------------------------------------------
def _write_to_sqlite(detay: pd.DataFrame, tezgah_oee: pd.DataFrame, karne: pd.DataFrame):
    config.DB_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(config.DB_PATH)
    try:
        detay_yaz = _select_canonical(detay)
        # Tarih kolonunu SQLite'a yazmadan önce string'e çeviriyoruz (SQLite'ta
        # native datetime tipi yok, ISO metin olarak saklanır ve db.py bunu
        # geri parse eder).
        if "TARİH" in detay_yaz.columns:
            detay_yaz["TARİH"] = pd.to_datetime(detay_yaz["TARİH"]).dt.strftime("%Y-%m-%d")

        # BUG DÜZELTMESİ: eskiden if_exists="append" idi — Excel HER SEFERİNDE
        # baştan sona okunduğu için (incremental değil, tam dosya) bu her ETL
        # çalıştırmasında TÜM geçmiş veriyi bir daha ekleyip satırları
        # ikiye/üçe katlıyordu. Sadece bu çalıştırmanın kaynağı olan
        # 'excel' satırlarını silip yeniden yazıyoruz — ileride tabletten
        # gelen ('kaynak'='tablet') satırlar varsa onlara DOKUNULMAZ.
        table_exists = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name=?", (config.TABLE_NAME,)
        ).fetchone()
        if table_exists:
            conn.execute(f'DELETE FROM "{config.TABLE_NAME}" WHERE kaynak = ?', ("excel",))

        detay_yaz.to_sql(config.TABLE_NAME, conn, if_exists="append", index=False)
        conn.execute(f'CREATE INDEX IF NOT EXISTS idx_tarih ON "{config.TABLE_NAME}"("TARİH")')
        conn.execute(f'CREATE INDEX IF NOT EXISTS idx_tezgah ON "{config.TABLE_NAME}"("TEZGAH")')
        conn.execute(f'CREATE INDEX IF NOT EXISTS idx_operator ON "{config.TABLE_NAME}"("Operator_Kod")')

        tezgah_oee.to_sql(config.TEZGAH_OEE_TABLE, conn, if_exists="replace", index=False)
        karne.to_sql(config.OPERATOR_KARNE_TABLE, conn, if_exists="replace", index=False)

        conn.commit()
        log.info("SQLite'a yazıldı: %s", config.DB_PATH)
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# POSTGRES'E YAZ (Supabase/Neon vb. — DATABASE_URL tanımlıysa ANA kaynak)
# ---------------------------------------------------------------------------
def _write_to_postgres(detay: pd.DataFrame, tezgah_oee: pd.DataFrame, karne: pd.DataFrame):
    if not config.DATABASE_URL:
        log.info("DATABASE_URL tanımlı değil (.env dosyasına bakın) — Postgres yazma adımı atlanıyor, sadece SQLite kullanılacak.")
        return

    # sqlalchemy/psycopg2 SADECE Postgres kullanılacaksa gereklidir — bu
    # yüzden burada (fonksiyon içinde) import ediyoruz. Böylece sadece
    # SQLite kullanan biri (DATABASE_URL tanımsız) bu paketleri hiç kurmak
    # zorunda kalmaz, etl.py yine de çalışır.
    try:
        from sqlalchemy import create_engine, text
    except ImportError:
        log.error(
            "DATABASE_URL tanımlı ama 'sqlalchemy' (ve 'psycopg2-binary') kurulu değil. "
            "Kurulum için: pip install sqlalchemy psycopg2-binary — bu adım atlanıyor, sadece SQLite yazılacak."
        )
        return

    engine = create_engine(config.DATABASE_URL)

    try:
        with engine.begin() as conn:
            # --- ANA TABLOLARI DOLDURMA (FOREIGN KEY HATASINI ÖNLEMEK İÇİN) ---
            log.info("Yeni Tezgah ve Operatörler denetleniyor...")

            # A) TEZGAHLARI EKLE (Sadece veritabanında olmayanları ekler)
            if "TEZGAH" in detay.columns:
                unique_tezgahlar = detay["TEZGAH"].dropna().unique()
                for tezgah in unique_tezgahlar:
                    conn.execute(text(f"""
                        INSERT INTO {config.TEZGAHLAR_TABLE} (tezgah_kodu)
                        VALUES (:t)
                        ON CONFLICT (tezgah_kodu) DO NOTHING;
                    """), {"t": tezgah})

            # B) OPERATÖRLERİ EKLE (Sadece veritabanında olmayanları ekler).
            # GİZLİLİK: config.STORE_REAL_NAMES_IN_DB False ise (varsayılan),
            # bu ÜÇÜNCÜ TARAF bulut veritabanına GERÇEK isim YAZILMAZ —
            # isim_soyisim NULL bırakılır. Gerçek isim<->kod eşlemesi her
            # zaman yerelde config.OPERATOR_KOD_HARITASI_PATH dosyasındadır.
            if "Operator_Kod" in detay.columns and "OPERATÖR_norm" in detay.columns:
                unique_operatorler = detay[["Operator_Kod", "OPERATÖR_norm"]].dropna().drop_duplicates()
                for _, row in unique_operatorler.iterrows():
                    isim = row["OPERATÖR_norm"] if config.STORE_REAL_NAMES_IN_DB else None
                    conn.execute(text(f"""
                        INSERT INTO {config.OPERATORLER_TABLE} (operator_kod, isim_soyisim)
                        VALUES (:kod, :isim)
                        ON CONFLICT (operator_kod) DO NOTHING;
                    """), {"kod": row["Operator_Kod"], "isim": isim})

            # --- VERİYİ TEMİZLEME (FULL REFRESH — SADECE 'excel' KAYNAKLI SATIRLAR) ---
            # Excel her ETL çalıştırmasında BAŞTAN SONA okunduğu için, önceki
            # 'excel' kaynaklı satırları silip yeniden yazıyoruz. TRUNCATE
            # yerine DELETE ... WHERE kaynak='excel' kullanıyoruz ki ileride
            # tabletten (kaynak='tablet') gelecek satırlara DOKUNULMASIN.
            log.info("Eski 'excel' kaynaklı kayıtlar temizleniyor (Full-Refresh, tablet kayıtları korunur)...")
            conn.execute(text(f"DELETE FROM {config.TABLE_NAME} WHERE kaynak = 'excel';"))

            # --- ANA VERİYİ (kanonik kolonlar) YAZ ---
            detay_yaz = _select_canonical(detay)
            log.info("%d satırlık ana tablo Postgres'e yazılıyor...", len(detay_yaz))
            detay_yaz.to_sql(config.TABLE_NAME, conn, if_exists="append", index=False)

        # Özet tablolar: FK bağımlılığı yok, tam replace güvenli.
        tezgah_oee.to_sql(config.TEZGAH_OEE_TABLE, engine, if_exists="replace", index=False)
        karne.to_sql(config.OPERATOR_KARNE_TABLE, engine, if_exists="replace", index=False)

        log.info("🚀 Postgres güncellemesi tamamlandı.")

    except Exception as e:
        log.error("Postgres'e yazarken hata oluştu (SQLite yine de güncellenecek): %s", e)
# ---------------------------------------------------------------------------
# ANA AKIŞ
# ---------------------------------------------------------------------------
def run():
    log.info("=" * 60)
    log.info("ETL başladı. INCOMING_DIR = %s", config.INCOMING_DIR)

    files = _find_excel_files(config.INCOMING_DIR)
    if not files:
        log.warning("İşlenecek Excel dosyası bulunamadı. (%s klasörünü kontrol edin)", config.INCOMING_DIR)
        return

    log.info("%d dosya bulundu: %s", len(files), [f.name for f in files])

    frames = []
    for f in files:
        df = _read_one(f)
        if df is not None:
            frames.append(df)
            log.info("  -> %s: %d satır okundu", f.name, len(df))

    if not frames:
        log.error("Hiçbir dosyadan geçerli veri okunamadı. İşlem durduruldu, SQLite güncellenmedi.")
        return

    combined = pd.concat(frames, ignore_index=True)
    log.info("Birleştirilmiş ham veri: %d satır (%d dosyadan)", len(combined), len(frames))

    detay = _clean(combined)  # BUG DÜZELTMESİ: eskiden iki kere çağrılıyordu (gereksiz, sonucu atılıyordu)

    if detay.empty:
        log.error("Temizlik sonrası veri kalmadı! Veritabanı güncellenmedi. Kaynak dosyaları kontrol edin.")
        return

    tezgah_oee = compute_tezgah_oee(detay)
    operator_karne = compute_operator_karne(detay)

    _write_to_postgres(detay, tezgah_oee, operator_karne)  # DATABASE_URL yoksa kendi içinde no-op
    _write_to_sqlite(detay, tezgah_oee, operator_karne)

    log.info(
        "Bitti | backend: %s | detay: %d satır | tezgah_oee: %d tezgah | operator_karne: %d operatör",
        "Postgres+SQLite" if config.DATABASE_URL else "sadece SQLite", len(detay), len(tezgah_oee), len(operator_karne),
    )
    log.info("ETL tamamlandı. ✅")
    log.info("=" * 60)


if __name__ == "__main__":
    run()
