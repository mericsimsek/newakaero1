# -*- coding: utf-8 -*-
"""
common.py — Tüm sayfaların paylaştığı veri yükleme, filtreleme ve analiz
motoru. Gerçek `etl.py` / `temizlik_kurallari.py` çıktısıyla (production.db)
birebir uyumlu kolon isimleri kullanır:

    TARİH, TEZGAH, VARDİYA, BAŞLANGIÇ_SAAT, Vardiya_Uzunluk_Saat,
    Operator_Kod, Durum, Sure_Saat, "ÜRETİM_C/T", Sapma_Saat, Sapma_Yuzdesi,
    Durus_Sebebi_Ham, Durus_Kategorisi, "KOD & OPE.",
    "PARÇA NO_ÜRETİM_GİRİLEN", Haftanin_Gunu, Hafta_Sonu

Bu modüldeki compute_* fonksiyonları, gerçek etl.py dosyasındaki aynı isimli
fonksiyonların BİREBİR aynı mantığıdır (sadece config.py bağımlılığı yerine
aşağıdaki modül-seviyesi sabitler kullanılır — gerçek config.py'niz hazır
olduğunda bu sabitleri oradan okuyacak şekilde tek satırla değiştirebilirsiniz).
"""
from __future__ import annotations

import glob
import sqlite3
from pathlib import Path

import numpy as np
import pandas as pd
import streamlit as st

# =============================================================================
# GERÇEK config.py VARSA ORADAN OKU (TEK MERKEZİ AYAR DOSYASI) — yoksa (örn.
# dashboard'u tek başına/demo modda çalıştırıyorsanız) makul varsayılanlara düş.
# Bu sayede config.py'de bir eşiği değiştirdiğinizde dashboard da otomatik
# güncellenir; ayrı ayrı iki yerde aynı sayıyı bakımlamanız gerekmez.
# =============================================================================
try:
    import config as _cfg
except ImportError:
    _cfg = None

EMSAL_MIN_TEKRAR = getattr(_cfg, "EMSAL_MIN_TEKRAR", 3)
CT_DUSUK_GUVEN_ORANI = getattr(_cfg, "CT_DUSUK_GUVEN_ORANI", 0.5)
KARNE_MIN_SAAT = getattr(_cfg, "KARNE_MIN_SAAT", 50)
KARNE_MIN_SAAT_GUNLUK = getattr(_cfg, "KARNE_MIN_SAAT_GUNLUK", 5)
_CFG_TABLE_NAME = getattr(_cfg, "TABLE_NAME", None)
_CFG_DB_PATH = str(getattr(_cfg, "DB_PATH", "")) if _cfg is not None else None

DEFAULT_DB_ADAYLARI = [p for p in [_CFG_DB_PATH] if p] + [
    "production.db", "data/production.db", "db/production.db", "../production.db",
]

RENK = {
    "Üretim": "#2E7D32",
    "Plansız_Duruş": "#C62828",
    "Set_Up": "#F9A825",
    "Yemek_Mola": "#8D6E63",
    "Rework": "#EF6C00",
    "Sınıflandırılmamış": "#90A4AE",
    "Kayıt Dışı / Bilinmeyen": "#BDBDBD",
}
DURUM_SIRASI = ["Üretim", "Plansız_Duruş", "Set_Up", "Yemek_Mola", "Rework", "Sınıflandırılmamış"]


# =============================================================================
# METRİK SÖZLÜĞÜ — AI asistanı ve UI açıklamaları BUNDAN besleniyor.
# Her metrik: ad, formül (okunabilir), hangi kolonlardan türediği, kısa yorum.
# =============================================================================
METRIC_REGISTRY: dict[str, dict] = {
    "kullanilabilirlik": {
        "ad": "Kullanılabilirlik %",
        "formul": "(Üretim_Saat + Rework_Saat) / (Üretim_Saat + Plansız_Duruş_Saat + Rework_Saat) × 100",
        "kaynak_kolonlar": ["Durum", "Sure_Saat"],
        "aciklama": "Set-up ve yemek molası planlı/kaçınılmaz süre sayıldığı için paydaya girmez.",
    },
    "kalite_orani": {
        "ad": "Kalite Oranı %",
        "formul": "Üretim_Saat / (Üretim_Saat + Rework_Saat) × 100",
        "kaynak_kolonlar": ["Durum", "Sure_Saat"],
        "aciklama": "Fire adedi veride yok; Rework süresi kalite kaybının vekil (proxy) göstergesidir.",
    },
    "performans_orani": {
        "ad": "Performans Oranı",
        "formul": "Σ(ÜRETİM_C/T) / Σ(Sure_Saat)  [sadece Durum='Üretim' satırları]",
        "kaynak_kolonlar": ["ÜRETİM_C/T", "Sure_Saat", "Durum"],
        "aciklama": "1'den büyükse operatör/tezgah referans C/T'den hızlı demektir. ⚠️ Referans C/T güncel değilse şişebilir.",
    },
    "gercek_oee": {
        "ad": "Gerçek OEE (yaklaşık)",
        "formul": "Kullanılabilirlik × Performans_Oranı × Kalite_Oranı",
        "kaynak_kolonlar": ["Durum", "Sure_Saat", "ÜRETİM_C/T"],
        "aciklama": "Klasik OEE formülünün bu veri setine uyarlanmış hali.",
    },
    "calisma_orani_operator": {
        "ad": "Operatör Çalışma Oranı %",
        "formul": "Üretim_Saat / Toplam_Saat × 100  [operatör bazında]",
        "kaynak_kolonlar": ["Operator_Kod", "Durum", "Sure_Saat"],
        "aciklama": "Bir operatörün tüm kayıtlı zamanının ne kadarının fiili üretimde geçtiğini gösterir.",
    },
    "ct_bazli_sapma": {
        "ad": "CT Bazlı Sapma %",
        "formul": "(Sure_Saat − ÜRETİM_C/T) / ÜRETİM_C/T × 100  [satır bazlı, sonra operatör ortalaması]",
        "kaynak_kolonlar": ["Sure_Saat", "ÜRETİM_C/T"],
        "aciklama": "Negatifse operatör katalog C/T'den hızlı; pozitifse yavaş çalışıyor demektir.",
    },
    "emsal_bazli_sapma": {
        "ad": "Emsal Bazlı Sapma %",
        "formul": "(Sure_Saat − Emsal_Medyan_Saat) / Emsal_Medyan_Saat × 100",
        "kaynak_kolonlar": ["Sure_Saat", "KOD & OPE.", "Operator_Kod"],
        "aciklama": (
            "Emsal_Medyan_Saat = aynı 'KOD & OPE.' işini yapan TÜM operatörlerin medyan süresi "
            f"(sadece TÜM veride en az {EMSAL_MIN_TEKRAR} tekrarı olan kodlar için hesaplanır). "
            "Katalog C/T yerine gerçek emsal performansla kıyaslar."
        ),
    },
    "ct_dusuk_guven_yuzde": {
        "ad": "CT Düşük Güven %",
        "formul": f"COUNT(Sure_Saat < {CT_DUSUK_GUVEN_ORANI} × ÜRETİM_C/T) / Islem_Sayisi × 100",
        "kaynak_kolonlar": ["Sure_Saat", "ÜRETİM_C/T"],
        "aciklama": "Bir işlemin kataloğun yarısından bile kısa sürmesi, muhtemelen hatalı zaman girişidir.",
    },
    "mtbf": {
        "ad": "MTBF (saat)",
        "formul": "Çalışır_Sure_Saat / Ariza_Sayisi  [Çalışır = Üretim+Set_Up+Rework, Arıza = Durus_Kategorisi='Arıza / Ekipman Sorunu']",
        "kaynak_kolonlar": ["TEZGAH", "Durum", "Durus_Kategorisi", "Sure_Saat"],
        "aciklama": "Yüksek MTBF = tezgah nadiren bozuluyor (güvenilir).",
    },
    "mttr": {
        "ad": "MTTR (saat)",
        "formul": "MEAN(Sure_Saat)  [sadece Durus_Kategorisi='Arıza / Ekipman Sorunu' kayıtları]",
        "kaynak_kolonlar": ["TEZGAH", "Durus_Kategorisi", "Sure_Saat"],
        "aciklama": "Düşük MTTR = arıza hızlı gideriliyor.",
    },
    "pareto_kumulatif": {
        "ad": "Duruş Pareto Kümülatif %",
        "formul": "CUMSUM(Toplam_Saat) / SUM(Toplam_Saat) × 100  [Durus_Kategorisi bazında, büyükten küçüğe sıralı]",
        "kaynak_kolonlar": ["Durus_Kategorisi", "Sure_Saat", "Durum"],
        "aciklama": "80/20 kuralı: genelde ilk 2-3 kategori toplam kaybın çoğunu oluşturur.",
    },
    "vardiya_hata_orani": {
        "ad": "Vardiya İçi Çeyrek — Hata Oranı %",
        "formul": "Rework_Sayisi / (Üretim+Rework Sayisi) × 100  [Vardiya_Ici_Pozisyon_Yuzde'ye göre 4 çeyreğe bölünmüş]",
        "kaynak_kolonlar": ["Vardiya_Ici_Pozisyon_Yuzde", "Durum"],
        "aciklama": "Vardiyanın hangi diliminde (başı/ortası/sonu) hata oranının arttığını gösterir.",
    },
    "setup_ortalama": {
        "ad": "Ortalama Setup Süresi",
        "formul": "MEAN(Sure_Saat)  [Durum='Set_Up', tezgah veya operatör bazında gruplanmış]",
        "kaynak_kolonlar": ["Durum", "Sure_Saat", "TEZGAH", "Operator_Kod"],
        "aciklama": "SMED (hızlı kalıp değişimi) çalışmasının başlangıç noktasıdır.",
    },
    "firsat_maliyeti": {
        "ad": "Fırsat Maliyeti (saat)",
        "formul": "Islem_Sayisi × (Medyan_Saat − En_Hizli_Medyan_Saat) / 60  [aynı KOD & OPE. farklı tezgahlarda]",
        "kaynak_kolonlar": ["KOD & OPE.", "TEZGAH", "Sure_Saat"],
        "aciklama": "'Bu parça hep en hızlı tezgahta işlenseydi ne kadar saat kazanılırdı' sorusunun cevabı.",
    },
    "rework_orani": {
        "ad": "Rework Oranı %",
        "formul": "Rework_Saat / (Üretim_Saat + Rework_Saat) × 100",
        "kaynak_kolonlar": ["Durum", "Sure_Saat"],
        "aciklama": "Kalite_Orani'nin ayna görüntüsü (100 − Kalite_Orani).",
    },
    "calisma_orani_gunluk": {
        "ad": "Günlük/Vardiyalık Çalışma Oranı %",
        "formul": "Üretim_Saat / (Üretim_Saat + Plansız_Duruş_Saat + Set_Up_Saat + Yemek_Saat + Rework_Saat) × 100",
        "kaynak_kolonlar": ["Durum", "Sure_Saat"],
        "aciklama": "Kullanılabilirlik'ten farkı: burada Set-up ve yemek molası da paydaya dahildir (toplam takvim zamanına oran).",
    },
    "sistemsel_kayma": {
        "ad": "Sistemsel Kayma Puanı (CT güvenilirlik)",
        "formul": "ORT(CT_Bazli_Sapma_Yuzde) − ORT(Emsal_Bazli_Sapma_Yuzde)  [operatör bazında ortalama]",
        "kaynak_kolonlar": ["Sure_Saat", "ÜRETİM_C/T", "KOD & OPE.", "Operator_Kod"],
        "aciklama": (
            "Sıfıra yakınsa referans C/T günceldir. Büyük/negatifse C/T gerçek hızdan "
            "yavaş (gevşek/eski) kalmıştır — gerçek veride (puredata1.xlsx) bu puan −7.0, "
            "ve operatörlerin %95'i C/T'den hızlı görünüyor, bu da referansın güncellenmesi "
            "gerektiğine işaret ediyor."
        ),
    },
    "sure_tutarsizligi": {
        "ad": "Süre Tutarsızlığı (veri kalitesi)",
        "formul": "(Bitis_Zaman − Baslangic_Zaman) [saat] − Sure_Saat",
        "kaynak_kolonlar": ["BAŞLANGIÇ_SAAT", "BİTİŞ_SAAT", "Sure_Saat"],
        "aciklama": (
            "Sıfırdan farklıysa, kayıtlı BAŞLANGIÇ-BİTİŞ saat aralığı ile Sure_Saat kolonu "
            "uyuşmuyor demektir (gerçek veride satırların ~%18'inde görülüyor, genelde "
            "30dk-1sa fark — muhtemelen mola/kayıt dışı süre)."
        ),
    },
    "onerilen_ct": {
        "ad": "Önerilen C/T (CT sorununa çözüm)",
        "formul": "MEDYAN(Sure_Saat)  [aynı 'KOD & OPE.' için, TÜM operatörler, Durum='Üretim', n≥EMSAL_MIN_TEKRAR]",
        "kaynak_kolonlar": ["KOD & OPE.", "Sure_Saat", "Durum"],
        "aciklama": (
            "Katalog ÜRETİM_C/T güncel olmadığında (bkz. Sistemsel Kayma) kullanılacak, "
            "verinin kendisinden türetilmiş güvenilir bir alternatif referans. Fark_Yuzde "
            "≥%15 olan kodlarda katalog C/T'nin güncellenmesi önerilir."
        ),
    },
}


