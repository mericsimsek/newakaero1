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

# BUG DÜZELTMESİ: bu modülde `log.info(...)` / `log.warning(...)` çağrılıyor
# ama logger hiç tanımlanmamıştı -> ilk yeni operatör görüldüğünde ya da
# haritası dosyası okunamadığında NameError ile ETL çöküyordu.
log = logging.getLogger("temizlik_kurallari")


# ---------------------------------------------------------------------------
# METİN / KATEGORİ YARDIMCILARI
# ---------------------------------------------------------------------------
def turkce_temizle(metin) -> str | None:
    """Türkçe karakterleri ASCII karşılıklarına çevirir, büyük harfe çevirir,
    baş/son boşlukları temizler. Operatör isimlerini karşılaştırılabilir
    hale getirmek için kullanılır (örn. 'Hüseyin' ve 'HÜSEYIN' aynı kişi)."""
    if pd.isna(metin):
        return metin
    metin = str(metin).strip().upper()
    harita = {"İ": "I", "I": "I", "Ç": "C", "Ş": "S", "Ğ": "G", "Ü": "U", "Ö": "O"}
    for tr, en in harita.items():
        metin = metin.replace(tr, en)
    return metin


def categorize_reason(text) -> str | None:
    """Duruş sebebi ham metnini (OPERASYON_NO kolonundan, Durum='Plansız_Duruş'
    olduğunda) config.CATEGORY_RULES'a göre anlamlı bir kategoriye atar."""
    if pd.isna(text):
        return None
    t = str(text).upper()
    for category, keywords in config.CATEGORY_RULES:
        for kw in keywords:
            if kw in t:
                return category
    if re.fullmatch(r"\d+", t.strip()):
        # Sadece sayı içeren bir duruş sebebi muhtemelen yanlışlıkla girilmiş
        # bir operasyon numarasıdır, gerçek bir sebep açıklaması değildir.
        return "Veri Girişi Hatası (Sayısal Kod)"
    return "Diğer / Sınıflandırılmamış"


def vardiya_saat(v) -> float | None:
    """'19:00 - 07:00' gibi bir vardiya aralığının kaç saat sürdüğünü hesaplar
    (gece yarısını geçen vardiyaları da doğru şekilde ele alır)."""
    if pd.isna(v):
        return None
    try:
        start, end = str(v).split(" - ")
        sh, sm = map(int, start.split(":"))
        eh, em = map(int, end.split(":"))
        start_min = sh * 60 + sm
        end_min = eh * 60 + em
        if end_min <= start_min:
            end_min += 24 * 60
        return (end_min - start_min) / 60
    except Exception:
        return None


def vardiya_sinifi(v) -> str | None:
    """Vardiya saat aralığını config.VARDIYA_ETIKET_HARITASI'na göre okunabilir
    bir sınıfa çevirir. Haritada olmayan yeni bir aralık görülürse
    silinmeden 'Bilinmeyen (...)' olarak işaretlenir ki fark edilsin."""
    if pd.isna(v):
        return None
    v_temiz = str(v).strip()
    return config.VARDIYA_ETIKET_HARITASI.get(v_temiz, f"Bilinmeyen ({v_temiz})")


def vardiya_ici_pozisyon(row: pd.Series) -> float | None:
    """Bir kaydın başlangıç saatinin, kendi vardiyasının başlangıcına göre
    yüzde kaçıncı noktada olduğunu hesaplar (0%=vardiya başı, 100%=vardiya
    sonu). Vardiyanın dışına taşan (>100%) veya negatif çıkan değerler,
    saat girişinde hata olabileceğinin bir işaretidir — silinmez, olduğu
    gibi bırakılır ki analiz sayfasında görülüp fark edilebilsin."""
    v = row.get("VARDİYA")
    bas = row.get("BAŞLANGIÇ_SAAT")
    if pd.isna(v) or pd.isna(bas):
        return None
    try:
        vardiya_baslangic = str(v).split(" - ")[0]
        vh, vm = map(int, vardiya_baslangic.split(":"))
        bh, bm, *_ = str(bas).split(":")
        bh, bm = int(bh), int(bm)
        v_dk = vh * 60 + vm
        b_dk = bh * 60 + bm
        fark = b_dk - v_dk
        if fark < 0:
            fark += 24 * 60
        uzunluk = row.get("Vardiya_Uzunluk_Saat", 0) * 60
        return round(fark / uzunluk * 100, 1) if uzunluk else None
    except Exception:
        return None


