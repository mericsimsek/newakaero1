# -*- coding: utf-8 -*-
"""
teyit.py — AI cevabını GERÇEK VERİYLE teyit eden modül (4_AI_Asistan.py'deki "🔍 Teyit et" butonu).

Üç şey yapar:
  1) Cevaptaki SAYILARI, ekranda/AI'a giden tablolarda aranıp bulunur (hangi tablo, hangi satır, hangi kolon).
     Bulunamayan sayı = "teyit edilemedi" (AI kendi hesaplamış ya da uydurmuş olabilir).
  2) Cevapta geçen S/N, operatör, tezgah, parça kodunun HAM KAYITLARINI gösterir (tek tek toplanabilir).
  3) Cevapta geçen metriklerin FORMÜLÜNÜ (METRIC_REGISTRY) gösterir ve gerçek satırlarla BASAMAK BASAMAK yeniden hesaplar;
     sonucu tablodaki değerle kıyaslar ("Emsal Bazlı Sapma % şu satırlardan, şu medyanla çıktı").
"""
from __future__ import annotations

import re
import numpy as np
import pandas as pd

from sn_izleme import ad_norm, hafta_no, hafta_baslangic, sn_tum_analiz, PILOT, ALARM_TOLERANS, uzun_kod

try:
    import common as _C
    METRIC_REGISTRY = _C.METRIC_REGISTRY
    EMSAL_MIN_TEKRAR = _C.EMSAL_MIN_TEKRAR
    CT_DUSUK_GUVEN_ORANI = _C.CT_DUSUK_GUVEN_ORANI
except Exception:  # common yoksa yalnızca S/N kısmı çalışır
    _C, METRIC_REGISTRY, EMSAL_MIN_TEKRAR, CT_DUSUK_GUVEN_ORANI = None, {}, 3, 0.5

_TR = str.maketrans("İıŞşĞğÜüÖöÇç", "IiSsGgUuOoCc")
def fold(s) -> str:
    return " ".join(str(s).translate(_TR).upper().split())

TABLO_NO = {"sn_baslik": 10, "tezgah_oee": 1, "operator_karne": 2, "pareto": 3, "mtbf_mttr": 4, "vardiya_yorgunluk": 5,
            "setup_parca_kirilimi": 6, "onerilen_ct": 9, "sn_ozet": 10, "sn_detay": 11, "sn_hafta": 12, "sn_setup": 13,
            "sn_gunluk": 14, "sn_operator": 15, "sn_vardiya": 16, "sn_tezgah": 17, "sn_rota": 18, "sn_durus": 19, "sn_uclar": 20}

SN_METRIKLER = {
    "sn_adet": {"ad": "Adet (S/N bazlı)", "formul": "Adet = farklı S/N sayısı  (aynı S/N'nin tüm kayıtları birleşir, 1 sayılır)",
                "kaynak_kolonlar": ["S/N", "Durum", "PARÇA NO_ÜRETİM_GİRİLEN", "OPERASYON_NO"],
                "aciklama": "Satır sayısı değil S/N sayısı sayılır; biri diğerinin işini devam ettirse de 1 adet."},
    "sn_esdeger": {"ad": "Adet Payı / Eşdeğer Ort. Süre", "formul": "Adet_Payı = Σ (kişinin_saati / S/N_toplam_saati);  Eşdeğer_Ort = Toplam_Saat / Adet_Payı",
                   "kaynak_kolonlar": ["S/N", "Operator_Kod", "Sure_Saat"],
                   "aciklama": "Bölünmüş S/N'de adet, o kişinin saat payı oranında paylaşılır; böylece yarım iş yapan haksız kıyaslanmaz."},
    "sn_hafta": {"ad": "Hafta (Perşembe→Çarşamba)", "formul": "Hafta_No = ISO_hafta(tarih − 3 gün);  S/N, TAMAMLANDIĞI (son kayıt) haftaya yazılır",
                 "kaynak_kolonlar": ["TARİH", "S/N"], "aciklama": "Hafta Perşembe başlar, Çarşamba biter."},
    "sn_alarm": {"ad": "Yavaş S/N alarmı", "formul": f"Yavaş  ⇔  S/N_Toplam_Saat > Katalog_C/T × (1 + {ALARM_TOLERANS:g})",
                 "kaynak_kolonlar": ["Sure_Saat", "ÜRETİM_C/T"], "aciklama": "Karşılaştırma kayıt bazında değil S/N toplamı üzerindendir."},
}

# Bir sayı tabloda başka bir kolonla tesadüfen eşleşmesin: "ana" kolonlar
PRIMARY = {"sn_ozet": {"Toplam_Saat", "Kayit", "Medyana_Gore_%"}, "sn_detay": {"Sure_Saat"}, "sn_rota": {"Toplam_Sa_Tum_Op"},
           "sn_hafta": {"Adet", "Ort_Sure_Sa", "Medyan_Sure_Sa", "Toplam_Uretim_Sa", "Gunluk_Ort_Adet", "Setup_Sa", "Bolunmus", "Uretim_Gunu"},
           "sn_gunluk": {"Adet", "Ort_Sure_Sa", "Toplam_Uretim_Sa", "Bolunmus"}}
ADET_ANA = {"S/N sayısı = ADET", "Adet", "SN_Sayisi", "Adet_Payi"}

ANAHTAR = {  # metrik anahtarı -> cevapta/soruda aranacak ifadeler (fold'lanmış)
    "kullanilabilirlik": ["KULLANILABILIRLIK"], "kalite_orani": ["KALITE ORAN"], "performans_orani": ["PERFORMANS ORAN", "PERFORMANS"],
    "gercek_oee": ["OEE"], "ct_bazli_sapma": ["CT BAZLI", "C/T BAZLI", "CT SAPMA", "C/T SAPMA", "KATALOG C/T"],
    "emsal_bazli_sapma": ["EMSAL"], "pareto_kumulatif": ["PARETO", "KUMULATIF"], "mtbf": ["MTBF"], "mttr": ["MTTR"],
    "sn_adet": ["ADET", "S/N"], "sn_esdeger": ["ESDEGER", "ADET PAYI"], "sn_hafta": ["HAFTA", "WEEK"], "sn_alarm": ["YAVAS", "ALARM"],
}