def metrik_aciklamasi(anahtar: str) -> str:
    """Bir metrik anahtarının markdown formatlı formül+açıklama metnini döndürür."""
    m = METRIC_REGISTRY.get(anahtar)
    if not m:
        return ""
    return f"**{m['ad']}**\n\n`{m['formul']}`\n\n{m['aciklama']}\n\n*Kaynak kolonlar: {', '.join(m['kaynak_kolonlar'])}*"


# =============================================================================
# VERİ KAYNAĞI — SQLite (gerçek ETL çıktısı) / yükleme / dummy veri
# =============================================================================
@st.cache_data(show_spinner=False)
def otomatik_db_bul() -> str | None:
    for aday in DEFAULT_DB_ADAYLARI:
        if Path(aday).exists():
            return aday
    bulunanlar = glob.glob("**/*.db", recursive=True)
    return bulunanlar[0] if bulunanlar else None


@st.cache_data(show_spinner=False)
def tablolari_listele(db_path: str) -> list[str]:
    conn = sqlite3.connect(db_path)
    try:
        return pd.read_sql(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'", conn
        )["name"].tolist()
    finally:
        conn.close()


def tabloyu_skorla(db_path: str, tablo: str) -> int:
    try:
        ornek = pd.read_sql(f'SELECT * FROM "{tablo}" LIMIT 3', sqlite3.connect(db_path))
    except Exception:
        return -1
    kritik = ["TEZGAH", "Durum", "Sure_Saat", "Operator_Kod", "TARİH"]
    return sum(k in ornek.columns for k in kritik)


def _vardiya_ici_pozisyon_hesapla(df: pd.DataFrame) -> pd.DataFrame:
    """BAŞLANGIÇ_SAAT + VARDİYA + Vardiya_Uzunluk_Saat varsa ama
    Vardiya_Ici_Pozisyon_Yuzde eksikse, temizlik_kurallari.vardiya_ici_pozisyon
    ile BİREBİR aynı mantıkla geriye dönük hesaplar (Vardiya Yorgunluk analizi
    bu kolona ihtiyaç duyar)."""
    if "Vardiya_Ici_Pozisyon_Yuzde" in df.columns:
        return df
    gerekli = ["VARDİYA", "BAŞLANGIÇ_SAAT", "Vardiya_Uzunluk_Saat"]
    if not all(c in df.columns for c in gerekli):
        return df

    def hesapla(row):
        v, bas, uzunluk = row.get("VARDİYA"), row.get("BAŞLANGIÇ_SAAT"), row.get("Vardiya_Uzunluk_Saat")
        if pd.isna(v) or pd.isna(bas) or pd.isna(uzunluk) or uzunluk == 0:
            return None
        try:
            vardiya_baslangic = str(v).split(" - ")[0]
            vh, vm = map(int, vardiya_baslangic.split(":"))
            bh, bm, *_ = str(bas).split(":")
            bh, bm = int(bh), int(bm)
            fark = (bh * 60 + bm) - (vh * 60 + vm)
            if fark < 0:
                fark += 24 * 60
            return round(fark / (uzunluk * 60) * 100, 1)
        except Exception:
            return None

    df = df.copy()
    df["Vardiya_Ici_Pozisyon_Yuzde"] = df.apply(hesapla, axis=1)
    return df


@st.cache_data(show_spinner="Veritabanından okunuyor...")
def sqlite_tablo_yukle(db_path: str, tablo: str) -> pd.DataFrame:
    conn = sqlite3.connect(db_path)
    try:
        df = pd.read_sql(f'SELECT * FROM "{tablo}"', conn)
    finally:
        conn.close()
    if "TARİH" in df.columns:
        df["TARİH"] = pd.to_datetime(df["TARİH"], errors="coerce")
    for c in ["Sure_Saat", "ÜRETİM_C/T", "Sapma_Saat", "Sapma_Yuzdesi", "Vardiya_Uzunluk_Saat", "Vardiya_Ici_Pozisyon_Yuzde"]:
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors="coerce")
    df = _vardiya_ici_pozisyon_hesapla(df)
    return df


