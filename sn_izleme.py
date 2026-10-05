# -*- coding: utf-8 -*-
"""
sn_izleme.py — Seri numarası (S/N) bazlı parça izleme, adet ve hafta özeti.

KURAL (kullanıcı): parça adedi S/N'ye göre sayılır. Aynı S/N'nin bütün kayıtları
(vardiya/operatör değişse de, biri diğerini devam ettirse de) birleşir ve 1 ADET olur.
Pilot: 2547M01G03 (kısa kod M01G03), operasyon 60.

HAFTA: Perşembe başlar, Çarşamba biter. Hafta no = (tarih − 3 gün)'ün ISO hafta numarası
(örn. Per 6 Ağu 2026 → hafta 32). Bir S/N, TAMAMLANDIĞI (son kayıt) haftaya yazılır.

SET-UP: Veride set-up satırları "60-SETUP" gibi operasyon metniyle ve çoğunlukla
S/N'siz girilir. Bu yüzden set-up, S/N'ye değil PARÇA+OPERASYON'a bağlanır; adet yanında
ayrı gösterilir ("setuplarla birlikte kaç adet"). Sadece "SETUP" yazanlar operasyonu
belirsiz sayılır, ayrıca raporlanır.

Beklenen kolonlar (ETL): TARİH, TEZGAH, VARDİYA, Operator_Kod, Durum (Üretim/Set_Up/…),
Sure_Saat, "S/N", "PARÇA NO_ÜRETİM_GİRİLEN", "OPERASYON_NO", opsiyonel "ÜRETİM_C/T",
BAŞLANGIÇ_SAAT, BİTİŞ_SAAT. (Ham Excel'deki boşluklu isimler de kabul edilir.)
"""
from __future__ import annotations

import numpy as np
import pandas as pd

UZUN_KOD_ONEKI = {"M01G03": "2547"}   # kısa kod -> uzun koddaki ön ek
PILOT = [("M01G03", "60")]            # (kısa kod, operasyon) — yenisini buraya ekleyin
ALARM_TOLERANS = 0.20                 # katalog C/T'yi %20'den fazla aşarsa "yavaş"


def _kol(df, *adaylar):
    for a in adaylar:
        if a in df.columns:
            return a
    return None


def ad_norm(x) -> str:
    """Operatör adı normalizasyonu: ham veride aynı kişi hem 'Hüseyin Yılmaz' hem 'HÜSEYİN YILMAZ' yazılmış;
    Türkçe-duyarlı büyük harfe çevirip boşlukları sadeleştirir (kişi iki satıra bölünmesin)."""
    if x is None or (isinstance(x, float) and np.isnan(x)):
        return x
    return " ".join(str(x).replace("i", "İ").replace("ı", "I").upper().split())


def uzun_kod(kisa: str) -> str:
    return UZUN_KOD_ONEKI.get(kisa, "") + kisa


def hafta_no(tarih: pd.Series) -> pd.Series:
    """Perşembe-başlangıçlı hafta numarası."""
    return (tarih - pd.Timedelta(days=3)).dt.isocalendar().week.astype(int)


def hafta_baslangic(tarih: pd.Series) -> pd.Series:
    return (tarih - pd.to_timedelta((tarih.dt.weekday - 3) % 7, unit="D")).dt.normalize()