# ------------------------------------------------------------------ TABLOLAR
def tablolari_topla(df: pd.DataFrame, pilot=PILOT, op_adi=lambda k: k) -> dict:
    """AI sayfasının bağlamında kullanılan tablolarla AYNI fonksiyonlardan üretilir."""
    T: dict[str, pd.DataFrame] = {}
    if _C is not None:
        for ad, fn in [("tezgah_oee", "compute_tezgah_oee"), ("operator_karne", "compute_operator_karne"), ("pareto", "compute_pareto"),
                       ("mtbf_mttr", "compute_mtbf_mttr"), ("vardiya_yorgunluk", "compute_vardiya_yorgunluk"),
                       ("setup_parca_kirilimi", "compute_setup_parca_kirilimi"), ("onerilen_ct", "compute_onerilen_ct")]:
            try:
                T[ad] = getattr(_C, fn)(df)
            except Exception:
                pass
    for kisa, op in pilot:
        try:
            t = sn_tum_analiz(df, kisa, op, op_adi)
        except Exception:
            continue
        if t["kapsam"].get("mesaj") or t["ozet"].empty:
            continue
        for a, b in [("ozet", "sn_ozet"), ("detay", "sn_detay"), ("hafta", "sn_hafta"), ("setup", "sn_setup"), ("gunluk", "sn_gunluk"),
                     ("operator", "sn_operator"), ("vardiya", "sn_vardiya"), ("tezgah", "sn_tezgah"), ("rota", "sn_rota"),
                     ("durus", "sn_durus"), ("uclar", "sn_uclar")]:
            if a in t and not t[a].empty:
                T[b] = t[a]
        T["_sn_kapsam"] = t["kapsam"]
    return T


# ------------------------------------------------------------------ VARLIK ÇIKARIMI
def varliklar(metin: str, df: pd.DataFrame) -> dict:
    f = fold(metin)
    out = {"sn": [], "operator": [], "tezgah": [], "parca": [], "hafta": [], "metrik": []}
    if "S/N" in df.columns:
        for s in df["S/N"].dropna().astype(str).unique():
            if len(s) >= 4 and re.search(rf"(?<![A-Z0-9]){re.escape(fold(s))}(?![A-Z0-9])", f):
                out["sn"].append(s)
    ok = "Operator_Kod" if "Operator_Kod" in df.columns else ("OPERATÖR" if "OPERATÖR" in df.columns else None)
    if ok:
        gor = {}
        for n in df[ok].dropna().astype(str).unique():
            gor.setdefault(fold(n), []).append(n)
        for fn, raw in gor.items():
            if len(fn) >= 5 and fn in f:
                out["operator"].append(fn)
    if "TEZGAH" in df.columns:
        for t in df["TEZGAH"].dropna().astype(str).unique():
            if re.search(rf"(?<![A-Z0-9]){re.escape(fold(t))}(?![A-Z0-9])", f):
                out["tezgah"].append(t)
    for c in [c for c in ["PARÇA NO_ÜRETİM_GİRİLEN", "PARÇA NO ÜRETİM GİRİLEN"] if c in df.columns][:1]:
        for p in df[c].dropna().astype(str).unique():
            if len(p) >= 5 and fold(p) in f:
                out["parca"].append(p)
    out["parca"] = out["parca"][:5]
    for m in re.finditer(r"(?:HAFTA|WEEK)\s*[-:]?\s*(\d{1,2})", f):
        out["hafta"].append(int(m.group(1)))
    for anahtar, ifadeler in ANAHTAR.items():
        if any(i in f for i in ifadeler):
            out["metrik"].append(anahtar)
    for k, m in {**METRIC_REGISTRY, **SN_METRIKLER}.items():
        if fold(m["ad"]) in f and k not in out["metrik"]:
            out["metrik"].append(k)
    return out


# ------------------------------------------------------------------ SAYI DOĞRULAMA
def _katalog(T: dict) -> pd.DataFrame:
    rows = []
    for ad, t in T.items():
        if not isinstance(t, pd.DataFrame) or t.empty:
            continue
        etk = [c for c in t.columns if (not pd.api.types.is_numeric_dtype(t[c]) or pd.api.types.is_bool_dtype(t[c]))
               and not pd.api.types.is_datetime64_any_dtype(t[c])]
        sayi = [c for c in t.columns if pd.api.types.is_numeric_dtype(t[c]) and not pd.api.types.is_bool_dtype(t[c])]
        for _, r in t.iterrows():
            et = " | ".join(([f"Hafta {int(r['Hafta'])}"] if "Hafta" in t.columns and pd.notna(r.get("Hafta")) else [])
                            + [str(r[c]) for c in etk[:6] if pd.notna(r[c])])
            for c in sayi:
                v = r[c]
                if pd.isna(v):
                    continue
                v = float(v)
                rows.append((v, ad, et, c, ""))
                if abs(v) <= 1.5 and any(k in c for k in ("Oran", "Kullan", "OEE", "Kalite")):
                    rows.append((v * 100, ad, et, c, "×100 (%)"))
                if any(k in c for k in ("Saat", "_Sa", "Sure")):
                    rows.append((v * 60, ad, et, c, "×60 (dakika)"))
    # AI bağlamının BAŞLIK satırındaki toplamlar (tablo hücresi değil, ama AI'a giden sayılar)
    k = T.get("_sn_kapsam"); oz = T.get("sn_ozet")
    if k and oz is not None and not oz.empty:
        for kol, v in [("S/N sayısı = ADET", k.get("sn_sayisi")), ("Medyan süre (sa)", k.get("medyan_saat")), ("Bölünmüş S/N", k.get("bolunmus_sn")),
                       ("Set-up toplam (sa)", k.get("setup_saat")), ("Üretim kaydı", k.get("uretim_kaydi")), ("S/N'si boş kayıt", k.get("sn_bos_kayit")),
                       ("Toplam süre (sa) = Σ Toplam_Saat", oz["Toplam_Saat"].sum()), ("Ortalama süre (sa)", oz["Toplam_Saat"].mean()),
                       ("Yavaş S/N sayısı", int((oz["Alarm"] == "🔴 Yavaş").sum()) if "Alarm" in oz else None)]:
            if v is not None:
                rows.append((float(v), "sn_baslik", "", kol, ""))
    return pd.DataFrame(rows, columns=["v", "tablo", "satir", "kolon", "donusum"])


def _sayi(s: str) -> float:
    if re.fullmatch(r"\d{1,3}(\.\d{3})+(,\d+)?", s):
        s = s.replace(".", "")
    return float(s.replace(",", "."))


def _maskele(metin: str, v: dict) -> str:
    m = metin
    kal = [r"\b(?=\w*\d)(?=\w*[A-Za-zÇĞİÖŞÜçğıöşü])\w+\b", r"TABLO\s*\d+", r"\d{1,2}:\d{2}", r"\d{1,2}[./-]\d{1,2}(?:[./-]\d{2,4})?", r"\b20\d\d\b", r"(?:HAFTA|WEEK)\s*[-:]?\s*\d+",
           r"(?im)^\s*\d+[.)]\s", r"Op_\d+", r"\bop(?:erasyon)?\s*\d+", r"\d+\s*\.\s*operasyon", r"\bAB-\d+\b"]
    for s in v["sn"] + v["parca"] + v["tezgah"]:
        kal.append(re.escape(s))
    for p in kal:
        m = re.sub(p, lambda x: " " * len(x.group(0)), m, flags=re.I)
    return m