@st.cache_data(show_spinner="Örnek veri üretiliyor...")
def ornek_veri_uret(n_gun: int = 21, seed: int = 42) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    tezgahlar = [f"AB-{i:02d}" for i in range(1, 9)]
    operatorler = [f"Op_{i:03d}" for i in range(1, 13)]
    parcalar = [f"P-{100 + i}" for i in range(0, 16)]
    durus_kategorileri = [
        "Arıza / Ekipman Sorunu", "Malzeme Bekleme", "Takım/Ekipman Ayarı",
        "Bekleme (Takım/Malzeme)", "İdari / Toplantı", "Temizlik",
        "Veri Girişi Hatası (Sayısal Kod)", "Diğer / Sınıflandırılmamış",
    ]
    operator_hiz_carpani = {op: rng.uniform(0.82, 1.22) for op in operatorler}
    tezgah_ariza_egilimi = {t: rng.uniform(0.5, 2.2) for t in tezgahlar}
    vardiyalar = ["07:00 - 19:00", "19:00 - 07:00"]

    satirlar = []
    baslangic_tarih = pd.Timestamp.now().normalize() - pd.Timedelta(days=n_gun)
    for gun_no in range(n_gun):
        tarih = baslangic_tarih + pd.Timedelta(days=gun_no)
        for tezgah in tezgahlar:
            for vardiya in vardiyalar:
                vardiya_baslangic_saat = 7 if vardiya.startswith("07") else 19
                dakika_ilerleme = 0
                operator = operatorler[rng.integers(0, len(operatorler))]
                n_islem = int(rng.integers(5, 12))
                for _ in range(n_islem):
                    durum = rng.choice(DURUM_SIRASI[:5], p=[0.66, 0.15, 0.08, 0.07, 0.04])
                    taban_sure = float(rng.gamma(2.0, 0.30)) if durum == "Üretim" else float(rng.gamma(1.2, 0.35))
                    if durum == "Plansız_Duruş":
                        taban_sure *= tezgah_ariza_egilimi[tezgah]
                    if durum == "Üretim":
                        taban_sure *= operator_hiz_carpani[operator]
                    sure_saat = float(np.clip(taban_sure, 0.05, 6.0))

                    baslangic_dk = vardiya_baslangic_saat * 60 + dakika_ilerleme
                    bh = int(baslangic_dk // 60) % 24
                    bm = int(baslangic_dk % 60)
                    bitis_dk = baslangic_dk + sure_saat * 60
                    eh = int(bitis_dk // 60) % 24
                    em = int(bitis_dk % 60)
                    parca = parcalar[rng.integers(0, len(parcalar))]

                    durus_kat, durus_ham = None, None
                    if durum == "Plansız_Duruş":
                        durus_kat = durus_kategorileri[rng.integers(0, len(durus_kategorileri))]
                        durus_ham = durus_kat

                    ct = np.nan
                    if durum == "Üretim":
                        ct = max(0.05, float(rng.normal(sure_saat / operator_hiz_carpani[operator], 0.04)))

                    satirlar.append({
                        "TARİH": tarih,
                        "TEZGAH": tezgah,
                        "VARDİYA": vardiya,
                        "Vardiya_Uzunluk_Saat": 12.0,
                        "BAŞLANGIÇ_SAAT": f"{bh:02d}:{bm:02d}",
                        "BİTİŞ_SAAT": f"{eh:02d}:{em:02d}",
                        "Operator_Kod": operator,
                        "KOD & OPE.": f"{parca}-OP{int(rng.integers(10, 90))}",
                        "PARÇA NO_ÜRETİM_GİRİLEN": parca,
                        "Durum": durum,
                        "Sure_Saat": round(sure_saat, 3),
                        "ÜRETİM_C/T": round(ct, 3) if not np.isnan(ct) else np.nan,
                        "Durus_Sebebi_Ham": durus_ham,
                        "Durus_Kategorisi": durus_kat,
                    })
                    dakika_ilerleme += sure_saat * 60

    df = pd.DataFrame(satirlar)
    df["TARİH"] = pd.to_datetime(df["TARİH"])
    df["Haftanin_Gunu"] = df["TARİH"].dt.day_name()
    df["Hafta_Sonu"] = df["TARİH"].dt.dayofweek.isin([5, 6]).astype(int)
    uretim_mask = (df["Durum"] == "Üretim") & df["ÜRETİM_C/T"].notna() & (df["ÜRETİM_C/T"] > 0)
    df["Sapma_Saat"] = np.where(uretim_mask, df["Sure_Saat"] - df["ÜRETİM_C/T"], np.nan)
    df["Sapma_Yuzdesi"] = np.where(uretim_mask, df["Sapma_Saat"] / df["ÜRETİM_C/T"] * 100, np.nan)
    df = _vardiya_ici_pozisyon_hesapla(df)
    return df


def veri_kaynagi_secici(anahtar_prefix: str = "vk") -> tuple[pd.DataFrame, str]:
    """Sidebar'da veri kaynağı seçtirir ve (df_ham, kaynak_notu) döndürür.
    Sonuç st.session_state'e yazılır ki diğer sayfalar aynı veriyi paylaşsın."""
    st.sidebar.title("⚙️ Veri Kaynağı")
    kaynak_secimi = st.sidebar.radio(
        "Veri nereden gelsin?",
        ["Otomatik: production.db bul", "SQLite dosyası yükle (.db)", "Excel/CSV yükle", "Örnek (demo) veri"],
        index=0, key=f"{anahtar_prefix}_kaynak",
    )

    df_ham, kaynak_notu = None, ""

    if kaynak_secimi == "Otomatik: production.db bul":
        db_path = otomatik_db_bul()
        if db_path:
            tablolar = tablolari_listele(db_path)
            skorlar = {t: tabloyu_skorla(db_path, t) for t in tablolar}
            # config.py'de TABLE_NAME tanımlıysa ve DB'de o tablo varsa, skora
            # bakmadan doğrudan onu öner (gerçek ETL çıktısıyla %100 örtüşür).
            if _CFG_TABLE_NAME and _CFG_TABLE_NAME in tablolar:
                en_iyi = _CFG_TABLE_NAME
            else:
                en_iyi = max(skorlar, key=skorlar.get) if skorlar else None
            if en_iyi and (skorlar.get(en_iyi, 0) >= 3 or en_iyi == _CFG_TABLE_NAME):
                secilen = st.sidebar.selectbox("Ana detay tablosu", tablolar, index=tablolar.index(en_iyi), key=f"{anahtar_prefix}_tablo")
                df_ham = sqlite_tablo_yukle(db_path, secilen)
                kaynak_notu = f"`{db_path}` — **{secilen}**"
            else:
                st.sidebar.warning("DB bulundu ama uygun tablo yok.")
        else:
            st.sidebar.info("`production.db` bulunamadı, örnek veri kullanılacak.")

    elif kaynak_secimi == "SQLite dosyası yükle (.db)":
        yuklenen = st.sidebar.file_uploader("SQLite (.db)", type=["db", "sqlite", "sqlite3"], key=f"{anahtar_prefix}_dbupload")
        if yuklenen is not None:
            gecici_yol = f"/tmp/{yuklenen.name}"
            with open(gecici_yol, "wb") as f:
                f.write(yuklenen.getbuffer())
            tablolar = tablolari_listele(gecici_yol)
            skorlar = {t: tabloyu_skorla(gecici_yol, t) for t in tablolar}
            en_iyi = max(skorlar, key=skorlar.get) if skorlar else None
            secilen = st.sidebar.selectbox("Ana detay tablosu", tablolar, index=tablolar.index(en_iyi) if en_iyi else 0, key=f"{anahtar_prefix}_tablo2")
            df_ham = sqlite_tablo_yukle(gecici_yol, secilen)
            kaynak_notu = f"Yüklenen dosya — **{secilen}**"

    elif kaynak_secimi == "Excel/CSV yükle":
        yuklenen = st.sidebar.file_uploader("Excel/CSV", type=["xlsx", "xls", "csv"], key=f"{anahtar_prefix}_fileupload")
        if yuklenen is not None:
            try:
                df_ham = pd.read_csv(yuklenen) if yuklenen.name.endswith(".csv") else pd.read_excel(yuklenen)
                if "TARİH" in df_ham.columns:
                    df_ham["TARİH"] = pd.to_datetime(df_ham["TARİH"], errors="coerce")
                df_ham = _vardiya_ici_pozisyon_hesapla(df_ham)
                kaynak_notu = f"Yüklenen dosya: **{yuklenen.name}**"
            except Exception as e:
                st.sidebar.error(f"Dosya okunamadı: {e}")
    else:
        df_ham = ornek_veri_uret()
        kaynak_notu = "🧪 **Örnek (demo) veri**"

    if df_ham is None or df_ham.empty:
        df_ham = ornek_veri_uret()
        kaynak_notu = "🧪 **Örnek (demo) veri** kullanılıyor."

    st.sidebar.caption(kaynak_notu)
    return df_ham, kaynak_notu


def filtrele(df_ham: pd.DataFrame, anahtar_prefix: str = "flt") -> pd.DataFrame:
    """Sidebar'da tarih/tezgah/operatör filtresi gösterir, filtrelenmiş df döner.
    Seçimler session_state'te tutulur ki sayfalar arası geçişte korunsun."""
    st.sidebar.markdown("---")
    st.sidebar.title("🔎 Filtreler")

    if "TARİH" in df_ham.columns and df_ham["TARİH"].notna().any():
        min_t, max_t = df_ham["TARİH"].min().date(), df_ham["TARİH"].max().date()
        tarih_araligi = st.sidebar.date_input("Tarih aralığı", value=(min_t, max_t), min_value=min_t, max_value=max_t, key=f"{anahtar_prefix}_tarih")
    else:
        tarih_araligi = None

    tezgah_secenekleri = sorted(df_ham["TEZGAH"].dropna().unique()) if "TEZGAH" in df_ham.columns else []
    secilen_tezgahlar = st.sidebar.multiselect("Tezgah", tezgah_secenekleri, default=tezgah_secenekleri, key=f"{anahtar_prefix}_tezgah")

    operator_secenekleri = sorted(df_ham["Operator_Kod"].dropna().unique()) if "Operator_Kod" in df_ham.columns else []
    secilen_operatorler = st.sidebar.multiselect("Operatör", operator_secenekleri, default=operator_secenekleri, key=f"{anahtar_prefix}_operator")

    df = df_ham.copy()
    if tarih_araligi and isinstance(tarih_araligi, tuple) and len(tarih_araligi) == 2:
        bas, son = tarih_araligi
        df = df[(df["TARİH"].dt.date >= bas) & (df["TARİH"].dt.date <= son)]
    if secilen_tezgahlar:
        df = df[df["TEZGAH"].isin(secilen_tezgahlar)]
    if secilen_operatorler and "Operator_Kod" in df.columns:
        df = df[df["Operator_Kod"].isin(secilen_operatorler)]

    st.sidebar.markdown("---")
    st.sidebar.caption(f"Filtre sonrası **{len(df):,}** satır")
    return df


def veri_yukle_ve_filtrele(anahtar_prefix: str = "p") -> tuple[pd.DataFrame, pd.DataFrame, str]:
    """Her sayfanın başında çağıracağı tek fonksiyon: (df_ham, df_filtreli, kaynak_notu)."""
    df_ham, kaynak_notu = veri_kaynagi_secici(anahtar_prefix)
    df = filtrele(df_ham, anahtar_prefix)
    return df_ham, df, kaynak_notu


def kolon_var(df: pd.DataFrame, *kolonlar) -> bool:
    return all(k in df.columns for k in kolonlar)


def _yuvarla(df: pd.DataFrame, **kolon_ondalik) -> pd.DataFrame:
    """Belirtilen kolonları verilen ondalık basamağa yuvarlar (varsa).
    Saat toplamları gibi değerler kesir dakikalardan (10dk=1/6sa=0.1666...,
    20dk=1/3sa=0.3333...) geldiği için float toplama sonunda uzun, çirkin
    ondalıklar (örn. 29.977777...) üretir — bu matematiksel olarak DOĞRU bir
    değerdir, veri bozukluğu değildir, ama ekranda gösterime uygun değildir.
    Her compute_* fonksiyonu, döndürdüğü DataFrame'i bu fonksiyondan
    geçirerek tutarlı bir gösterim hassasiyeti garanti eder."""
    df = df.copy()
    for kolon, ondalik in kolon_ondalik.items():
        if kolon in df.columns:
            df[kolon] = pd.to_numeric(df[kolon], errors="coerce").round(ondalik)
    return df


# =============================================================================
# HESAPLAMA FONKSİYONLARI — gerçek etl.py'deki compute_* fonksiyonlarının
# BİREBİR aynı mantığı (config.* yerine yukarıdaki modül sabitleri kullanılır)
# =============================================================================
def compute_tezgah_oee(df: pd.DataFrame) -> pd.DataFrame:
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
        }), include_groups=False,
    ).reset_index()

    ozet["Planlanan_Sure"] = ozet["Uretim_Saat"] + ozet["Plansiz_Durus_Saat"] + ozet["Rework_Saat"]
    ozet["Kullanilabilirlik"] = np.where(ozet["Planlanan_Sure"] > 0, ((ozet["Uretim_Saat"] + ozet["Rework_Saat"]) / ozet["Planlanan_Sure"]).round(4), 0)
    ozet["Kalite_Orani"] = np.where((ozet["Uretim_Saat"] + ozet["Rework_Saat"]) > 0, (ozet["Uretim_Saat"] / (ozet["Uretim_Saat"] + ozet["Rework_Saat"])).round(4), 0)
    ozet["OEE_Yaklasik"] = (ozet["Kullanilabilirlik"] * ozet["Kalite_Orani"]).round(4)

    if "ÜRETİM_C/T" in df.columns:
        perf = df[df["Durum"] == "Üretim"].groupby("TEZGAH").apply(
            lambda g: pd.Series({"Teorik_Sure_Toplam": g["ÜRETİM_C/T"].fillna(0).sum(), "Gercek_Sure_Toplam": g["Sure_Saat"].sum()}),
            include_groups=False,
        ).reset_index()
        perf["Performans_Orani"] = np.where(perf["Gercek_Sure_Toplam"] > 0, (perf["Teorik_Sure_Toplam"] / perf["Gercek_Sure_Toplam"]).round(4), 0)
        ozet = ozet.merge(perf[["TEZGAH", "Performans_Orani"]], on="TEZGAH", how="left")
        ozet["Gercek_OEE"] = (ozet["Kullanilabilirlik"] * ozet["Performans_Orani"] * ozet["Kalite_Orani"]).round(4)

    if "Durus_Kategorisi" in df.columns:
        durus = df[df["Durum"] == "Plansız_Duruş"].dropna(subset=["Durus_Kategorisi"])
        if not durus.empty:
            kat_ozet = durus.groupby(["TEZGAH", "Durus_Kategorisi"])["Sure_Saat"].sum().reset_index().sort_values(["TEZGAH", "Sure_Saat"], ascending=[True, False])
            en_buyuk_2 = (
                kat_ozet.groupby("TEZGAH").head(2).groupby("TEZGAH")
                .apply(lambda g: ", ".join(f"{r.Durus_Kategorisi} ({r.Sure_Saat:.1f}sa)" for r in g.itertuples()), include_groups=False)
                .rename("En_Buyuk_Kayip_Kategorileri").reset_index()
            )
            ozet = ozet.merge(en_buyuk_2, on="TEZGAH", how="left")
    if "En_Buyuk_Kayip_Kategorileri" not in ozet.columns:
        ozet["En_Buyuk_Kayip_Kategorileri"] = None
    ozet = _yuvarla(
        ozet, Uretim_Saat=2, Plansiz_Durus_Saat=2, Set_Up_Saat=2, Yemek_Saat=2, Rework_Saat=2,
        Planlanan_Sure=2, Kullanilabilirlik=4, Kalite_Orani=4, OEE_Yaklasik=4,
        Performans_Orani=4, Gercek_OEE=4,
    )
    return ozet.sort_values("Uretim_Saat", ascending=False).reset_index(drop=True)