def sn_izleme(df: pd.DataFrame, parca_kisa: str = "M01G03", operasyon: str = "60", op_adi=lambda k: k):
    """Döner: (ozet, detay, setup, kapsam)
    ozet  : S/N başına 1 satır = 1 ADET (toplam saat, operatörler, vardiyalar, bölünmüş mü, alarm)
    detay : S/N'nin her kaydı (kim, vardiya, saat aralığı, süre)
    setup : bu parça+operasyonun set-up kayıtları (S/N'siz olabilir)
    kapsam: sayılar ve veri kalitesi uyarıları (kapsam['mesaj'] doluysa hesap yapılamadı)
    """
    c_parca = _kol(df, "PARÇA NO_ÜRETİM_GİRİLEN", "PARÇA NO ÜRETİM GİRİLEN")
    c_op = _kol(df, "OPERASYON_NO", "OPERASYON NO")
    c_kisi = _kol(df, "Operator_Kod", "OPERATÖR")
    c_ct = _kol(df, "ÜRETİM_C/T", "ÜRETİM C/T")
    bos = pd.DataFrame()
    if any(c is None for c in [c_parca, c_op, c_kisi, _kol(df, "Durum"), _kol(df, "Sure_Saat"), _kol(df, "S/N"), _kol(df, "TARİH")]):
        return bos, bos, bos, {"mesaj": "Gerekli kolonlar yok (S/N DB'de olmayabilir)."}

    parca_mask = df[c_parca].astype(str).str.contains(parca_kisa, na=False)
    op_s = df[c_op].astype(str).str.strip()
    kapsam = {"uzun_kod": uzun_kod(parca_kisa), "operasyon": str(operasyon)}

    # ---- SET-UP (parça+operasyon bazlı)
    s_tum = df[parca_mask & (df["Durum"] == "Set_Up")]
    s_op = s_tum[op_s.loc[s_tum.index].str.startswith(str(operasyon))].copy()
    s_bel = s_tum[op_s.loc[s_tum.index].str.upper().eq("SETUP")]
    kapsam.update(setup_kayit=int(len(s_op)), setup_saat=float(s_op["Sure_Saat"].sum()),
                  setup_belirsiz_kayit=int(len(s_bel)), setup_belirsiz_saat=float(s_bel["Sure_Saat"].sum()))
    s_op["Operator"] = s_op[c_kisi].map(op_adi).map(ad_norm)
    setup = s_op[[c for c in ["TARİH", "VARDİYA", "TEZGAH", "Operator", c_op, "Sure_Saat", "S/N"] if c in s_op.columns]] \
        .sort_values("TARİH").reset_index(drop=True)

    # ---- ÜRETİM (S/N bazlı)
    u = df[parca_mask & (df["Durum"] == "Üretim") & (op_s == str(operasyon))].copy()
    kapsam.update(uretim_kaydi=int(len(u)), sn_bos_kayit=int(u["S/N"].isna().sum()),
                  sn_bos_saat=float(u.loc[u["S/N"].isna(), "Sure_Saat"].sum()))
    u = u[u["S/N"].notna()]
    if u.empty:
        return bos, bos, setup, kapsam

    u["Operator"] = u[c_kisi].map(op_adi).map(ad_norm)
    kol = [c for c in ["S/N", "TARİH", "VARDİYA", "BAŞLANGIÇ_SAAT", "BİTİŞ_SAAT", "TEZGAH", "Operator", "Sure_Saat"] if c in u.columns]
    sirala = ["S/N", "TARİH"] + (["BAŞLANGIÇ_SAAT"] if "BAŞLANGIÇ_SAAT" in u.columns else [])
    detay = u[kol].sort_values(sirala).reset_index(drop=True)

    agg = dict(
        Toplam_Saat=("Sure_Saat", "sum"), Kayit=("Sure_Saat", "size"),
        Ilk_Tarih=("TARİH", "min"), Son_Tarih=("TARİH", "max"),
        Operatorler=("Operator", lambda s: " + ".join(dict.fromkeys(s))),
    )
    if "VARDİYA" in u.columns:
        agg["Vardiyalar"] = ("VARDİYA", lambda s: " + ".join(dict.fromkeys(s.dropna().astype(str))))
    if "TEZGAH" in u.columns:
        agg["Tezgah"] = ("TEZGAH", lambda s: " / ".join(dict.fromkeys(s)))
    if c_ct:
        agg["Katalog_CT"] = (c_ct, "max")
    ozet = u.groupby("S/N").agg(**agg).reset_index()
    ozet["Adet"] = 1
    ozet["Bolunmus_Is"] = ozet["Kayit"] > 1
    med = ozet["Toplam_Saat"].median()
    ozet["Medyana_Gore_%"] = (ozet["Toplam_Saat"] - med) / med * 100 if med > 0 else np.nan
    if "Katalog_CT" in ozet:
        ref = ozet["Katalog_CT"].where(ozet["Katalog_CT"] > 0)
        ozet["Alarm"] = np.where(ref.notna() & (ozet["Toplam_Saat"] > ref * (1 + ALARM_TOLERANS)), "🔴 Yavaş", "🟢 Normal")
    ozet["Hafta"] = hafta_no(ozet["Son_Tarih"])
    ozet = ozet.sort_values("Son_Tarih").reset_index(drop=True)
    kapsam.update(sn_sayisi=int(len(ozet)), medyan_saat=float(med), bolunmus_sn=int(ozet["Bolunmus_Is"].sum()))
    return ozet.round({"Toplam_Saat": 2, "Medyana_Gore_%": 1}), detay.round({"Sure_Saat": 2}), setup, kapsam