# ---------------------------------------------------------------------------
# SAYI / TARİH FORMAT KORUMASI
# ---------------------------------------------------------------------------
_THOUSANDS_ONLY_PATTERN = re.compile(r"^-?\d{1,3}(\.\d{3})+$")


def _fix_turkish_decimal(series: pd.Series) -> pd.Series:
    """Türkçe sayı formatlarını normalize eder ('1.234,56' -> 1234.56,
    '1.039' (sadece binlik ayraç) -> 1039.0). Ayrıntılı mantık: virgül
    varsa kesin Türkçe format; sadece nokta varsa ve gruplar tam 3 haneliyse
    binlik ayraçtır, değilse gerçek ondalıktır (örn. '2.5')."""
    def convert(val):
        if pd.isna(val):
            return val
        if isinstance(val, (int, float)):
            return float(val)
        s = str(val).strip()
        if s == "":
            return None
        if "," in s:
            s = s.replace(".", "").replace(",", ".")
        elif _THOUSANDS_ONLY_PATTERN.match(s):
            s = s.replace(".", "")
        try:
            return float(s)
        except ValueError:
            return None
    return series.apply(convert)


def _fix_mixed_dates(series: pd.Series) -> pd.Series:
    """Karışık tarih formatlarını tek bir datetime kolonuna çevirir."""
    def parse_one(val):
        if pd.isna(val):
            return pd.NaT
        if isinstance(val, (pd.Timestamp, datetime)):
            return pd.Timestamp(val)
        s = str(val).strip()
        for fmt in ("%d.%m.%Y", "%Y-%m-%d", "%d/%m/%Y", "%d.%m.%Y %H:%M:%S", "%Y-%m-%d %H:%M:%S"):
            try:
                return pd.Timestamp(datetime.strptime(s, fmt))
            except ValueError:
                continue
        return pd.to_datetime(s, dayfirst=True, errors="coerce")
    return series.apply(parse_one)


# ---------------------------------------------------------------------------
# OPERATÖR KODU KALICILIĞI
# ---------------------------------------------------------------------------
def _load_operator_kod_haritasi() -> dict:
    """Daha önce atanmış operatör isim->kod eşlemesini diskten okur.
    Dosya yoksa (ilk çalıştırma) boş sözlük döner."""
    path = config.OPERATOR_KOD_HARITASI_PATH
    if not path.exists():
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        log.warning("Operatör kod haritası okunamadı (%s), sıfırdan başlanıyor: %s", path, e)
        return {}


def _save_operator_kod_haritasi(harita: dict):
    config.OPERATOR_KOD_HARITASI_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(config.OPERATOR_KOD_HARITASI_PATH, "w", encoding="utf-8") as f:
        json.dump(harita, f, ensure_ascii=False, indent=2, sort_keys=True)


def _get_or_assign_operator_kodlari(normalized_names: pd.Series) -> dict:
    """Normalize edilmiş operatör isimlerini kalıcı kodlara eşler.

    ÖNEMLİ: Bu fonksiyon önceki çalıştırmalardan kalan haritayı okur ve
    SADECE yeni (daha önce görülmemiş) operatörlere yeni kod atar. Var
    olan bir operatörün kodu ASLA değişmez. Bunun nedeni: ETL her seferinde
    tüm veriyi baştan işliyor (full-refresh); eğer kodlar her çalıştırmada
    "o anki dosyalardaki isimlerin alfabetik sırasına" göre yeniden
    atansaydı, bir operatör eksik/fazla geldiğinde TÜM diğer operatörlerin
    kodu kayabilir ve geçmiş karneler/analizler tutarsız hale gelirdi.
    """
    harita = _load_operator_kod_haritasi()
    mevcut_kodlar = set(harita.values())
    sonraki_no = len(mevcut_kodlar) + 1

    yeni_isimler = sorted(set(normalized_names.dropna()) - set(harita.keys()))
    for isim in yeni_isimler:
        while f"Op_{sonraki_no:03d}" in mevcut_kodlar:
            sonraki_no += 1
        yeni_kod = f"Op_{sonraki_no:03d}"
        harita[isim] = yeni_kod
        mevcut_kodlar.add(yeni_kod)
        sonraki_no += 1

    if yeni_isimler:
        log.info("%d yeni operatöre kod atandı: %s", len(yeni_isimler), yeni_isimler)
        _save_operator_kod_haritasi(harita)

    return harita