def compute_operator_karne(df: pd.DataFrame, emsal_referans_df: pd.DataFrame | None = None) -> pd.DataFrame:
    if emsal_referans_df is None:
        emsal_referans_df = df
    if df.empty or "Durum" not in df.columns or "Operator_Kod" not in df.columns:
        return pd.DataFrame()
    u = df[df["Durum"] == "Üretim"].copy()
    if u.empty or "KOD & OPE." not in u.columns:
        return pd.DataFrame()

    u_ref = emsal_referans_df[emsal_referans_df["Durum"] == "Üretim"].dropna(subset=["KOD & OPE.", "Sure_Saat"])
    kod_sayim = u_ref.groupby("KOD & OPE.").size()
    guvenilir_kodlar = kod_sayim[kod_sayim >= EMSAL_MIN_TEKRAR].index
    emsal_medyan = u_ref[u_ref["KOD & OPE."].isin(guvenilir_kodlar)].groupby("KOD & OPE.")["Sure_Saat"].median().rename("Emsal_Medyan_Saat")

    u_g = u[u["KOD & OPE."].isin(emsal_medyan.index)].dropna(subset=["Sure_Saat"]).copy()
    u_g = u_g.merge(emsal_medyan, on="KOD & OPE.")
    u_g["Emsal_Sapma_Yuzde"] = np.where(u_g["Emsal_Medyan_Saat"] > 0, ((u_g["Sure_Saat"] - u_g["Emsal_Medyan_Saat"]) / u_g["Emsal_Medyan_Saat"]) * 100, 0)

    hacim = u.groupby("Operator_Kod").agg(Toplam_Saat=("Sure_Saat", "sum"), Islem_Sayisi=("Sure_Saat", "count")).reset_index()
    hacim["Islem_Saat_Hizi"] = np.where(hacim["Toplam_Saat"] > 0, hacim["Islem_Sayisi"] / hacim["Toplam_Saat"], 0)
    karne = hacim.copy()

    if "Sapma_Yuzdesi" in u.columns:
        ct_sapma = u[u["Sapma_Yuzdesi"].notna()].groupby("Operator_Kod")["Sapma_Yuzdesi"].mean().reset_index()
        ct_sapma.columns = ["Operator_Kod", "CT_Bazli_Sapma_Yuzde"]
        karne = karne.merge(ct_sapma, on="Operator_Kod", how="left")

    if "ÜRETİM_C/T" in u.columns:
        u = u.copy()
        u["CT_Dusuk_Guven_mi"] = u["Sure_Saat"] < (CT_DUSUK_GUVEN_ORANI * u["ÜRETİM_C/T"].fillna(0))
        dusuk_guven = u.groupby("Operator_Kod")["CT_Dusuk_Guven_mi"].sum().reset_index()
        dusuk_guven.columns = ["Operator_Kod", "CT_Dusuk_Guven_Satir_Sayisi"]
        karne = karne.merge(dusuk_guven, on="Operator_Kod", how="left")
        karne["CT_Dusuk_Guven_Satir_Sayisi"] = karne["CT_Dusuk_Guven_Satir_Sayisi"].fillna(0)
        karne["CT_Dusuk_Guven_Yuzde"] = np.where(karne["Islem_Sayisi"] > 0, (karne["CT_Dusuk_Guven_Satir_Sayisi"] / karne["Islem_Sayisi"] * 100).round(1), 0)

    if not u_g.empty:
        emsal_sapma = u_g.groupby("Operator_Kod")["Emsal_Sapma_Yuzde"].mean().reset_index()
        emsal_sapma.columns = ["Operator_Kod", "Emsal_Bazli_Sapma_Yuzde"]
        karne = karne.merge(emsal_sapma, on="Operator_Kod", how="left")

    if "TARİH" in df.columns and df["TARİH"].notna().any():
        gun_sayisi = df["TARİH"].dt.date.nunique()
    else:
        gun_sayisi = 30
    efektif_esik = min(KARNE_MIN_SAAT, KARNE_MIN_SAAT_GUNLUK * gun_sayisi)
    karne_guvenilir = karne[karne["Toplam_Saat"] >= efektif_esik].copy()
    karne_guvenilir = _yuvarla(
        karne_guvenilir, Toplam_Saat=2, Islem_Saat_Hizi=3,
        CT_Bazli_Sapma_Yuzde=1, Emsal_Bazli_Sapma_Yuzde=1,
    )
    karne_guvenilir.attrs["efektif_esik_saat"] = efektif_esik
    karne_guvenilir.attrs["gun_sayisi"] = gun_sayisi
    return karne_guvenilir.sort_values("Toplam_Saat", ascending=False).reset_index(drop=True)