def sn_hafta_ozeti(ozet: pd.DataFrame, setup: pd.DataFrame) -> pd.DataFrame:
    """Hafta (Per→Çar) başına: adet, ortalama süre/adet, günlük ortalama adet, set-up saati."""
    if ozet.empty:
        return pd.DataFrame()
    o = ozet.copy()
    o["Hafta_Baslangic"] = hafta_baslangic(o["Son_Tarih"])
    h = o.groupby(["Hafta", "Hafta_Baslangic"]).agg(
        Adet=("Adet", "sum"), Ort_Sure_Sa=("Toplam_Saat", "mean"), Medyan_Sure_Sa=("Toplam_Saat", "median"),
        Toplam_Uretim_Sa=("Toplam_Saat", "sum"), Bolunmus=("Bolunmus_Is", "sum"),
        Uretim_Gunu=("Son_Tarih", "nunique"),
    ).reset_index()
    h["Gunluk_Ort_Adet"] = h["Adet"] / h["Uretim_Gunu"]
    h["Hafta_Bitis"] = h["Hafta_Baslangic"] + pd.Timedelta(days=6)
    if setup is not None and not setup.empty:
        s = setup.copy()
        s["Hafta"] = hafta_no(s["TARİH"])
        h = h.merge(s.groupby("Hafta")["Sure_Saat"].sum().rename("Setup_Sa").reset_index(), on="Hafta", how="left")
    h["Setup_Sa"] = h.get("Setup_Sa", 0).fillna(0)
    return h.round({"Ort_Sure_Sa": 2, "Medyan_Sure_Sa": 2, "Toplam_Uretim_Sa": 1, "Gunluk_Ort_Adet": 2, "Setup_Sa": 1})


def _op_sade(seri: pd.Series) -> pd.Series:
    return seri.astype(str).str.strip()


def _pay_analizi(detay: pd.DataFrame, ozet: pd.DataFrame, kolon: str, medyan: float) -> pd.DataFrame:
    """Operatör/vardiya/tezgah kırılımı. Bölünmüş S/N'de adet, o kişinin SAAT PAYI oranında paylaşılır
    (Adet_Payi = Σ kendi_saati / S/N_toplam_saati). Esdeger_Ort_Sa = Toplam_Saat / Adet_Payi."""
    if detay.empty or kolon not in detay.columns:
        return pd.DataFrame()
    o = detay.merge(ozet[["S/N", "Toplam_Saat", "Operatorler"]], on="S/N", suffixes=("", "_sn"))
    o["Pay"] = o["Sure_Saat"] / o["Toplam_Saat"]
    o["Tek_Kisi"] = ~o["Operatorler"].str.contains(r"\+", regex=True)
    g = o.groupby(kolon)
    r = g.agg(Kayit=("Sure_Saat", "size"), SN_Sayisi=("S/N", "nunique"), Toplam_Saat=("Sure_Saat", "sum"),
              Adet_Payi=("Pay", "sum"), Ilk_Tarih=("TARİH", "min"), Son_Tarih=("TARİH", "max")).reset_index()
    r["Esdeger_Ort_Sa"] = r["Toplam_Saat"] / r["Adet_Payi"]
    if kolon == "Operator":   # tek başına bitirdiği S/N'lerin süre ortalaması (karıştırmasız kıyas)
        solo = o[o["Tek_Kisi"]].groupby(kolon).agg(Tek_Basina_Adet=("S/N", "nunique")).reset_index()
        solo_ort = o[o["Tek_Kisi"]].drop_duplicates(["S/N"]).groupby(kolon)["Toplam_Saat"].mean().rename("Tek_Basina_Ort_Sa").reset_index()
        r = r.merge(solo, on=kolon, how="left").merge(solo_ort, on=kolon, how="left")
    r["Medyana_Gore_%"] = (r["Esdeger_Ort_Sa"] - medyan) / medyan * 100 if medyan > 0 else np.nan
    return r.sort_values("Adet_Payi", ascending=False).round(
        {"Toplam_Saat": 2, "Adet_Payi": 2, "Esdeger_Ort_Sa": 2, "Tek_Basina_Ort_Sa": 2, "Medyana_Gore_%": 1}).reset_index(drop=True)