def _tur(etiket0: str, T_df: pd.DataFrame, sets: dict) -> str | None:
    for tur, kume in sets.items():
        if etiket0 in kume:
            return tur
    return None


def _rota_op_ok(kolon: str, bag: str) -> bool:
    m = re.match(r"Op(\d+)_", kolon)
    if not m:
        return False
    n = m.group(1)
    return bool(re.search(rf"\bOP(?:ERASYON)?\s*{n}\b|\b{n}\s*\.?\s*OPERASYON", bag))


def _varlik_degeri(T: dict, tablo: str, kolon: str, anilan: set) -> str:
    t = T.get(tablo)
    if t is None or t.empty or kolon not in t.columns:
        return ""
    etk = [c for c in t.columns if not pd.api.types.is_numeric_dtype(t[c])]
    if not etk:
        return ""
    r = t[t[etk[0]].astype(str).map(fold).isin(anilan)]
    return ", ".join(f"{x[etk[0]]} → {kolon} = {x[kolon]}" for _, x in r.head(2).iterrows())


def sayi_kontrol(cevap: str, V: dict, T: dict, df: pd.DataFrame | None = None) -> pd.DataFrame:
    kat = _katalog(T)
    f = fold(cevap)
    sets = {}
    if df is not None:
        if "TEZGAH" in df.columns:
            sets["tezgah"] = {fold(x) for x in df["TEZGAH"].dropna().astype(str).unique()}
        okc = "Operator_Kod" if "Operator_Kod" in df.columns else ("OPERATÖR" if "OPERATÖR" in df.columns else None)
        if okc:
            sets["operator"] = {fold(x) for x in df[okc].dropna().astype(str).unique()}
        if "S/N" in df.columns:
            sets["sn"] = {fold(x) for x in df["S/N"].dropna().astype(str).unique()}
    anılan = {"tezgah": {fold(x) for x in V["tezgah"]}, "operator": set(V["operator"]), "sn": {fold(x) for x in V["sn"]}}
    sat = []
    maske = fold(_maskele(cevap, V))
    for m in re.finditer(r"(?<![\w.,])(%\s?)?([-−–]?)(\d+(?:[.,]\d+)?)\s*(%|SAAT\w*|SA\b|DK\b|DAKIKA\w*|ADET\w*|KEZ\b|PARCA\w*)?", maske):
        isaret, s_, birim = m.group(2), m.group(3), ("%" if m.group(1) else (m.group(4) or ""))
        x = _sayi(s_) * (-1 if isaret else 1)
        d = len(re.split(r"[.,]", s_)[1]) if re.search(r"[.,]\d", s_) else 0
        if d == 0 and not birim and abs(x) < 10:
            continue  # sıra/liste numarası olabilir
        bag = f[max(0, m.start() - 70): m.end() + 70]
        tol = 0.5 * 10 ** -d + 1e-9
        adaylar = kat[(kat["v"] - x).abs() <= tol].copy() if not kat.empty else kat
        if d == 0 and len(adaylar):
            adaylar = adaylar[adaylar["v"].round() == x]
        def birim_uyumlu(a):
            if a.empty:
                return a
            if birim.startswith("SA"):
                return a[a["kolon"].str.contains("Saat|_Sa\\b|Sure|CT|Süre|Toplam|Esdeger", regex=True) & (a["donusum"] == "")]
            if birim.startswith(("DK", "DAKIKA")):
                return a[a["donusum"].str.startswith("×60")]
            if birim == "%":
                return a[a["kolon"].str.contains("Yuzde|Oran|Sapma|Kullan|OEE|Kalite|Kumulatif|%", regex=True) | a["donusum"].str.startswith("×100")]
            return a
        adaylar = birim_uyumlu(adaylar)
        isaret_ters = False
        if not len(adaylar) and x < 0 and not kat.empty:   # AI "−7,2" yerine "7,2% daha hızlı" demiş olabilir
            adaylar = birim_uyumlu(kat[(kat["v"] - abs(x)).abs() <= tol].copy()); isaret_ters = bool(len(adaylar))
        elif not len(adaylar) and x > 0 and not kat.empty:
            adaylar = birim_uyumlu(kat[(kat["v"] + x).abs() <= tol].copy()); isaret_ters = bool(len(adaylar))
        eslesme = "—"; tablo = satir = kolon = ""; durum = "⚠️ teyit edilemedi"
        if len(adaylar):
            def puan(r):
                toks = [fold(t.strip()) for t in r.satir.split(" | ") if len(t.strip()) >= 4]
                var = lambda t, yer: re.search(rf"(?<![A-Z0-9]){re.escape(t)}(?![A-Z0-9])", yer)
                yerel = sum(1 for t in set(toks) if var(t, bag))
                genel = sum(1 for t in set(toks) if var(t, f))
                p = 3 * min(yerel, 3) + min(genel, 2)
                ilk_ = fold(r.satir.split(" | ")[0]) if r.satir else ""
                tur_ = _tur(ilk_, kat, sets) if ilk_ else None
                if tur_:   # satır bir S/N / operatör / tezgah satırı: cevapta anılanla aynı mı?
                    p += ({"sn": 8, "tezgah": 6, "operator": 3}[tur_] if ilk_ in anılan[tur_] else (-8 if anılan[tur_] else 0))
                p += 2 if (birim == "%" and (any(k in r.kolon for k in ("Yuzde", "Oran", "Sapma", "%")) or r.donusum.startswith("×100"))) else 0
                p += 2 if (birim.startswith("SA") and any(k in r.kolon for k in ("Saat", "_Sa", "Sure")) and not r.donusum) else 0
                p += 2 if (birim.startswith(("DK", "DAKIKA")) and "dakika" in r.donusum) else 0
                p += (4 if r.kolon in ("Adet", "S/N sayısı = ADET") else 2) if (birim.startswith(("ADET", "PARCA")) and any(k in r.kolon for k in ("ADET", "Adet", "SN_Sayisi", "Kayit"))) else 0
                p -= 2 if (r.donusum and not (birim == "%" or birim.startswith(("DK", "DAKIKA")))) else 0
                if r.tablo in PRIMARY and (r.kolon in PRIMARY[r.tablo] or (r.tablo == "sn_rota" and _rota_op_ok(r.kolon, bag))):
                    p += 8
                if birim.startswith(("ADET", "PARCA")) and r.kolon in ADET_ANA:
                    p += 4
                p -= TABLO_NO.get(r.tablo, 30) * 0.01
                return p, yerel + genel
            sk = [puan(r) for r in adaylar.itertuples()]
            adaylar["puan"] = [a_[0] for a_ in sk]; adaylar["etiket_isabet"] = [a_[1] for a_ in sk]
            b = adaylar.sort_values("puan", ascending=False).iloc[0]
            tablo = f"TABLO {TABLO_NO.get(b.tablo, '?')} ({b.tablo})"; satir = b.satir; kolon = b.kolon + (f" {b.donusum}" if b.donusum else "")
            eslesme = f"{b.v:.4g}"
            ilk = fold(b.satir.split(" | ")[0]) if b.satir else ""
            tur = _tur(ilk, kat, sets) if ilk else None
            if tur and anılan[tur] and ilk not in anılan[tur]:
                gercek = _varlik_degeri(T, b.tablo, b.kolon, anılan[tur])
                durum = (f"⚠️ bu değer başka bir {tur}'e ait ({b.satir.split(' | ')[0]})"
                         + (f"; cevapta anılan için tabloda: {gercek}" if gercek else ""))
            elif isaret_ters:
                durum = f"🟡 sayı eşleşti ama İŞARET TERS (tabloda {b.v:.4g})"
            elif b.tablo in PRIMARY and not (b.kolon in PRIMARY[b.tablo] or (b.tablo == "sn_rota" and _rota_op_ok(b.kolon, bag))):
                ana = _varlik_degeri(T, "sn_ozet", "Toplam_Saat", anılan["sn"]) if anılan["sn"] else ""
                durum = f"🟡 sayı yalnızca başka bir kolonla eşleşti ({b.kolon})" + (f"; ana değer: {ana}" if ana else "")
            elif birim.startswith(("ADET", "PARCA")) and b.kolon not in ADET_ANA:
                k_ = T.get("_sn_kapsam", {}).get("sn_sayisi")
                durum = f"🟡 'adet' için bu sayı başka bir kolonla eşleşti ({b.kolon})" + (f"; S/N sayısı (adet) = {k_}" if k_ else "")
            elif b.satir and b.etiket_isabet == 0:
                durum = "🟡 zayıf eşleşme — satır etiketi cevapta geçmiyor, aynı sayı başka yerde olabilir (tesadüf)"
            else:
                durum = "✅ eşleşti"
        if not durum.startswith("✅") and not kat.empty:
            tum_anilan = anılan["tezgah"] | anılan["operator"] | anılan["sn"]
            yk = kat[kat["satir"].map(lambda z: fold(z.split(" | ")[0]) in tum_anilan if z else False)].copy()
            if birim == "%":
                yk = yk[yk["kolon"].str.contains("Yuzde|Oran|Sapma|%", regex=True) | yk["donusum"].str.startswith("×100")]
            elif birim.startswith("SA"):
                yk = yk[yk["kolon"].str.contains("Saat|_Sa|Sure") & (yk["donusum"] == "")]
            if len(yk):
                yk["g"] = ((yk["v"] - x).abs() / max(abs(x), 1e-9))
                yk = yk[(yk["g"] <= 0.12) & (yk["g"] > 0)].sort_values("g")
                if len(yk):
                    y0 = yk.iloc[0]
                    durum += f" · yakın değer: {y0.satir.split(' | ')[0]} → {y0.kolon} = {y0.v:.4g} (cevaptaki {isaret}{s_} ile tam örtüşmüyor)"
        sat.append({"Cevaptaki ifade": (isaret + s_ + " " + birim.lower()).strip(), "Bağlam": cevap[max(0, m.start() - 25): m.end() + 15].replace("\n", " "),
                    "Tabloda": eslesme, "Tablo": tablo, "Satır": satir, "Kolon": kolon, "Durum": durum})
    return pd.DataFrame(sat)