def compute_ct_guvenilirlik(df: pd.DataFrame) -> dict:
    """Katalog ÜRETİM_C/T'nin bugünkü gerçek üretim hızını ne kadar iyi
    temsil ettiğini test eder (gerçek puredata1.xlsx ile doğrulandı).

    1) sistemsel_kayma_puan: operatörlerin ortalama CT-bazlı sapması ile
       ortalama Emsal-bazlı sapması arasındaki fark. Sağlıklı bir C/T
       referansında bu küçük olmalı. Büyük ve TEK YÖNLÜ bir fark, C/T'nin
       güncel gerçek hıza göre YAVAŞ (gevşek/eski) kaldığının işaretidir.
    2) negatif_ct_sapma_orani: operatörlerin yüzde kaçının CT'den "hızlı"
       göründüğü. %50 civarı sağlıklıdır; %90+ ise referans C/T şüphelidir.
    3) tezgah_agnostik_kodlar: aynı KOD & OPE.'nin FARKLI tezgahlarda C/T
       değeri hep AYNI ama gerçek medyan süresi tezgaha göre ciddi
       değiştiği kodlar — yani C/T, hangi makinenin daha hızlı/yavaş
       olduğunu hiç hesaba katmıyor.

    GERÇEK VERİ SONUCU (puredata1.xlsx, tüm dönem): negatif_ct_sapma_orani
    %95, sistemsel_kayma_puan −7.0 — yani operatörlerin neredeyse tamamı
    kataloğun "önünde" görünüyor. Bu, muhtemelen operatörlerin gerçekten
    çok hızlı olduğu anlamına DEĞİL, ÜRETİM_C/T referansının güncellenmesi
    gerektiği anlamına gelir. Performans_Orani / Gerçek_OEE değerlerini
    yorumlarken bu uyarıyı dikkate alın.
    """
    sonuc = {"gecerli": False}
    gerekli = ["Durum", "ÜRETİM_C/T", "Sure_Saat", "KOD & OPE.", "TEZGAH", "Operator_Kod"]
    if df.empty or not all(c in df.columns for c in gerekli):
        return sonuc

    u = df[(df["Durum"] == "Üretim") & df["ÜRETİM_C/T"].notna() & (df["ÜRETİM_C/T"] > 0)].copy()
    if u.empty:
        return sonuc

    karne = compute_operator_karne(df, emsal_referans_df=df)
    kayma = korelasyon = negatif_ct_orani = None
    if not karne.empty and "CT_Bazli_Sapma_Yuzde" in karne.columns and "Emsal_Bazli_Sapma_Yuzde" in karne.columns:
        k = karne.dropna(subset=["CT_Bazli_Sapma_Yuzde", "Emsal_Bazli_Sapma_Yuzde"])
        if not k.empty:
            kayma = round(k["CT_Bazli_Sapma_Yuzde"].mean() - k["Emsal_Bazli_Sapma_Yuzde"].mean(), 1)
            korelasyon = round(k["CT_Bazli_Sapma_Yuzde"].corr(k["Emsal_Bazli_Sapma_Yuzde"]), 3) if len(k) > 2 else None
            negatif_ct_orani = round((k["CT_Bazli_Sapma_Yuzde"] < 0).mean() * 100, 1)

    grup = u.groupby(["KOD & OPE.", "TEZGAH"]).agg(
        n=("Sure_Saat", "size"), medyan_sure=("Sure_Saat", "median"), ct=("ÜRETİM_C/T", "median"),
    ).reset_index()
    grup = grup[grup["n"] >= EMSAL_MIN_TEKRAR]
    coklu_tezgah = grup.groupby("KOD & OPE.").filter(lambda g: g["TEZGAH"].nunique() >= 2)

    agnostik_kodlar = []
    for kod, alt in coklu_tezgah.groupby("KOD & OPE."):
        ct_sabit = alt["ct"].nunique() == 1
        sure_min = alt["medyan_sure"].min()
        sure_farki_yuzde = (alt["medyan_sure"].max() - sure_min) / sure_min * 100 if sure_min > 0 else 0
        if ct_sabit and sure_farki_yuzde >= 15:
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


def compute_onerilen_ct(df: pd.DataFrame) -> pd.DataFrame:
    """CT GÜVENİLİRLİK SORUNUNA SOMUT ÇÖZÜM: katalog ÜRETİM_C/T yerine, her
    'KOD & OPE.' için TÜM operatörlerin gerçek fiili sürelerinin medyanından
    türetilen 'Onerilen_CT_Saat' hesaplar (zaten Emsal_Medyan_Saat'te
    kullanılan aynı güvenilir yöntem: medyan, aykırı değerlere dayanıklıdır).

    Sadece n >= EMSAL_MIN_TEKRAR tekrarı olan kodlar için öneri üretilir
    (az veriyle istatistik güvenilmez olur). Fark_Yuzde ≥ %15 ise (mutlak
    değer) 'Guncelleme_Onerisi' True olur — bu kodun katalog C/T'sinin
    muhtemelen güncellenmesi gerektiği anlamına gelir.
    """
    gerekli = ["Durum", "KOD & OPE.", "Sure_Saat", "ÜRETİM_C/T"]
    if df.empty or not all(c in df.columns for c in gerekli):
        return pd.DataFrame()
    u = df[df["Durum"] == "Üretim"].dropna(subset=["KOD & OPE.", "Sure_Saat"])
    if u.empty:
        return pd.DataFrame()

    grup = u.groupby("KOD & OPE.").agg(
        Onerilen_CT_Saat=("Sure_Saat", "median"),
        Islem_Sayisi=("Sure_Saat", "size"),
        Katalog_CT_Saat=("ÜRETİM_C/T", "median"),
    ).reset_index()
    grup = grup[grup["Islem_Sayisi"] >= EMSAL_MIN_TEKRAR].copy()
    if grup.empty:
        return pd.DataFrame()

    grup["Fark_Yuzde"] = np.where(
        grup["Katalog_CT_Saat"] > 0,
        (grup["Onerilen_CT_Saat"] - grup["Katalog_CT_Saat"]) / grup["Katalog_CT_Saat"] * 100,
        np.nan,
    )
    grup["Guncelleme_Onerisi"] = grup["Fark_Yuzde"].abs() >= 15
    grup = _yuvarla(grup, Onerilen_CT_Saat=2, Katalog_CT_Saat=2, Fark_Yuzde=1)
    return grup.reindex(grup["Fark_Yuzde"].abs().sort_values(ascending=False).index).reset_index(drop=True)


def compute_ct_cozum_etkisi(df: pd.DataFrame) -> dict:
    """compute_onerilen_ct()'nin ÖNERDİĞİ değerleri katalog C/T yerine
    KULLANSAYDIK sistemsel kayma ne kadar düzelirdi? sorusunu sayısal
    olarak cevaplar — 'teşhis'ten 'çözümün etkisini ölçmeye' geçiş.
    negatif_ct_sapma_orani, compute_ct_guvenilirlik()'teki ile AYNI mantıkla
    ama Onerilen_CT_Saat referans alınarak yeniden hesaplanır. Sağlıklı bir
    referansta bu oranın ~%50'ye yaklaşması beklenir (yarı hızlı/yarı yavaş)."""
    onerilen = compute_onerilen_ct(df)
    if onerilen.empty or not kolon_var(df, "Durum", "KOD & OPE.", "Sure_Saat"):
        return {"gecerli": False}
    harita = onerilen.set_index("KOD & OPE.")["Onerilen_CT_Saat"]
    u = df[df["Durum"] == "Üretim"].dropna(subset=["KOD & OPE.", "Sure_Saat"]).copy()
    u["Onerilen_CT_Saat"] = u["KOD & OPE."].map(harita)
    u = u.dropna(subset=["Onerilen_CT_Saat"])
    if u.empty:
        return {"gecerli": False}
    u["Yeni_Sapma_Yuzde"] = (u["Sure_Saat"] - u["Onerilen_CT_Saat"]) / u["Onerilen_CT_Saat"] * 100
    return {
        "gecerli": True,
        "kapsanan_islem_sayisi": int(len(u)),
        "kapsanan_kod_sayisi": int(onerilen.shape[0]),
        "yeni_negatif_ct_sapma_orani": round((u["Yeni_Sapma_Yuzde"] < 0).mean() * 100, 1),
        "yeni_ortalama_sapma": round(u["Yeni_Sapma_Yuzde"].mean(), 1),
    }