def sn_gunluk(ozet: pd.DataFrame) -> pd.DataFrame:
    """Gün bazında (tamamlanma tarihi) adet."""
    if ozet.empty:
        return pd.DataFrame()
    o = ozet.copy()
    o["Gun"] = o["Son_Tarih"].dt.normalize()
    g = o.groupby("Gun").agg(Adet=("Adet", "sum"), Ort_Sure_Sa=("Toplam_Saat", "mean"),
                            Toplam_Uretim_Sa=("Toplam_Saat", "sum"), Bolunmus=("Bolunmus_Is", "sum")).reset_index()
    g["Haftanin_Gunu"] = g["Gun"].dt.weekday.map(dict(enumerate(["Pzt", "Sal", "Çar", "Per", "Cum", "Cmt", "Paz"])))
    g["Hafta"] = hafta_no(g["Gun"])
    return g.round({"Ort_Sure_Sa": 2, "Toplam_Uretim_Sa": 1})


def sn_rota(df: pd.DataFrame, parca_kisa: str = "M01G03", op_adi=lambda k: k) -> pd.DataFrame:
    """A→Z: parçanın TÜM operasyonlardaki yolculuğu. Satır = S/N; kolonlar = operasyon başına saat
    + o operasyonu yapan kişiler. Aynı S/N başka parça koduna da yazılmışsa 'Baska_Parca_Kodu' dolar."""
    c_parca = _kol(df, "PARÇA NO_ÜRETİM_GİRİLEN", "PARÇA NO ÜRETİM GİRİLEN")
    c_op = _kol(df, "OPERASYON_NO", "OPERASYON NO")
    c_kisi = _kol(df, "Operator_Kod", "OPERATÖR")
    if None in (c_parca, c_op, c_kisi) or "S/N" not in df.columns:
        return pd.DataFrame()
    u = df[df[c_parca].astype(str).str.contains(parca_kisa, na=False) & (df["Durum"] == "Üretim") & df["S/N"].notna()].copy()
    if u.empty:
        return pd.DataFrame()
    u["Op"] = _op_sade(u[c_op])
    u["Operator"] = u[c_kisi].map(op_adi).map(ad_norm)
    saat = u.pivot_table(index="S/N", columns="Op", values="Sure_Saat", aggfunc="sum")
    kisi = u.pivot_table(index="S/N", columns="Op", values="Operator",
                         aggfunc=lambda s: " + ".join(dict.fromkeys(s)))
    ops = sorted(saat.columns, key=lambda x: (not x.split("-")[0].isdigit(), int(x.split("-")[0]) if x.split("-")[0].isdigit() else 0, x))
    r = pd.DataFrame(index=saat.index)
    for op in ops:
        r[f"Op{op}_Sa"] = saat[op]
        r[f"Op{op}_Kim"] = kisi[op]
    r["Toplam_Sa_Tum_Op"] = saat.sum(axis=1)
    r["Op_Sayisi"] = saat.notna().sum(axis=1)
    r["Ilk_Tarih"] = u.groupby("S/N")["TARİH"].min()
    r["Son_Tarih"] = u.groupby("S/N")["TARİH"].max()
    diger = df[df["S/N"].isin(r.index) & ~df[c_parca].astype(str).str.contains(parca_kisa, na=False)]
    r["Baska_Parca_Kodu"] = diger.groupby("S/N")[c_parca].agg(lambda s: ", ".join(sorted(set(s.astype(str)))))
    return r.reset_index().sort_values("Ilk_Tarih").round(2).reset_index(drop=True)


