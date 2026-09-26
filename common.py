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
    karne_guvenilir.attrs["efektif_esik_saat"] = efektif_esik
    karne_guvenilir.attrs["gun_sayisi"] = gun_sayisi
    return karne_guvenilir.sort_values("Toplam_Saat", ascending=False).reset_index(drop=True)


def compute_pareto(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty or not kolon_var(df, "Durum", "Durus_Kategorisi", "Sure_Saat"):
        return pd.DataFrame()
    d = df[df["Durum"] == "Plansız_Duruş"].dropna(subset=["Durus_Kategorisi"])
    if d.empty:
        return pd.DataFrame()
    ozet = d.groupby("Durus_Kategorisi")["Sure_Saat"].agg(Toplam_Saat="sum", Adet="count").reset_index()
    ozet = ozet.sort_values("Toplam_Saat", ascending=False).reset_index(drop=True)
    ozet["Kumulatif_%"] = (ozet["Toplam_Saat"].cumsum() / ozet["Toplam_Saat"].sum() * 100).round(1)
    return ozet


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
    return ozet.sort_values("Ariza_Sayisi", ascending=False).reset_index(drop=True)


def compute_zaman_cizelgesi(df: pd.DataFrame) -> pd.DataFrame:
    """Her satıra gerçek Baslangic_Zaman / Bitis_Zaman (datetime) ekler.
    YÖNTEM: bir (TEZGAH, TARİH, VARDİYA) grubundaki ilk kayıt TARİH+BAŞLANGIÇ_SAAT'e
    sabitlenir; sonraki her kayıt bir öncekinin bitişinden başlar (zincirleme)."""
    gerekli = ["TEZGAH", "TARİH", "VARDİYA", "BAŞLANGIÇ_SAAT", "Sure_Saat"]
    if df.empty or not all(c in df.columns for c in gerekli):
        return pd.DataFrame()
    df = df.copy()
    df["TARİH"] = pd.to_datetime(df["TARİH"])

    def _grup_zincirle(grup: pd.DataFrame) -> pd.DataFrame:
        grup = grup.copy()
        ilk = grup.iloc[0]
        try:
            parcalar = str(ilk["BAŞLANGIÇ_SAAT"]).split(":")
            h, m = int(parcalar[0]), int(parcalar[1])
            s = int(parcalar[2]) if len(parcalar) > 2 else 0
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

    # NOT: df.groupby(...).apply(fn) yerine BİLEREK klasik for-döngüsü kullanılıyor.
    # pandas 2.2+/3.0'da groupby().apply(), gruplama kolonlarını (TEZGAH/TARİH/VARDİYA)
    # varsayılan olarak fonksiyona geçirilen alt-DataFrame'den ÇIKARIYOR
    # (include_groups davranış değişikliği) — _grup_zincirle ise TARİH kolonuna
    # ihtiyaç duyduğu için bu, pandas sürümüne göre değişen bir KeyError'a yol
    # açıyordu. for name, grup in df.groupby(...) döngüsü her pandas sürümünde
    # gruplama kolonlarını dahil eder, bu yüzden sürümden bağımsız güvenilirdir.
    df_sirali = df.sort_values(["TEZGAH", "TARİH", "VARDİYA"], kind="stable")
    parcalar_sonuc = []
    for _, grup in df_sirali.groupby(["TEZGAH", "TARİH", "VARDİYA"], sort=False):
        parcalar_sonuc.append(_grup_zincirle(grup))
    if not parcalar_sonuc:
        return pd.DataFrame()
    sonuc = pd.concat(parcalar_sonuc, ignore_index=True)
    return sonuc.sort_values(["TEZGAH", "Baslangic_Zaman"]).reset_index(drop=True)


def compute_setup_parca_kirilimi(df: pd.DataFrame) -> pd.DataFrame:
    gerekli = ["Durum", "Operator_Kod", "PARÇA NO_ÜRETİM_GİRİLEN", "Sure_Saat"]
    if df.empty or not all(c in df.columns for c in gerekli):
        return pd.DataFrame()
    setup = df[df["Durum"] == "Set_Up"].dropna(subset=["PARÇA NO_ÜRETİM_GİRİLEN"])
    if setup.empty:
        return pd.DataFrame()
    ozet = setup.groupby(["Operator_Kod", "PARÇA NO_ÜRETİM_GİRİLEN"]).agg(
        Toplam_Setup_Saat=("Sure_Saat", "sum"), Setup_Adet=("Sure_Saat", "count"),
        Ort_Setup_Dakika=("Sure_Saat", lambda s: round(s.mean() * 60, 1)),
    ).reset_index()
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
    return piv