def compute_pareto(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty or not kolon_var(df, "Durum", "Durus_Kategorisi", "Sure_Saat"):
        return pd.DataFrame()
    d = df[df["Durum"] == "Plansız_Duruş"].dropna(subset=["Durus_Kategorisi"])
    if d.empty:
        return pd.DataFrame()
    ozet = d.groupby("Durus_Kategorisi")["Sure_Saat"].agg(Toplam_Saat="sum", Adet="count").reset_index()
    ozet = ozet.sort_values("Toplam_Saat", ascending=False).reset_index(drop=True)
    ozet["Kumulatif_%"] = (ozet["Toplam_Saat"].cumsum() / ozet["Toplam_Saat"].sum() * 100).round(1)
    return _yuvarla(ozet, Toplam_Saat=2)


def compute_vardiya_yorgunluk(df: pd.DataFrame) -> pd.DataFrame:
    gerekli = ["Vardiya_Ici_Pozisyon_Yuzde", "Durum"]
    if df.empty or not all(c in df.columns for c in gerekli):
        return pd.DataFrame()
    ilgili = df[df["Durum"].isin(["Üretim", "Rework"]) & df["Vardiya_Ici_Pozisyon_Yuzde"].notna()].copy()
    ilgili = ilgili[ilgili["Vardiya_Ici_Pozisyon_Yuzde"].between(0, 100)]
    if ilgili.empty:
        return pd.DataFrame()
    ilgili["Dilim"] = pd.cut(
        ilgili["Vardiya_Ici_Pozisyon_Yuzde"], bins=[0, 25, 50, 75, 100],
        labels=["1. Çeyrek (0-25%)", "2. Çeyrek (25-50%)", "3. Çeyrek (50-75%)", "4. Çeyrek (75-100%)"],
        include_lowest=True,
    )
    ozet = ilgili.groupby("Dilim", observed=True).apply(
        lambda g: pd.Series({"Toplam_Islem": len(g), "Hata_Sayisi": int((g["Durum"] == "Rework").sum())}), include_groups=False,
    ).reset_index()
    ozet["Hata_Orani_%"] = np.where(ozet["Toplam_Islem"] > 0, (ozet["Hata_Sayisi"] / ozet["Toplam_Islem"] * 100).round(2), 0.0)
    return ozet


def compute_mtbf_mttr(df: pd.DataFrame) -> pd.DataFrame:
    gerekli = ["TEZGAH", "Durum", "Sure_Saat"]
    if df.empty or not all(c in df.columns for c in gerekli):
        return pd.DataFrame()
    ariza_kategori = "Arıza / Ekipman Sorunu"
    if "Durus_Kategorisi" in df.columns:
        ariza = df[(df["Durum"] == "Plansız_Duruş") & (df["Durus_Kategorisi"] == ariza_kategori)]
    else:
        ariza = df[df["Durum"] == "Plansız_Duruş"]
    calisir_durum = df[df["Durum"].isin(["Üretim", "Set_Up", "Rework"])]
    ariza_ozet = ariza.groupby("TEZGAH").agg(Ariza_Sayisi=("Sure_Saat", "count"), MTTR_Saat=("Sure_Saat", "mean"), Toplam_Ariza_Saat=("Sure_Saat", "sum")).reset_index()
    calisir_ozet = calisir_durum.groupby("TEZGAH")["Sure_Saat"].sum().rename("Calisir_Sure_Saat").reset_index()
    ozet = calisir_ozet.merge(ariza_ozet, on="TEZGAH", how="left")
    ozet[["Ariza_Sayisi", "MTTR_Saat", "Toplam_Ariza_Saat"]] = ozet[["Ariza_Sayisi", "MTTR_Saat", "Toplam_Ariza_Saat"]].fillna(0)
    ozet["MTBF_Saat"] = np.where(ozet["Ariza_Sayisi"] > 0, (ozet["Calisir_Sure_Saat"] / ozet["Ariza_Sayisi"]).round(1), np.nan)
    ozet["MTTR_Saat"] = ozet["MTTR_Saat"].round(2)
    ozet = _yuvarla(ozet, Calisir_Sure_Saat=2, Toplam_Ariza_Saat=2)
    return ozet.sort_values("Ariza_Sayisi", ascending=False).reset_index(drop=True)


def compute_zaman_cizelgesi(df: pd.DataFrame) -> pd.DataFrame:
    """Her satıra gerçek Baslangic_Zaman / Bitis_Zaman (datetime) ekler.

    YÖNTEM (gerçek puredata1.xlsx ile doğrulanmış, ZİNCİRLEME KULLANMAZ):
    Veride hem BAŞLANGIÇ_SAAT hem gerçek BİTİŞ_SAAT var. Her satırın zamanı
    KENDİ satırından, TAMAMEN BAĞIMSIZ olarak çözülür — bir önceki satıra
    bağımlı değildir, bu yüzden kayıt boşluklarında (loglanmamış olaylarda)
    KAYMA RİSKİ YOKTUR:
      1. Vardiyanın kendi başlangıç saati (örn. "19:00 - 07:00" -> 19:00)
         referans alınır.
      2. BAŞLANGIÇ_SAAT, vardiya başlangıcından KÜÇÜKSE (örn. 02:00 < 19:00),
         bu saat aslında bir SONRAKİ takvim gününe aittir (gece yarısını
         geçen vardiya) -> TARİH + 1 gün.
      3. BİTİŞ_SAAT, kendi BAŞLANGIÇ_SAAT'inden KÜÇÜKSE (gece yarısını o
         satırın içinde geçmişse), bitiş +1 gün daha alınır.
      4. BİTİŞ_SAAT eksikse (nadiren), Sure_Saat ile tamamlanır (yedek).

    DOĞRULAMA: puredata1.xlsx'teki 2652 satırın TAMAMI hatasız çözüldü, sıfır
    negatif süre, sıfır >16 saatlik tekil olay üretti; AB-06/2026-08-01
    vardiyası elle karşılaştırılıp doğrulandı (gece yarısı geçişi dahil).

    VERİ KALİTESİ BULGUSU: BİTİŞ−BAŞLANGIÇ farkı ile Sure_Saat kolonu
    satırların ~%18.5'inde birebir uyuşmuyor (çoğunlukla 30dk-1sa fark —
    muhtemelen mola/kayıt dışı zaman BAŞLANGIÇ/BİTİŞ'e dahil ama Sure_Saat'e
    dahil değil). Bu fark her satır için `Sure_Tutarsizligi_Saat` kolonunda
    ayrıca hesaplanır ki UI'da şeffaf şekilde gösterilebilsin — SESSİZCE
    yutulmaz.
    """
    gerekli = ["TEZGAH", "TARİH", "VARDİYA", "BAŞLANGIÇ_SAAT", "Sure_Saat"]
    if df.empty or not all(c in df.columns for c in gerekli):
        return pd.DataFrame()
    df = df.copy()
    df["TARİH"] = pd.to_datetime(df["TARİH"])
    bitis_var = "BİTİŞ_SAAT" in df.columns

    def _parse_hm(s):
        try:
            parcalar = str(s).split(":")
            h, m = int(parcalar[0]), int(parcalar[1])
            sn = int(parcalar[2]) if len(parcalar) > 2 else 0
            return h * 3600 + m * 60 + sn
        except Exception:
            return None

    def _vardiya_baslangic_saniye(v):
        try:
            ilk = str(v).split(" - ")[0]
            h, m = map(int, ilk.split(":"))
            return h * 3600 + m * 60
        except Exception:
            return 0

    def _satir_zamanlari(row):
        gun_baz = row["TARİH"].normalize()
        vb = _vardiya_baslangic_saniye(row["VARDİYA"])
        bas_sn = _parse_hm(row["BAŞLANGIÇ_SAAT"])
        if bas_sn is None:
            return pd.NaT, pd.NaT
        bas_gun_ofset = 1 if bas_sn < vb else 0
        baslangic = gun_baz + pd.Timedelta(days=bas_gun_ofset, seconds=bas_sn)

        bit_sn = _parse_hm(row["BİTİŞ_SAAT"]) if bitis_var else None
        if bit_sn is None:
            sure = row["Sure_Saat"] if pd.notna(row["Sure_Saat"]) else 0
            bitis = baslangic + pd.Timedelta(hours=float(sure))
        else:
            bit_gun_ofset = bas_gun_ofset + (1 if bit_sn < bas_sn else 0)
            bitis = gun_baz + pd.Timedelta(days=bit_gun_ofset, seconds=bit_sn)
        return baslangic, bitis

    zamanlar = df.apply(_satir_zamanlari, axis=1, result_type="expand")
    df["Baslangic_Zaman"], df["Bitis_Zaman"] = zamanlar[0], zamanlar[1]
    df["Sure_Tutarsizligi_Saat"] = (
        (df["Bitis_Zaman"] - df["Baslangic_Zaman"]).dt.total_seconds() / 3600 - df["Sure_Saat"]
    ).round(2)
    # Gösterim amaçlı çıktı: satır bazlı türev kolonlar okunabilir hassasiyete yuvarlanır
    # (asıl hesaplar bu fonksiyonun çıktısından değil, ham df'den yapılır).
    df = _yuvarla(df, Sure_Saat=2, Sapma_Saat=2, Sapma_Yuzdesi=1, **{"ÜRETİM_SÜRESİ": 2, "İMALAT YAPILAN SÜRE-1": 2, "SET-UP_SÜRESİ": 2, "YEMEK_MOLA_SÜRESİ": 2, "ÜRETİM_C/T": 2})

    return df.sort_values(["TEZGAH", "Baslangic_Zaman"]).reset_index(drop=True)


def compute_bosluklar(zaman_df: pd.DataFrame, esik_dakika: float = 1.0) -> pd.DataFrame:
    """compute_zaman_cizelgesi() çıktısını alır; her TEZGAH için ardışık
    olaylar arasında KAYIT DIŞI (loglanmamış) boşlukları tespit eder.

    'Her vakti dakikayı bilmeliyiz' gereksinimi: Gantt'ta sadece LOGLANMIŞ
    olaylar görünür, ama bir tezgah iki kayıt arasında hiçbir şey
    loglanmadan durmuş/beklemiş olabilir — bu fonksiyon o görünmez
    boşlukları da bulup "Kayıt Dışı / Bilinmeyen" bir sözde-Durum olarak
    üretir ki Gantt'a eklenince günün TAMAMI (24 saat) hesaba katılmış olsun.

    esik_dakika: bu süreden kısa boşluklar (yuvarlama farkları) gösterilmez.
    """
    if zaman_df.empty or not all(c in zaman_df.columns for c in ["TEZGAH", "Baslangic_Zaman", "Bitis_Zaman"]):
        return pd.DataFrame()

    satirlar = []
    for tezgah, grup in zaman_df.sort_values("Baslangic_Zaman").groupby("TEZGAH"):
        onceki_bitis = None
        onceki_op = None
        for _, row in grup.iterrows():
            if onceki_bitis is not None and row["Baslangic_Zaman"] > onceki_bitis:
                bosluk_dk = (row["Baslangic_Zaman"] - onceki_bitis).total_seconds() / 60
                if bosluk_dk >= esik_dakika:
                    satirlar.append({
                        "TEZGAH": tezgah,
                        "Baslangic_Zaman": onceki_bitis,
                        "Bitis_Zaman": row["Baslangic_Zaman"],
                        "Durum": "Kayıt Dışı / Bilinmeyen",
                        "Sure_Saat": round(bosluk_dk / 60, 3),
                        "Onceki_Operator": onceki_op,
                        "Sonraki_Operator": row.get("Operator_Kod"),
                    })
            onceki_bitis = max(onceki_bitis, row["Bitis_Zaman"]) if onceki_bitis is not None else row["Bitis_Zaman"]
            onceki_op = row.get("Operator_Kod")
    return pd.DataFrame(satirlar).pipe(lambda d: _yuvarla(d, Sure_Saat=2) if not d.empty else d)


def compute_setup_parca_kirilimi(df: pd.DataFrame) -> pd.DataFrame:
    """Operatör × Parça bazında set-up süresi/adedi. 'Cihan M92'de 3 kere,
    toplam 2.5 saat set-up yapmış, ortalama 50 dk' gibi soruları cevaplar.

    NOT (gerçek veriyle bulunan düzeltme): PARÇA NO_ÜRETİM_GİRİLEN boş olan
    satırlar (gerçek veride 47 Set_Up kaydından 2'si — örn. bir tezgahtan
    diğerine "DÖNÜŞ" gibi parçasız geçiş kayıtları) eskiden dropna ile
    SESSİZCE atılıyordu. Artık atılmıyor: parça kodu boşsa, onun yerine
    "KOD & OPE." metni (varsa) kullanılıyor; o da yoksa "(Parçasız/Diğer)"
    ile işaretlenip TABLODA GÖRÜNÜR kalıyor — hiçbir setup kaydı kaybolmaz.
    """
    gerekli = ["Durum", "Operator_Kod", "PARÇA NO_ÜRETİM_GİRİLEN", "Sure_Saat"]
    if df.empty or not all(c in df.columns for c in gerekli):
        return pd.DataFrame()
    setup = df[df["Durum"] == "Set_Up"].copy()
    if setup.empty:
        return pd.DataFrame()

    parca_kolonu = setup["PARÇA NO_ÜRETİM_GİRİLEN"]
    if "KOD & OPE." in setup.columns:
        parca_kolonu = parca_kolonu.fillna(setup["KOD & OPE."])
    setup["PARÇA NO_ÜRETİM_GİRİLEN"] = parca_kolonu.fillna("(Parçasız/Diğer)")

    ozet = setup.groupby(["Operator_Kod", "PARÇA NO_ÜRETİM_GİRİLEN"]).agg(
        Toplam_Setup_Saat=("Sure_Saat", "sum"), Setup_Adet=("Sure_Saat", "count"),
        Ort_Setup_Dakika=("Sure_Saat", lambda s: round(s.mean() * 60, 1)),
    ).reset_index()
    ozet = _yuvarla(ozet, Toplam_Setup_Saat=2)
    return ozet.sort_values("Toplam_Setup_Saat", ascending=False).reset_index(drop=True)


def compute_aylik_ozet(df: pd.DataFrame, boyut: str = "TEZGAH") -> pd.DataFrame:
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
        }), include_groups=False,
    ).reset_index()
    planlanan = ozet["Uretim_Saat"] + ozet["Plansiz_Durus_Saat"] + ozet["Rework_Saat"]
    ozet["Kullanilabilirlik_%"] = ((ozet["Uretim_Saat"] + ozet["Rework_Saat"]) / planlanan * 100).where(planlanan > 0, 0).round(1)
    ozet = _yuvarla(ozet, Uretim_Saat=2, Plansiz_Durus_Saat=2, Set_Up_Saat=2, Rework_Saat=2)
    return ozet.sort_values(["Ay", "Uretim_Saat"], ascending=[True, False]).reset_index(drop=True)