# ------------------------------------------------------------------ İLİŞKİ KONTROLÜ
def iliski_kontrol(V: dict, df: pd.DataFrame, cevap: str) -> pd.DataFrame:
    """Cevaptaki 'bu S/N'yi şu kişi/tezgah/vardiya yaptı' ilişkileri ham kayıtta var mı?"""
    if "S/N" not in df.columns:
        return pd.DataFrame()
    okc = "Operator_Kod" if "Operator_Kod" in df.columns else "OPERATÖR"
    vard_set = {re.sub(r"\s", "", str(x)): str(x) for x in df["VARDİYA"].dropna().unique()} if "VARDİYA" in df.columns else {}
    c_n = re.sub(r"\s", "", cevap)
    vard_an = [orj for k, orj in vard_set.items() if k in c_n]
    rows = []
    for sn in V["sn"]:
        d = df[df["S/N"].astype(str) == sn]
        ops = {fold(x): x for x in d[okc].astype(str)}
        tzs = {str(x) for x in d["TEZGAH"].astype(str)} if "TEZGAH" in d.columns else set()
        vs = {str(x) for x in d["VARDİYA"].astype(str)} if "VARDİYA" in d.columns else set()
        for fn in V["operator"]:
            rows.append({"İddia": f"{sn} ↔ {fn}", "Ham veride": "✅ var" if fn in ops else "❌ YOK", "S/N'nin ham kayıtlarında": ", ".join(sorted(set(ops.values())))})
        for t in V["tezgah"]:
            rows.append({"İddia": f"{sn} ↔ {t}", "Ham veride": "✅ var" if t in tzs else "❌ YOK", "S/N'nin ham kayıtlarında": ", ".join(sorted(tzs))})
        for v in vard_an:
            rows.append({"İddia": f"{sn} ↔ vardiya {v}", "Ham veride": "✅ var" if v in vs else "❌ YOK", "S/N'nin ham kayıtlarında": ", ".join(sorted(vs))})
    return pd.DataFrame(rows)


# ------------------------------------------------------------------ HAM KANIT
def _kols(df, *adaylar):
    return [c for c in adaylar if c in df.columns]

def kanit_sn(df: pd.DataFrame, sn: str) -> dict:
    ok = "Operator_Kod" if "Operator_Kod" in df.columns else "OPERATÖR"
    d = df[df["S/N"].astype(str) == sn]
    kol = _kols(d, "TARİH", "VARDİYA", "BAŞLANGIÇ_SAAT", "BAŞLANGIÇ SAAT", "BİTİŞ_SAAT", "BİTİŞ SAAT", "TEZGAH", ok, "KOD & OPE.", "Durum", "Sure_Saat", "FINISH")
    op_kol = _kols(d, "OPERASYON_NO", "OPERASYON NO")
    ozet = d.groupby([c for c in (op_kol + ["Durum"]) if c in d.columns])["Sure_Saat"].agg(Kayit="size", Toplam_Saat="sum").reset_index()
    return {"satirlar": d[kol + op_kol].sort_values([c for c in ["TARİH"] if c in d.columns]).reset_index(drop=True), "ozet": ozet.round(2),
            "toplam": round(float(d["Sure_Saat"].sum()), 2), "uretim": round(float(d.loc[d["Durum"] == "Üretim", "Sure_Saat"].sum()), 2)}