def parca_duruslari(df: pd.DataFrame, parca_kisa: str = "M01G03", operasyon: str = "60", op_adi=lambda k: k) -> pd.DataFrame:
    """SADECE BELİRTİLEN OPERASYONDAKİ duruş kayıtlarını sebep metniyle getirir."""
    c_parca = _kol(df, "PARÇA NO_ÜRETİM_GİRİLEN", "PARÇA NO ÜRETİM GİRİLEN")
    c_op = _kol(df, "OPERASYON_NO", "OPERASYON NO")
    c_kisi = _kol(df, "Operator_Kod", "OPERATÖR")
    c_sebep = _kol(df, "Durus_Sebebi_Ham") or c_op
    if None in (c_parca, c_kisi, c_sebep, c_op):
        return pd.DataFrame()
        
    d = df[
        df[c_parca].astype(str).str.contains(parca_kisa, na=False) & 
        df["Durum"].astype(str).str.contains("Duruş") &
        (df[c_op].astype(str).str.strip() == str(operasyon))
    ].copy()
    
    if d.empty:
        return pd.DataFrame()
    d["Operator"] = d[c_kisi].map(op_adi).map(ad_norm)
    d["Sebep"] = d[c_sebep]
    kol = [c for c in ["TARİH", "VARDİYA", "TEZGAH", "Operator", "Sebep", "Sure_Saat", "S/N"] if c in d.columns]
    return d[kol].sort_values("TARİH").round({"Sure_Saat": 2}).reset_index(drop=True)


def sn_tum_analiz(df: pd.DataFrame, parca_kisa: str, operasyon: str, op_adi=lambda k: k) -> dict:
    """Tek çağrıda her şey; sayfa, AI ve doğrulama betiği aynı sözlüğü kullanır."""
    ozet, detay, setup, k = sn_izleme(df, parca_kisa, operasyon, op_adi)
    t = {"ozet": ozet, "detay": detay, "setup": setup, "kapsam": k}
    if k.get("mesaj") or ozet.empty:
        return t
    med = k["medyan_saat"]
    t["hafta"] = sn_hafta_ozeti(ozet, setup)
    t["gunluk"] = sn_gunluk(ozet)
    t["operator"] = _pay_analizi(detay, ozet, "Operator", med)
    t["vardiya"] = _pay_analizi(detay, ozet, "VARDİYA", med)
    t["tezgah"] = _pay_analizi(detay, ozet, "TEZGAH", med)
    sirali = ozet.sort_values(["Toplam_Saat", "Son_Tarih"], ascending=[False, True])
    cols = [c for c in ["S/N", "Toplam_Saat", "Kayit", "Operatorler", "Vardiyalar", "Tezgah", "Son_Tarih"] if c in ozet.columns]
    t["uclar"] = pd.concat([sirali.head(5)[cols].assign(Grup="EN YAVAŞ 5"),
                            sirali.tail(5).iloc[::-1][cols].assign(Grup="EN HIZLI 5")]).reset_index(drop=True)
    t["rota"] = sn_rota(df, parca_kisa, op_adi)
    t["durus"] = parca_duruslari(df, parca_kisa, operasyon, op_adi)
    return t


_AI_TABLOLAR = [  # (anahtar, TABLO no, başlık)
    ("ozet", 10, "S/N BAZLI ADET — her satır 1 ADET. Toplam_Saat = S/N'nin tüm kayıtları; Bolunmus_Is=True: birden fazla kayıt; "
                 "Alarm: Katalog_CT'yi %{tol} aşarsa Yavaş; Hafta Perşembe→Çarşamba, S/N tamamlandığı (Son_Tarih) haftaya yazılır"),
    ("detay", 11, "S/N KAYIT DETAYI — her S/N için kim, hangi vardiya, saat aralığı, süre"),
    ("hafta", 12, "HAFTALIK ADET ÖZETİ (Perşembe→Çarşamba; Gunluk_Ort_Adet = Adet / üretim yapılan gün)"),
    ("setup", 13, "SET-UP KAYITLARI (parça+operasyona bağlı, S/N'siz olabilir)"),
    ("gunluk", 14, "GÜNLÜK ADET (tamamlanma tarihine göre)"),
    ("operator", 15, "OPERATÖR ANALİZİ — Adet_Payi: bölünmüş S/N'de adet saat payı oranında paylaşılır; Esdeger_Ort_Sa = Toplam_Saat/Adet_Payi; "
                     "Tek_Basina_*: S/N'yi tek başına bitirdiği işler; Medyana_Gore_% medyan süreye göre fark"),
    ("vardiya", 16, "VARDİYA ANALİZİ (aynı Adet_Payi mantığı)"),
    ("tezgah", 17, "TEZGAH ANALİZİ (aynı Adet_Payi mantığı)"),
    ("rota", 18, "PARÇA ROTASI A→Z — S/N'nin TÜM operasyonlardaki saati ve kimin yaptığı; Baska_Parca_Kodu doluysa aynı S/N başka parça koduna da yazılmış (veri hatası şüphesi)"),
    ("durus", 19, "PARÇAYA YAZILMIŞ DURUŞLAR (sebep metniyle)"),
    ("uclar", 20, "EN YAVAŞ 5 ve EN HIZLI 5 S/N (hazır sıralama; eşit sürelerde Son_Tarih'i eski olan önce)"),
]