def compute_heatmap(df: pd.DataFrame) -> pd.DataFrame:
    parca_kolonu = "PARÇA NO_ÜRETİM_GİRİLEN" if "PARÇA NO_ÜRETİM_GİRİLEN" in df.columns else None
    if df.empty or parca_kolonu is None or not kolon_var(df, "TEZGAH", "Durum", "Sure_Saat"):
        return pd.DataFrame()
    d = df[df["Durum"].isin(["Plansız_Duruş", "Rework"])]
    if d.empty:
        return pd.DataFrame()
    piv = d.pivot_table(index=parca_kolonu, columns="TEZGAH", values="Sure_Saat", aggfunc="sum", fill_value=0.0)
    piv["_toplam"] = piv.sum(axis=1)
    piv = piv.sort_values("_toplam", ascending=False).head(20).drop(columns="_toplam")
    return piv.round(2)


# =============================================================================
# SUNUM ↔ HESAP DOĞRULAMA (Uretim_Verimliligi_Sunumu: 31 Ağustos - 5 Eylül)
# =============================================================================
# Aşağıdaki hedef değerler sunumdaki gömülü chart/tablo verilerinden ve
# kullanıcının paylaştığı ham tezgah tablosundan alınmıştır. Amaç: dashboard'un
# hesapları sunumla ne kadar örtüşüyor, TEK TEK görebilmek.
SUNUM_TEZGAH = {
    # tezgah: (Kullanilabilirlik %, Plansız kayıp saat, Üretim saat (ham tablo))
    "AB-03": (99.4, 0.8, 117.25), "AB-08": (99.1, 0.7, 81.00), "AB-07": (99.1, 0.9, 98.33),
    "AB-06": (98.0, 2.5, 125.58), "AB-11": (97.2, 2.7, 95.00), "AB-09": (96.8, 3.5, 105.00),
    "AB-02": (96.6, 4.3, 123.17), "AB-10": (94.2, 6.2, 102.00),
}
SUNUM_TEZGAH_SETUP_SAAT = {  # kullanıcının ham tablosundaki Set_Up_Saat
    "AB-10": 0.0, "AB-02": 2.0, "AB-09": 0.5, "AB-11": 0.0, "AB-06": 0.0, "AB-07": 5.5, "AB-08": 0.0, "AB-03": 0.0,
}
SUNUM_OPERATOR_EMSAL = {  # normalize isim: hedef Emsal sapma %
    "MEHMET EMRE KARAKAS": 9.7, "MUHAMMED KORKMAZ": 9.6, "ASKIN SAKALLI": 8.0,
    "HAMZA KARAASLAN": 2.8, "YASAR SENOL": -4.5, "MEHMET ALI COLAK": -14.4,
    "MESUT ARDIC": -15.1, "NURULLAH ZENGIN": -20.8, "HUSEYIN YENICE": -21.7,
    "NAZIF GUCLUDAG": -22.2,
}
SUNUM_SETUP = [  # (normalize isim, KOD & OPE. ipucu, dakika)
    ("YASAR SENOL", "M01G03", 135), ("NURULLAH ZENGIN", "M13P02 70", 105),
    ("MEHMET EMRE KARAKAS", "M92", 90), ("NAZIF GUCLUDAG", "T170045", 60),
    ("MUHAMMED KORKMAZ", "M01G03", 30), ("CIHAN SARIGUL", "M14P04", 30),
    ("BERAT TUNC", "60-SETUP", 30),
]
SUNUM_SETUP_FABRIKA_ORT_DK = 69
SUNUM_REWORK = ("NAZIF GUCLUDAG", "AB-07", 2.5)
SUNUM_VARDIYA = [1.63, 2.96, 2.78, 4.63]
SUNUM_TOPLAM_KAYIP_SAAT = 21.75