def kanit_kisi(df: pd.DataFrame, fn: str) -> dict:
    ok = "Operator_Kod" if "Operator_Kod" in df.columns else "OPERATÖR"
    d = df[df[ok].astype(str).map(fold) == fn]
    durum = d.groupby("Durum")["Sure_Saat"].agg(Kayit="size", Toplam_Saat="sum").reset_index().round(2)
    parca = _kols(d, "PARÇA NO_ÜRETİM_GİRİLEN", "PARÇA NO ÜRETİM GİRİLEN")
    p = (d[d["Durum"] == "Üretim"].groupby(parca[0])["Sure_Saat"].agg(Kayit="size", Toplam_Saat="sum").reset_index()
         .sort_values("Toplam_Saat", ascending=False).head(8).round(2)) if parca else pd.DataFrame()
    return {"yazimlar": sorted(d[ok].astype(str).unique()), "durum": durum, "parca": p, "satir": len(d),
            "tarih": (d["TARİH"].min(), d["TARİH"].max()) if "TARİH" in d.columns and len(d) else None}

def kanit_tezgah(df: pd.DataFrame, t: str) -> dict:
    d = df[df["TEZGAH"].astype(str) == t]
    return {"durum": d.groupby("Durum")["Sure_Saat"].agg(Kayit="size", Toplam_Saat="sum").reset_index().round(2), "satir": len(d)}


# ------------------------------------------------------------------ FORMÜL DÖKÜMLERİ
def _kayit(key):
    m = METRIC_REGISTRY.get(key) or SN_METRIKLER.get(key) or {}
    return {"anahtar": key, "ad": m.get("ad", key), "formul": m.get("formul", "—"), "kaynak": m.get("kaynak_kolonlar", []),
            "aciklama": m.get("aciklama", ""), "ornekler": []}

def _durum(h, t, tol=0.051):
    if t is None or (isinstance(t, float) and np.isnan(t)):
        return "ℹ️ tabloda bu satır yok"
    return "✅ tabloyla aynı" if abs(h - float(t)) <= tol else f"❌ FARKLI (tabloda {float(t):.4g})"

def _satir_tab(T, ad, kol, deger):
    t = T.get(ad)
    if t is None or t.empty or kol not in t.columns:
        return None
    r = t[t[kol] == deger]
    return r.iloc[0] if len(r) else None

def _tezgah_ornek(df, T, key, tz):
    d = df[df["TEZGAH"].astype(str) == tz]
    u, p, r = (d.loc[d["Durum"] == x, "Sure_Saat"].sum() for x in ("Üretim", "Plansız_Duruş", "Rework"))
    tab = _satir_tab(T, "tezgah_oee", "TEZGAH", tz)
    adim = [("Filtre", f"TEZGAH = {tz}  ({len(d)} satır)"), ("Üretim_Saat (Durum='Üretim')", f"{u:.2f}"),
            ("Plansız_Duruş_Saat", f"{p:.2f}"), ("Rework_Saat", f"{r:.2f}")]
    if key == "kullanilabilirlik":
        den = u + p + r; h = (u + r) / den * 100 if den else 0
        adim += [("Formül (sayılarla)", f"({u:.2f} + {r:.2f}) / ({u:.2f} + {p:.2f} + {r:.2f}) × 100 = {h:.2f} %")]
        t_ = None if tab is None else tab["Kullanilabilirlik"] * 100
    elif key == "kalite_orani":
        h = u / (u + r) * 100 if (u + r) else 0
        adim += [("Formül (sayılarla)", f"{u:.2f} / ({u:.2f} + {r:.2f}) × 100 = {h:.2f} %")]
        t_ = None if tab is None else tab["Kalite_Orani"] * 100
    else:  # performans / OEE
        uu = d[d["Durum"] == "Üretim"]; teo = uu["ÜRETİM_C/T"].fillna(0).sum() if "ÜRETİM_C/T" in uu.columns else np.nan; ger = uu["Sure_Saat"].sum()
        perf = teo / ger if ger else 0
        adim += [("Σ ÜRETİM_C/T (teorik süre, Üretim satırları)", f"{teo:.2f}"), ("Σ Sure_Saat (gerçek süre, Üretim satırları)", f"{ger:.2f}")]
        if key == "performans_orani":
            h = perf; adim += [("Formül (sayılarla)", f"{teo:.2f} / {ger:.2f} = {perf:.4f}")]
            t_ = None if tab is None or "Performans_Orani" not in tab else tab["Performans_Orani"]
        else:
            den = u + p + r; kul = (u + r) / den if den else 0; kal = u / (u + r) if (u + r) else 0
            h = kul * perf * kal; adim += [("Formül (sayılarla)", f"{kul:.4f} × {perf:.4f} × {kal:.4f} = {h:.4f}")]
            t_ = None if tab is None or "Gercek_OEE" not in tab else tab["Gercek_OEE"]
    tol = 0.051 if key in ("kullanilabilirlik", "kalite_orani") else 0.00051
    return {"baslik": f"{tz}", "adimlar": adim, "hesap": round(float(h), 4), "tablo": None if t_ is None else float(t_), "durum": _durum(h, t_, tol),
            "not": "⚠️ Katalog C/T güncel değilse Performans/OEE şişer (>1 çıkabilir)." if key in ("performans_orani", "gercek_oee") else ""}

def _ct_ornek(df, T, ok_raw):
    d = df[(df["Durum"] == "Üretim") & df[ok_raw[0]].isin(ok_raw)]
    if "Sapma_Yuzdesi" in d.columns:
        s = d["Sapma_Yuzdesi"]
    else:
        ct = d["ÜRETİM_C/T"]; s = np.where(ct > 0, (d["Sure_Saat"] - ct) / ct * 100, np.nan)
    d = d.assign(_sapma=s)
    ok = d[d["_sapma"].notna()]
    return d, ok