def sn_ai_baglami(df: pd.DataFrame, pilot=PILOT, op_adi=lambda k: k) -> str:
    """4_AI_Asistan.py bağlamına eklenecek metin. Sayfadaki tablolarla AYNI sözlükten üretilir."""
    parcalar = []
    for kisa, op in pilot:
        t = sn_tum_analiz(df, kisa, op, op_adi)
        k = t["kapsam"]
        if k.get("mesaj"):
            parcalar.append(f"### TABLO 10: S/N İZLEME — {k['mesaj']} Bu yüzden S/N sorularına cevap VERİLEMEZ; kullanıcıya bunu söyle.")
            continue
        if t["ozet"].empty:
            continue
        parcalar.append(
            f"## S/N İZLEME — {k['uzun_kod']} operasyon {op}: {k['sn_sayisi']} adet, medyan {k['medyan_saat']:.2f} sa, "
            f"ortalama {t['ozet']['Toplam_Saat'].mean():.2f} sa, toplam {t['ozet']['Toplam_Saat'].sum():.2f} sa, "
            f"{k['bolunmus_sn']} bölünmüş iş, {int((t['ozet']['Alarm'] == '🔴 Yavaş').sum()) if 'Alarm' in t['ozet'] else 0} yavaş S/N, "
            f"set-up {k['setup_saat']:.1f} sa ({k['setup_kayit']} kayıt). "
            f"ADET KURALI: aynı S/N'nin tüm kayıtları birleşir, 1 sayılır.")
        for anahtar, no, baslik in _AI_TABLOLAR:
            tab = t.get(anahtar)
            if tab is None or tab.empty:
                continue
            parcalar.append(f"### TABLO {no}: {baslik.format(tol=f'%{int(ALARM_TOLERANS*100)}')}\n" + tab.to_csv(index=False))
        parcalar.append(f"(Operasyonu belirsiz 'SETUP' kayıtları: {k['setup_belirsiz_kayit']} adet, {k['setup_belirsiz_saat']:.1f} sa — op {op}'a eklenmedi. "
                        f"S/N'si boş üretim kaydı: {k['sn_bos_kayit']}, {k['sn_bos_saat']:.1f} sa — adede dahil değil.)")
    return "\n\n".join(parcalar)


SN_AI_KURALI = (
    "6. Kullanıcı seri numarası (S/N), adet, parça süresi, kim çalıştı, vardiya, 'week/hafta N', günlük ortalama, set-up, "
    "operatör/vardiya/tezgah karşılaştırması, parçanın operasyonlar arası yolculuğu (rota) veya o parçadaki duruşları sorarsa "
    "TABLO 10-20'yi kullan. Adet = S/N sayısı (aynı S/N'nin kayıtları birleşir, 1 adet). Hafta Perşembe'den başlar. "
    "Bölünmüş işte operatör kıyasını Adet_Payi/Esdeger_Ort_Sa ile yap, 'tek başına bitirdiği' işleri ayrıca belirt. "
    "'En yavaş/en hızlı' sorularında TABLO 20'yi kullan, kendin sıralama yapma. Cevaba S/N, adet, saat, operatör, vardiya yaz; kullanıcı 'Parça S/N İzleme' sekmesinde doğrulayacak. "
    "Tablolar yalnızca pilot parça/operasyonu kapsar; başka parça sorulursa 'henüz S/N izleme yok' de."
)