def _operator_isim_kod_haritasi() -> dict:
    """config.OPERATOR_KOD_HARITASI_PATH (isim -> kod) dosyasını okur; yoksa boş."""
    import json
    try:
        yol = getattr(_cfg, "OPERATOR_KOD_HARITASI_PATH", None)
        if yol is not None and Path(yol).exists():
            with open(yol, "r", encoding="utf-8") as f:
                return json.load(f)
    except Exception:
        pass
    for aday in ["data/operator_kod_haritasi.json", "operator_kod_haritasi.json"]:
        if Path(aday).exists():
            import json as _j
            with open(aday, "r", encoding="utf-8") as f:
                return _j.load(f)
    return {}


def _durum_isareti(fark, tolerans):
    if fark is None or pd.isna(fark):
        return "❓"
    return "✅" if abs(fark) <= tolerans else "⚠️"


def sunum_karsilastirma(df_tum: pd.DataFrame, bas: str = "2026-08-31", bit: str = "2026-09-05") -> dict:
    """Sunumdaki her sayıyı, aynı tarih penceresi için canlı hesapla karşılaştırır.
    Döner: {'tezgah','operator','setup','rework','vardiya','ozet'} DataFrame'leri
    (+ 'pencere_satir'). Emsal referansı TÜM veridir (sunumla aynı metodoloji)."""
    sonuc: dict = {}
    if df_tum.empty or "TARİH" not in df_tum.columns:
        return sonuc
    hafta = df_tum[(df_tum["TARİH"] >= pd.Timestamp(bas)) & (df_tum["TARİH"] <= pd.Timestamp(bit))].copy()
    sonuc["pencere_satir"] = len(hafta)
    if hafta.empty:
        return sonuc
    harita = _operator_isim_kod_haritasi()

    # ---- 1) TEZGAH ----
    t = compute_tezgah_oee(hafta).set_index("TEZGAH")
    satirlar = []
    for tezgah, (h_kul, h_plan, h_uretim) in SUNUM_TEZGAH.items():
        if tezgah not in t.index:
            satirlar.append({"Tezgah": tezgah, "Not": "Pencerede yok"})
            continue
        r = t.loc[tezgah]
        b_kul = round(float(r["Kullanilabilirlik"]) * 100, 2)
        satirlar.append({
            "Tezgah": tezgah,
            "Kullanılabilirlik % (sunum)": h_kul, "Kullanılabilirlik % (bizim)": b_kul,
            "Fark (puan)": round(b_kul - h_kul, 2), "Durum KUL": _durum_isareti(b_kul - h_kul, 0.6),
            "Plansız kayıp sa (sunum)": h_plan, "Plansız kayıp sa (bizim)": round(float(r["Plansiz_Durus_Saat"]), 2),
            "Fark (sa)": round(float(r["Plansiz_Durus_Saat"]) - h_plan, 2),
            "Durum PLN": _durum_isareti(float(r["Plansiz_Durus_Saat"]) - h_plan, 0.8),
            "Üretim sa (ham tablo)": h_uretim, "Üretim sa (bizim)": round(float(r["Uretim_Saat"]), 2),
            "Fark Üretim (sa)": round(float(r["Uretim_Saat"]) - h_uretim, 2),
            "Set-up sa (ham tablo)": SUNUM_TEZGAH_SETUP_SAAT.get(tezgah),
            "Set-up sa (bizim)": round(float(r["Set_Up_Saat"]), 2),
            "Fark Set-up (sa)": round(float(r["Set_Up_Saat"]) - SUNUM_TEZGAH_SETUP_SAAT.get(tezgah, 0), 2),
        })
    sonuc["tezgah"] = pd.DataFrame(satirlar)
    sonuc["toplam_kayip_bizim"] = round(float(t["Plansiz_Durus_Saat"].sum() + t["Rework_Saat"].sum()), 2)

    # ---- 2) OPERATÖR (emsal referansı = tüm veri) ----
    k = compute_operator_karne(hafta, emsal_referans_df=df_tum)
    satirlar = []
    for isim, h in SUNUM_OPERATOR_EMSAL.items():
        kod = harita.get(isim)
        b = None
        if kod is not None and not k.empty and kod in set(k["Operator_Kod"]):
            b = float(k.loc[k["Operator_Kod"] == kod, "Emsal_Bazli_Sapma_Yuzde"].iloc[0])
        satirlar.append({
            "Operatör (isim)": isim, "Kod": kod, "Emsal sapma % (sunum)": h,
            "Emsal sapma % (bizim)": None if b is None else round(b, 1),
            "Fark (puan)": None if b is None else round(b - h, 1),
            "Durum": _durum_isareti(None if b is None else b - h, 2.0),
        })
    op_df = pd.DataFrame(satirlar)
    if op_df["Emsal sapma % (bizim)"].notna().sum() >= 3:
        op_df["Sıra (sunum)"] = op_df["Emsal sapma % (sunum)"].rank(ascending=False).astype(int)
        op_df["Sıra (bizim)"] = op_df["Emsal sapma % (bizim)"].rank(ascending=False, na_option="bottom").astype(int)
    sonuc["operator"] = op_df

    # ---- 3) SETUP ----
    su = hafta[hafta["Durum"] == "Set_Up"].copy()
    su["dk"] = (su["Sure_Saat"] * 60).round(0)
    tum_su = df_tum[df_tum["Durum"] == "Set_Up"].copy()
    tum_su["dk"] = (tum_su["Sure_Saat"] * 60).round(0)
    satirlar = []
    for isim, ipucu, dk in SUNUM_SETUP:
        kod = harita.get(isim)
        aday = su[(su["Operator_Kod"] == kod) & ((su["dk"] - dk).abs() <= 1)] if kod else su.iloc[0:0]
        if not aday.empty:
            a = aday.iloc[0]
            satirlar.append({"Operatör": isim, "Sunum dk": dk, "Bizim dk": float(a["dk"]), "Tarih": a["TARİH"].date(),
                             "Tezgah": a["TEZGAH"], "KOD & OPE.": a["KOD & OPE."], "Durum": "✅ Pencerede bulundu"})
        else:
            dis = tum_su[(tum_su["Operator_Kod"] == kod) & ((tum_su["dk"] - dk).abs() <= 1)] if kod else tum_su.iloc[0:0]
            if not dis.empty:
                a = dis.iloc[0]
                satirlar.append({"Operatör": isim, "Sunum dk": dk, "Bizim dk": float(a["dk"]), "Tarih": a["TARİH"].date(),
                                 "Tezgah": a["TEZGAH"], "KOD & OPE.": a["KOD & OPE."],
                                 "Durum": "⚠️ Aynı kayıt var ama pencere DIŞINDA"})
            else:
                satirlar.append({"Operatör": isim, "Sunum dk": dk, "Bizim dk": None, "Tarih": None,
                                 "Tezgah": None, "KOD & OPE.": None, "Durum": "❌ Hiç bulunamadı"})
    sonuc["setup"] = pd.DataFrame(satirlar)
    sonuc["setup_ort_bizim_dk"] = round(float(su["dk"].mean()), 1) if not su.empty else None
    sonuc["setup_adet_bizim"] = int(len(su))

    # ---- 4) REWORK ----
    rw = hafta[hafta["Durum"] == "Rework"]
    if rw.empty:
        sonuc["rework"] = pd.DataFrame()
    else:
        g = rw.groupby(["Operator_Kod", "TEZGAH"])["Sure_Saat"].agg(Saat="sum", Adet="count").reset_index()
        g["Saat"] = g["Saat"].round(2)
        ters = {v: k for k, v in harita.items()}
        g.insert(0, "Operatör (isim)", g["Operator_Kod"].map(ters))
        sonuc["rework"] = g

    # ---- 5) VARDİYA YORGUNLUK (pencere + tam dönem) ----
    v_pen = compute_vardiya_yorgunluk(hafta)
    v_tum = compute_vardiya_yorgunluk(df_tum)
    satirlar = []
    for i, h in enumerate(SUNUM_VARDIYA):
        satirlar.append({
            "Dilim": f"{i + 1}. Çeyrek", "Sunum %": h,
            "Bizim % (pencere)": float(v_pen["Hata_Orani_%"].iloc[i]) if len(v_pen) > i else None,
            "Bizim % (tam dönem)": float(v_tum["Hata_Orani_%"].iloc[i]) if len(v_tum) > i else None,
        })
    sonuc["vardiya"] = pd.DataFrame(satirlar)
    return sonuc