def _operator_ornek(df, T, key, fn, raw=None):
    okc = "Operator_Kod" if "Operator_Kod" in df.columns else "OPERATÖR"
    tum = sorted(df.loc[df[okc].astype(str).map(fold) == fn, okc].astype(str).unique())
    raw = raw or tum
    d = df[df[okc].astype(str).isin(raw)]
    u = d[d["Durum"] == "Üretim"]
    out = {"baslik": f"{', '.join(raw)}" + (f"  (aynı kişinin diğer yazımları: {', '.join(x for x in tum if x not in raw)})" if len(tum) > len(raw) else ""), "adimlar": [], "tablo": None, "durum": "", "tablolar": [], "not": ""}
    karne = T.get("operator_karne")
    krow = None if karne is None or karne.empty else karne[karne["Operator_Kod"].astype(str).isin(raw)]
    if len(tum) > 1:
        out["not"] = ("⚠️ Aynı kişi veride birden fazla yazımla geçiyor ('" + "', '".join(tum) + "'); operatör karnesi her yazımı AYRI operatör sayar. "
                      "Bu döküm yalnızca yukarıdaki yazımın satırlarını kullanır. İsimleri ETL'de tek biçime getirin.")
    if key == "ct_bazli_sapma":
        ct = u["ÜRETİM_C/T"] if "ÜRETİM_C/T" in u.columns else pd.Series(np.nan, index=u.index)
        sap = u["Sapma_Yuzdesi"] if "Sapma_Yuzdesi" in u.columns else pd.Series(np.where(ct > 0, (u["Sure_Saat"] - ct) / ct * 100, np.nan), index=u.index)
        ok = u.assign(_s=sap)[sap.notna()]
        h = float(ok["_s"].mean()) if len(ok) else np.nan
        out["adimlar"] = [("Filtre", f"Durum='Üretim' ve operatör = {fn}: {len(u)} satır; C/T'si olan: {len(ok)}"),
                          ("Satır formülü", "(Sure_Saat − ÜRETİM_C/T) / ÜRETİM_C/T × 100"), ("Operatör ortalaması", f"ORT({len(ok)} satır) = {h:.2f} %")]
        ornek = ok.head(6)[_kols(ok, "TARİH", "KOD & OPE.", "Sure_Saat", "ÜRETİM_C/T", "_s")].rename(columns={"_s": "Satır_Sapma_%"}).round(2)
        out["tablolar"].append(("İlk 6 satır (hesabın ham girdisi)", ornek))
        t_ = None if krow is None or not len(krow) else float(krow["CT_Bazli_Sapma_Yuzde"].mean())
    else:  # emsal
        ref = df[df["Durum"] == "Üretim"].dropna(subset=["KOD & OPE.", "Sure_Saat"])
        sayim = ref.groupby("KOD & OPE.").size()
        guv = sayim[sayim >= EMSAL_MIN_TEKRAR].index
        med = ref[ref["KOD & OPE."].isin(guv)].groupby("KOD & OPE.")["Sure_Saat"].median().rename("Emsal_Medyan_Saat")
        g = u[u["KOD & OPE."].isin(med.index)].dropna(subset=["Sure_Saat"]).merge(med, on="KOD & OPE.")
        g["Sapma_%"] = np.where(g["Emsal_Medyan_Saat"] > 0, (g["Sure_Saat"] - g["Emsal_Medyan_Saat"]) / g["Emsal_Medyan_Saat"] * 100, 0)
        h = float(g["Sapma_%"].mean()) if len(g) else np.nan
        out["adimlar"] = [("Emsal referansı", f"ekrandaki filtreli veride Durum='Üretim' ({len(ref)} satır). Bir KOD & OPE. için en az {EMSAL_MIN_TEKRAR} tekrar şart; "
                                              f"{len(guv)}/{len(sayim)} kod bu şartı sağlıyor"),
                          ("Operatörün hesaba giren satırı", f"{len(g)} / {len(u)}"),
                          ("Satır formülü", "(Sure_Saat − Emsal_Medyan_Saat) / Emsal_Medyan_Saat × 100"),
                          ("Operatör ortalaması", f"ORT({len(g)} satır) = {h:.2f} %")]
        kod = (g.groupby("KOD & OPE.").agg(Satir=("Sure_Saat", "size"), Op_Ort_Sure=("Sure_Saat", "mean"), Emsal_Medyan_Saat=("Emsal_Medyan_Saat", "first"),
                                             Ort_Sapma_Yuzde=("Sapma_%", "mean")).reset_index())
        kod["Emsal_n(tekrar)"] = kod["KOD & OPE."].map(sayim)
        out["tablolar"].append(("KOD & OPE. kırılımı (en çok satırlı 10) — medyan nereden geliyor", kod.sort_values("Satir", ascending=False).head(10).round(2)))
        # S/N bölünme yanlılığı
        if "S/N" in g.columns and g["S/N"].notna().any():
            gs = g[g["S/N"].notna()].copy()
            tot = df[df["Durum"] == "Üretim"].groupby(["S/N", "KOD & OPE."])["Sure_Saat"].agg(["sum", "size"]).rename(columns={"sum": "SN_Toplam", "size": "SN_Kayit"})
            gs = gs.join(tot, on=["S/N", "KOD & OPE."])
            bol = gs[gs["SN_Kayit"] > 1]
            if len(bol):
                sn_sap = (bol["SN_Toplam"] - bol["Emsal_Medyan_Saat"]) / bol["Emsal_Medyan_Saat"] * 100
                out["tablolar"].append(("⚠️ Bölünmüş (çok kayıtlı) S/N satırları — satır bazlı emsal sapması yapay düşük/yüksek olabilir",
                                        pd.DataFrame([{"Bölünmüş satır": len(bol), "Satır bazlı ort. sapma %": round(float(bol["Sapma_%"].mean()), 1),
                                                       "Aynı işlerin S/N-toplam bazlı sapması %": round(float(sn_sap.mean()), 1)}])))
        t_ = None if krow is None or not len(krow) else float(krow["Emsal_Bazli_Sapma_Yuzde"].mean())
    out["hesap"] = None if pd.isna(h) else round(h, 2)
    out["tablo"] = t_
    if t_ is None:
        out["durum"] = "ℹ️ Operatör karnede yok (toplam üretim saati eşiğin altında olabilir) — hesap yine de yukarıdan görülebilir"
    else:
        out["durum"] = _durum(h, t_, 0.06)
    return out

def _pareto_ornek(df, T):
    if "Durus_Kategorisi" not in df.columns:
        return None
    d = df[df["Durum"] == "Plansız_Duruş"].dropna(subset=["Durus_Kategorisi"]).groupby("Durus_Kategorisi")["Sure_Saat"].sum().sort_values(ascending=False)
    if d.empty:
        return None
    tab = pd.DataFrame({"Saat": d.round(2), "Yüzde": (d / d.sum() * 100).round(1), "Kümülatif_%": (d.cumsum() / d.sum() * 100).round(1)}).reset_index()
    return {"baslik": "Duruş kategorileri (Durum='Plansız_Duruş')", "adimlar": [("Toplam duruş", f"{d.sum():.2f} sa")], "tablolar": [("Pareto", tab)],
            "hesap": None, "tablo": None, "durum": "ℹ️ kümülatif yüzde = sıralı saatlerin birikimli toplamı / toplam duruş", "not": ""}