# --------------------------------------------------------------------------- STREAMLIT
def render_sn_sekmesi(df: pd.DataFrame, pilot=PILOT, op_adi=lambda k: k):
    """app4.py: `with sekme[T_SN]: render_sn_sekmesi(df)`"""
    import streamlit as st

    if "S/N" not in df.columns:
        st.error("Veride **S/N** kolonu yok. config.CANONICAL_COLUMNS + ETL'e S/N eklenmeden bu sekme ve AI S/N sorularına cevap veremez.")
        return
    secenek = [f"{uzun_kod(k)} — op {o}" for k, o in pilot]
    i = st.selectbox("Parça / operasyon", range(len(pilot)), format_func=lambda x: secenek[x], key="sn_parca")
    kisa, op = pilot[i]
    t = sn_tum_analiz(df, kisa, op, op_adi)
    ozet, detay, setup, k = t["ozet"], t["detay"], t["setup"], t["kapsam"]
    if ozet.empty:
        st.info("Seçili filtrelerde bu parça/operasyon için S/N'li üretim kaydı yok.")
        return

    def indir(tablo, ad, anahtar):
        st.download_button(f"⬇️ {ad} (CSV)", tablo.to_csv(index=False).encode("utf-8-sig"),
                           file_name=f"{uzun_kod(kisa)}_op{op}_{anahtar}.csv", mime="text/csv", key=f"dl_{anahtar}")

    c1, c2, c3, c4, c5 = st.columns(5)
    c1.metric("Adet (S/N)", k["sn_sayisi"])
    c2.metric("Medyan süre/adet (sa)", f"{k['medyan_saat']:.2f}")
    c3.metric("Bölünmüş iş", k["bolunmus_sn"])
    c4.metric("Set-up (sa)", f"{k['setup_saat']:.1f}")
    c5.metric("Yavaş S/N", int((ozet["Alarm"] == "🔴 Yavaş").sum()) if "Alarm" in ozet else 0)
    if k["sn_bos_kayit"]:
        st.warning(f"{k['sn_bos_kayit']} üretim kaydında S/N boş ({k['sn_bos_saat']:.1f} sa) — adede dahil değil.")
    if k["setup_belirsiz_kayit"]:
        st.caption(f"Operasyonu belirsiz 'SETUP' kaydı: {k['setup_belirsiz_kayit']} ({k['setup_belirsiz_saat']:.1f} sa) — op {op}'a eklenmedi.")

    sek = st.tabs(["📅 Hafta & Gün", "📦 S/N Adet", "🔎 S/N Doğrula", "🧭 Parça Rotası (A→Z)", "👷 Operatör",
                   "🕒 Vardiya", "🏭 Tezgah", "🛠️ Set-up & Duruş", "🤖 AI'a giden veri"])
    with sek[0]:
        st.markdown("**Hafta (Perşembe→Çarşamba)**"); st.dataframe(t["hafta"], use_container_width=True, hide_index=True)
        st.markdown("**Gün bazlı adet (tamamlanma tarihi)**"); st.dataframe(t["gunluk"], use_container_width=True, hide_index=True)
        indir(t["hafta"], "Hafta özeti", "hafta")
    with sek[1]:
        st.dataframe(ozet, use_container_width=True, hide_index=True)
        indir(ozet, "S/N adet tablosu", "sn")
    with sek[2]:
        # YENİ EKLENEN: Operatöre göre S/N listesini daraltma
        tum_operatorler = ["Tümü"] + sorted(detay["Operator"].dropna().unique().tolist())
        secili_op = st.selectbox("Önce Operatör Seç (Opsiyonel):", tum_operatorler, key="op_sec")
        
        if secili_op != "Tümü":
            uygun_snler = detay[detay["Operator"] == secili_op]["S/N"].unique().tolist()
        else:
            uygun_snler = ozet["S/N"].tolist()
            
        sn = st.selectbox("Seri numarası", uygun_snler, key="sn_sec")
        
        st.markdown("**Bu operasyondaki ham kayıtlar**")
        st.dataframe(detay[detay["S/N"] == sn], use_container_width=True, hide_index=True)
        if not t["rota"].empty:
            st.markdown("**Bu S/N'nin tüm operasyonlardaki yolculuğu**")
            st.dataframe(t["rota"][t["rota"]["S/N"] == sn], use_container_width=True, hide_index=True)
        st.caption("AI bir S/N için süre/operatör söylediğinde bu tablolarla birebir tutmalıdır.")
        indir(detay, "Tüm S/N kayıt detayı", "detay")
    with sek[3]:
        st.info("""
        📍 **Parça Rotası (İzlenebilirlik / Traceability)**
        Bir parçanın doğumundan bitişine kadar fabrikanın içindeki seyahatnamesidir. 
        Son operasyonda (Örn: 60) bir hata çıkarsa, geçmişe dönüp parçayı önceki operasyonlarda kimin işlediğini anında bulmanızı sağlar.
        """)
        st.dataframe(t["rota"], use_container_width=True, hide_index=True)
        indir(t["rota"], "Parça rotası", "rota")
        
    with sek[4]:
        st.info("""
        ⚖️️ **Operatör Performans ve Adalet Sistemi**
        Bölünmüş işlerde emeği doğru dağıtmak için hesaplanan saf performans metrikleridir:
        * **Kayıt:** Ustanın Excel'e 'Başladım/Bittim' diyerek girdiği satır sayısı.
        * **SN Sayısı:** Ustanın elini sürdüğü benzersiz parça sayısı.
        * **Adet Payı (Net Üretim):** 1 parçayı 2 usta beraber işlediyse sistem parçayı adilce böler (Örn: 0.5 Yaşar'a, 0.5 Özcan'a yazar).
        * **Eşdeğer Ort Sa:** Ustanın ortaklaşa yaptığı işler dahil, 1 tam parçayı bitirme hızı.
        * **Tek Başına Adet & Ort Sa:** Ustanın hiç kimsenin karışmadığı, tek başına alıp bitirdiği parça sayısı ve hızı.
        * **Medyana Göre %:** Ustanın genel parça ortalamasına (medyan) göre ne kadar yavaş (+) veya hızlı (-) olduğu.
        """)
        st.dataframe(t["operator"], use_container_width=True, hide_index=True)
        indir(t["operator"], "Operatör analizi", "operator")
        
    with sek[5]:
        st.info("""
        🕒 **Vardiya Analizi**
        Vardiyaların performans kıyası:
        * **Kayıt & SN Sayısı:** Bu vardiyada girilen satır ve işlem gören benzersiz parça sayısı.
        * **Adet Payı (Net Üretim):** Vardiya değişiminde bölünen işlerin saate göre hesaplanmış net parça karşılığı.
        * **Eşdeğer Ort Sa:** Bu vardiyanın 1 tam parçayı bitirme hızı.
        * **Medyana Göre %:** Vardiyanın ortalama hızının, genel parça ortalamasına (medyan) göre sapması.
        """)
        st.dataframe(t["vardiya"], use_container_width=True, hide_index=True)
        
    with sek[6]:
        st.info("""
        🏭 **Tezgah Analizi**
        Tezgahların yük ve performans kıyası:
        * **Kayıt & SN Sayısı:** Bu tezgahta yapılan işlem ve geçen parça sayısı.
        * **Adet Payı (Net Üretim):** Bu tezgahta tamamlanan net parça karşılığı.
        * **Eşdeğer Ort Sa:** Bu tezgahın 1 tam parçayı işleme hızı.
        * **Medyana Göre %:** Bu tezgahtaki işlem hızının genel medyana göre sapması.
        """)
        st.dataframe(t["tezgah"], use_container_width=True, hide_index=True)
        
    with sek[7]:
        st.markdown(f"**Set-up (op {op}, toplam {k['setup_saat']:.1f} sa)**")
        st.dataframe(setup, use_container_width=True, hide_index=True)
        st.markdown("**Parçaya yazılmış duruşlar (Sadece seçili operasyon için, sebep metniyle)**")
        st.dataframe(t["durus"], use_container_width=True, hide_index=True)
        
    with sek[8]:
        st.caption("Asistan bu metni okuyor; yukarıdaki tablolarla birebir aynı veri.")
        st.code(sn_ai_baglami(df, pilot, op_adi), language="csv")