def _sn_ornekler(df, T, key, V, cevap=""):
    out = []
    ozet, detay = T.get("sn_ozet"), T.get("sn_detay")
    kap = T.get("_sn_kapsam", {})
    if ozet is None or ozet.empty:
        return out
    if key == "sn_adet":
        out.append({"baslik": f"{kap.get('uzun_kod', '')} op {kap.get('operasyon', '')}",
                    "adimlar": [("Filtre", f"Durum='Üretim', parça={kap.get('uzun_kod', '')}, OPERASYON_NO={kap.get('operasyon', '')}, S/N dolu"),
                                ("Üretim kaydı", str(kap.get("uretim_kaydi", "?"))), ("Farklı S/N (=adet)", str(len(ozet))),
                                ("S/N'si boş kayıt (adede dahil değil)", f"{kap.get('sn_bos_kayit', 0)} kayıt / {kap.get('sn_bos_saat', 0):.2f} sa")],
                    "hesap": len(ozet), "tablo": len(ozet), "durum": "✅ tabloyla aynı",
                    "tablolar": [("S/N listesi (ilk 15)", ozet[["S/N", "Toplam_Saat", "Kayit", "Operatorler", "Son_Tarih"]].head(15))], "not": ""})
    if key == "sn_hafta":
        hf = T.get("sn_hafta")
        for w in V["hafta"][:2]:
            d = ozet[ozet["Hafta"] == w]
            iddia = re.search(rf"(?:HAFTA|WEEK)\s*{w}\b[^.\n]{{0,60}}?(\d+)\s*ADET", fold(cevap))
            adim = [("Hafta tanımı", "Perşembe→Çarşamba; S/N son kayıt tarihine göre"), ("Bu haftada tamamlanan S/N", str(len(d)))]
            idd_durum = None
            if iddia:
                adim.append(("Cevaptaki iddia", f"{iddia.group(1)} adet"))
                idd_durum = "✅ iddia tuttu" if int(iddia.group(1)) == len(d) else f"❌ CEVAP YANLIŞ: ham veriden {len(d)} adet çıkıyor"
            out.append({"baslik": f"Hafta {w}", "adimlar": adim,
                        "iddia_durum": idd_durum, "hesap": len(d), "tablo": None if hf is None or not len(hf[hf["Hafta"] == w]) else float(hf[hf["Hafta"] == w]["Adet"].iloc[0]),
                        "durum": _durum(len(d), None if hf is None or not len(hf[hf["Hafta"] == w]) else float(hf[hf["Hafta"] == w]["Adet"].iloc[0])),
                        "tablolar": [("Bu haftanın S/N'leri", d[["S/N", "Toplam_Saat", "Son_Tarih", "Operatorler"]])], "not": ""})
    if key == "sn_esdeger" and detay is not None:
        for fn in V["operator"][:2]:
            x = detay[detay["Operator"].map(fold) == fn].merge(ozet[["S/N", "Toplam_Saat"]], on="S/N", suffixes=("", "_SN"))
            if x.empty:
                continue
            x["Pay"] = x["Sure_Saat"] / x["Toplam_Saat"]
            pay, saat = x["Pay"].sum(), x["Sure_Saat"].sum()
            tab = T.get("sn_operator"); row = None if tab is None else tab[tab["Operator"].map(fold) == fn]
            t_ = None if row is None or not len(row) else float(row["Esdeger_Ort_Sa"].iloc[0])
            out.append({"baslik": fn, "adimlar": [("Kendi saati Σ", f"{saat:.2f}"), ("Adet_Payı Σ(kendi/S/N toplam)", f"{pay:.2f}"),
                                                  ("Eşdeğer_Ort", f"{saat:.2f} / {pay:.2f} = {saat / pay:.2f} sa")],
                        "hesap": round(saat / pay, 2), "tablo": t_, "durum": _durum(saat / pay, t_, 0.011),
                        "tablolar": [("S/N bazında pay", x[["S/N", "TARİH", "Sure_Saat", "Toplam_Saat", "Pay"]].round(3).head(15))], "not": ""})
    if key == "sn_alarm" and "Katalog_CT" in ozet.columns:
        ct = float(ozet["Katalog_CT"].max()); es = ct * (1 + ALARM_TOLERANS)
        d = ozet[ozet["Toplam_Saat"] > es]
        out.append({"baslik": "Yavaş S/N eşiği", "adimlar": [("Katalog C/T", f"{ct:g} sa"), ("Eşik", f"{ct:g} × (1 + {ALARM_TOLERANS:g}) = {es:g} sa"),
                                                              ("Eşiği aşan S/N", str(len(d)))],
                    "hesap": len(d), "tablo": int((ozet.get("Alarm") == "🔴 Yavaş").sum()) if "Alarm" in ozet else None,
                    "durum": "✅ tabloyla aynı" if "Alarm" in ozet and len(d) == int((ozet["Alarm"] == "🔴 Yavaş").sum()) else "❌ FARKLI",
                    "tablolar": [("Eşiği aşanlar", d[["S/N", "Toplam_Saat", "Kayit", "Operatorler"]])],
                    "not": "Excel kayıt bazında (tek satır > C/T) 'DİKKAT' der; burada S/N toplamına bakılır, ikisi farklı sonuç verebilir."})
    return out


# ------------------------------------------------------------------ ANA GİRİŞ
def teyit_raporu(soru: str, cevap: str, df: pd.DataFrame, T: dict) -> dict:
    V = varliklar(soru + "\n" + cevap, df)
    VC = varliklar(cevap, df)           # varlıklar: soru+cevap; sayı maskesi: cevap
    sayilar = sayi_kontrol(cevap, VC, T, df)
    iliski = iliski_kontrol(V, df, cevap)
    kanit = {"sn": {s: kanit_sn(df, s) for s in V["sn"][:6]}, "kisi": {o: kanit_kisi(df, o) for o in V["operator"][:4]},
             "tezgah": {t: kanit_tezgah(df, t) for t in V["tezgah"][:4]}}
    formuller = []
    for key in V["metrik"]:
        k = _kayit(key)
        try:
            if key in ("kullanilabilirlik", "kalite_orani", "performans_orani", "gercek_oee") and "TEZGAH" in df.columns:
                tzs = V["tezgah"][:3] or (T["tezgah_oee"]["TEZGAH"].head(2).tolist() if "tezgah_oee" in T and not T["tezgah_oee"].empty else [])
                k["ornekler"] = [_tezgah_ornek(df, T, key, tz) for tz in tzs]
            elif key in ("ct_bazli_sapma", "emsal_bazli_sapma"):
                ops = V["operator"][:3]
                okc = "Operator_Kod" if "Operator_Kod" in df.columns else "OPERATÖR"
                ornekler = []
                for fn in ops:
                    yazimlar = sorted(df.loc[df[okc].astype(str).map(fold) == fn, okc].astype(str).unique(),
                                      key=lambda y: -float(df.loc[df[okc].astype(str) == y, "Sure_Saat"].sum()))
                    for y in yazimlar[:2]:
                        ornekler.append(_operator_ornek(df, T, key, fn, raw=[y]))
                k["ornekler"] = ornekler
                if not ops:
                    k["not"] = "Hesap dökümü için cevapta/soruda bir operatör adı geçmeli."
            elif key == "pareto_kumulatif":
                p = _pareto_ornek(df, T); k["ornekler"] = [p] if p else []
            elif key.startswith("sn_"):
                k["ornekler"] = _sn_ornekler(df, T, key, V, cevap)
        except Exception as e:  # tek bir metrik tüm raporu düşürmesin
            k["not"] = f"Bu metrik için hesap dökümü üretilemedi: {e}"
        formuller.append(k)
    n = len(sayilar); ok = int((sayilar["Durum"].str.startswith("✅")).sum()) if n else 0
    zayif = int((sayilar["Durum"].str.startswith(("🟡", "⚠️ bu değer"))).sum()) if n else 0
    fdurum = [o["durum"] + (" " + o["iddia_durum"] if o.get("iddia_durum") else "") for f in formuller for o in f["ornekler"]]
    return {"varliklar": V, "sayilar": sayilar, "iliski": iliski, "kanit": kanit, "formuller": formuller,
            "ozet": {"sayi": n, "eslesen": ok, "zayif": zayif, "eslesmeyen": n - ok - zayif,
                     "iliski": len(iliski), "iliski_yok": int(iliski["Ham veride"].str.startswith("❌").sum()) if len(iliski) else 0, "formul_ornek": len(fdurum), "formul_tutan": sum(d.startswith("✅") for d in fdurum), "formul_farkli": sum(("FARKLI" in d or "YANLIŞ" in d) for d in fdurum)}}


# ------------------------------------------------------------------ STREAMLIT
def render_teyit(st, soru: str, cevap: str, df: pd.DataFrame, T: dict, kaynak_notu: str = ""):
    R = teyit_raporu(soru, cevap, df, T)
    o = R["ozet"]
    if kaynak_notu:
        st.caption(f"📂 Veri kaynağı: {kaynak_notu}")
    st.markdown(f"**Sayı kontrolü:** {o['sayi']} sayı → ✅ {o['eslesen']} eşleşti · 🟡 {o['zayif']} şüpheli eşleşme · ⚠️ {o['eslesmeyen']} teyit edilemedi"
                + (f"  |  **Formül dökümü:** {o['formul_ornek']} örnek → ✅ {o['formul_tutan']} tuttu · ❌ {o['formul_farkli']} farklı" if o["formul_ornek"] else ""))
    if o["eslesmeyen"] or o["zayif"]:
        st.warning("Teyit edilemeyen/şüpheli sayılar AI'ın kendi hesapladığı, yanlış satırdan aldığı ya da tablolarda olmayan değerler olabilir; bunları savunmadan önce ham kayıtlardan elle kontrol edin.")
    if o.get("iliski_yok"):
        st.error(f"❌ {o['iliski_yok']} ilişki iddiası ham kayıtlarda YOK (örn. cevapta geçen kişi o S/N'yi hiç çalışmamış). Aşağıdaki '2) Ham kayıtlar' bölümüne bakın.")
    with st.expander("1) Cevaptaki sayılar → hangi tablo / satır / kolon", expanded=True):
        if R["sayilar"].empty:
            st.info("Cevapta kontrol edilecek sayı bulunamadı.")
        else:
            st.dataframe(R["sayilar"], use_container_width=True, hide_index=True)
    with st.expander("2) Cevapta geçenlerin HAM KAYITLARI (kendin topla, kendin gör)"):
        K = R["kanit"]
        if len(R["iliski"]):
            st.markdown("**İlişki kontrolü** — cevaptaki 'bu S/N'yi şu kişi/tezgah yaptı' iddiaları ham kayıtta var mı?")
            st.dataframe(R["iliski"], use_container_width=True, hide_index=True)
        if not (K["sn"] or K["kisi"] or K["tezgah"]):
            st.info("Cevapta/soruda tanınan bir S/N, operatör veya tezgah yok.")
        for s, k in K["sn"].items():
            st.markdown(f"**S/N {s}** — tüm kayıtların toplamı {k['toplam']} sa (üretim {k['uretim']} sa)")
            st.dataframe(k["satirlar"], use_container_width=True, hide_index=True)
            st.dataframe(k["ozet"], use_container_width=True, hide_index=True)
        for fn, k in K["kisi"].items():
            tr = "" if not k["tarih"] else f" · {k['tarih'][0]:%d.%m.%Y} – {k['tarih'][1]:%d.%m.%Y}"
            st.markdown(f"**{fn}** — {k['satir']} satır{tr} · yazımlar: {', '.join(k['yazimlar'])}")
            st.dataframe(k["durum"], use_container_width=True, hide_index=True)
            if not k["parca"].empty:
                st.dataframe(k["parca"], use_container_width=True, hide_index=True)
        for t, k in K["tezgah"].items():
            st.markdown(f"**{t}** — {k['satir']} satır")
            st.dataframe(k["durum"], use_container_width=True, hide_index=True)
    with st.expander("3) Formül nereden çıkıyor? (gerçek satırlarla yeniden hesap)", expanded=bool(R["formuller"])):
        if not R["formuller"]:
            st.info("Cevapta tanınan bir metrik yok. Metrik adı (örn. Emsal Bazlı Sapma, Kullanılabilirlik, OEE, Adet, Hafta) geçtiğinde burada formül ve dökümü görünür.")
        for f in R["formuller"]:
            st.markdown(f"##### {f['ad']}")
            st.code(f["formul"], language="text")
            if f["kaynak"]:
                st.caption("Kaynak kolonlar: " + ", ".join(f["kaynak"]))
            if f["aciklama"]:
                st.caption(f["aciklama"])
            if f.get("not"):
                st.info(f["not"])
            for ex in f["ornekler"]:
                st.markdown(f"**▸ {ex['baslik']}**   {ex['durum']}" + (f"   ·   {ex['iddia_durum']}" if ex.get("iddia_durum") else ""))
                st.table(pd.DataFrame(ex["adimlar"], columns=["Adım", "Değer"]))
                if ex.get("hesap") is not None:
                    st.markdown(f"Yeniden hesap: **{ex['hesap']}** · tabloda: **{ex['tablo'] if ex['tablo'] is not None else '—'}**")
                for ad, tab in ex.get("tablolar", []):
                    st.caption(ad)
                    st.dataframe(tab, use_container_width=True, hide_index=True)
                if ex.get("not"):
                    st.info(ex["not"])
    return R
