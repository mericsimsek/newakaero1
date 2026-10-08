# -*- coding: utf-8 -*-
"""
sn_izleme.py — Seri numarası (S/N) bazlı parça izleme, adet, hafta özeti ve hafta detayı.

KURAL: parça adedi S/N'ye göre sayılır. Aynı S/N'nin bütün kayıtları (vardiya/operatör değişse de) birleşir, 1 ADET olur.
Pilot: 2547M01G03 (kısa kod M01G03), operasyon 60.
HAFTA: Perşembe başlar, Çarşamba biter. Hafta no = (tarih − 3 gün)'ün ISO hafta numarası. S/N, tamamlandığı haftaya yazılır.
SET-UP / DURUŞ: ham veride operasyon numarası değil metin ("60-SETUP", "ITEM TEKRARI") taşırlar; parça+operasyona bağlanırlar.
SEÇİCİ: "Tümü" seçilirse pilot sınırı olmadan TÜM parça/operasyonların S/N adedi (gün süzgeçli) görünür; tek tek parça/operasyon da seçilebilir.
"""
from __future__ import annotations

import re
import numpy as np
import pandas as pd

UZUN_KOD_ONEKI = {"M01G03": "2547"}
PILOT = [("M01G03", "60")]
ALARM_TOLERANS = 0.20
DURUM_SIRASI = ["Üretim", "Plansız_Duruş", "Set_Up", "Yemek_Mola", "Rework", "Sınıflandırılmamış"]
DURUM_RENK = {"Üretim": "#2E7D32", "Plansız_Duruş": "#C62828", "Set_Up": "#F9A825", "Yemek_Mola": "#8D6E63",
              "Rework": "#EF6C00", "Sınıflandırılmamış": "#90A4AE"}
GUN_ADI = ["Pzt", "Sal", "Çar", "Per", "Cum", "Cmt", "Paz"]


def _kol(df, *adaylar):
    for a in adaylar:
        if a in df.columns:
            return a
    return None



def ad_norm(x) -> str:
    if x is None or (isinstance(x, float) and np.isnan(x)):
        return x
    return " ".join(str(x).replace("i", "İ").replace("ı", "I").upper().split())


UZUN_HARITA: dict = {}   


def _uzun_kolonu(df):
    for c in df.columns:
        f = str(c).replace("_", " ").replace("\n", " ").upper().replace("İ", "I")
        if "UZUN" in f and "REFERANS" in f:
            return c
    return None


def _ilk_dolu(s) -> str:
    v = pd.Series(list(s)).dropna().astype(str).str.strip()
    v = v[(v != "") & (v.str.lower() != "nan")]
    return v.value_counts().index[0] if len(v) else ""


def uzun_haritasi_guncelle(df: pd.DataFrame) -> dict:
    cu, cp = _uzun_kolonu(df), _kol(df, "PARÇA NO_ÜRETİM_GİRİLEN", "PARÇA NO ÜRETİM GİRİLEN")
    if not cu or not cp:
        return UZUN_HARITA
    d = df[[cp, cu]].dropna().astype(str)
    d[cp], d[cu] = d[cp].str.strip(), d[cu].str.strip()
    for kisa, g in d.groupby(cp):
        uyumlu = g[g[cu].str.endswith(kisa)][cu]
        sec = _ilk_dolu(uyumlu if len(uyumlu) else g[cu])
        if sec:
            UZUN_HARITA[kisa] = sec
    return UZUN_HARITA


def uzun_kod(kisa: str) -> str:
    return UZUN_HARITA.get(kisa) or UZUN_KOD_ONEKI.get(kisa, "") + kisa


def _p_etiket(p) -> str:
    uz = UZUN_HARITA.get(str(p))
    return f"{uz} ({p})" if uz and uz != str(p) else str(p)


_TR_KATLA = str.maketrans("İıŞşĞğÜüÖöÇç", "IiSsGgUuOoCc")
_BITTI_ADAYLARI = ["finish", "durum2", "islem durumu"]

def _bitti_maske(s: pd.Series) -> pd.Series:
    return s.astype(str).str.translate(_TR_KATLA).str.upper().str.contains("BITTI", regex=False, na=False)

def _bitti_kolonu(df: pd.DataFrame):
    adaylar = [c for c in df.columns if str(c).strip() == "parça"]
    adaylar += [c for c in df.columns if c not in adaylar and str(c).strip().translate(_TR_KATLA).lower() in _BITTI_ADAYLARI]
    for c in adaylar:
        if _bitti_maske(df[c]).any():
            return c
    en, say = None, 0
    for c in df.columns:
        if c in adaylar or not (pd.api.types.is_object_dtype(df[c]) or pd.api.types.is_string_dtype(df[c])):
            continue
        u = pd.Series(df[c].dropna().astype(str).unique())
        if u.empty or len(u) > 50 or not (u.str.translate(_TR_KATLA).str.upper().str.strip() == "BITTI").any():
            continue
        n = int(_bitti_maske(df[c]).sum())
        if n > say:
            en, say = c, n
    return en or (adaylar[0] if adaylar else None)

def _alarm(yavas: pd.Series, bitti: pd.Series, c_bitti):
    norm = np.where(yavas, "🔴 Yavaş", "🟢 Normal")
    if c_bitti is None:                    # kontrol sütunu yok → eski davranış
        return norm
    return np.where(bitti.astype(bool), norm, "⏳ Yarım Kaldı")
def uzun_uyumsuz(df: pd.DataFrame, parca="Parça", uzun="Uzun_Ref") -> pd.DataFrame:
    if parca not in df.columns or uzun not in df.columns:
        return df.iloc[0:0]
    u = df[uzun].astype(str).str.strip()
    ok = df[uzun].notna() & (u != "") & (u.str.lower() != "nan")
    return df[ok & pd.Series([not a.endswith(str(b)) for a, b in zip(u, df[parca].astype(str))], index=df.index)]


def _hay(df: pd.DataFrame, parca="Parça", uzun="Uzun_Ref", op="Operasyon", opno="Op_No", kod="Kod_Ope") -> pd.Series:
    def g(c):
        if c not in df.columns:
            return pd.Series("", index=df.index)
        x = df[c].astype(str).str.strip()
        return x.where(~x.str.lower().isin(["nan", "none", "<na>"]), "")
    p, u, o, n, k = g(parca), g(uzun), g(op), g(opno), g(kod)
    um = p.map(lambda a: uzun_kod(a) if a else "")          
    ops = ("§" + o + "§").where(o != "", "") + " " + ("§" + n + "§").where(n != "", "")
    h = p + " " + u + " " + um + " " + k + " " + ops
    return h.str.translate(_TR_KATLA).str.upper()


_ARA_GURULTU = {"OP", "OPE", "OPERASYON", "PARCA", "KOD", "UZUN", "REFERANS", "REF", "NO", "ARA", "VE", "-", "·", "/"}


def _ara_maske(hay: pd.Series, q: str) -> pd.Series:
    m = pd.Series(True, index=hay.index)
    for tok in re.split(r"[\s,;·|]+", q.translate(_TR_KATLA).upper().strip()):
        if not tok or tok in _ARA_GURULTU:
            continue
        if tok.isdigit() and len(tok) <= 3:
            m &= hay.str.contains(rf"§{tok}(?![0-9])", regex=True, na=False)
        else:
            m &= hay.str.contains(re.escape(tok), regex=True, na=False)
    return m


def hafta_no(tarih: pd.Series) -> pd.Series:
    w = (tarih - pd.Timedelta(days=3)).dt.isocalendar().week
    return w.astype("Int64") if w.isna().any() else w.astype(int)   


def hafta_baslangic(tarih: pd.Series) -> pd.Series:
    return (tarih - pd.to_timedelta((tarih.dt.weekday - 3) % 7, unit="D")).dt.normalize()


def sn_izleme(df: pd.DataFrame, parca_kisa: str = "M01G03", operasyon: str = "60", op_adi=lambda k: k):
    c_parca = _kol(df, "PARÇA NO_ÜRETİM_GİRİLEN", "PARÇA NO ÜRETİM GİRİLEN")
    c_op = _kol(df, "OPERASYON_NO", "OPERASYON NO")
    c_kisi = _kol(df, "Operator_Kod", "OPERATÖR")
    c_ct = _kol(df, "ÜRETİM_C/T", "ÜRETİM C/T")
    bos = pd.DataFrame()
    if any(c is None for c in [c_parca, c_op, c_kisi, _kol(df, "Durum"), _kol(df, "Sure_Saat"), _kol(df, "S/N"), _kol(df, "TARİH")]):
        return bos, bos, bos, {"mesaj": "Gerekli kolonlar yok (S/N DB'de olmayabilir)."}

    parca_mask = df[c_parca].astype(str).str.contains(parca_kisa, na=False, regex=False)
    op_s = df[c_op].astype(str).str.strip()
    kapsam = {"uzun_kod": uzun_kod(parca_kisa), "operasyon": str(operasyon)}

    s_tum = df[parca_mask & (df["Durum"] == "Set_Up")]
    s_op = s_tum[op_s.loc[s_tum.index].str.startswith(str(operasyon))].copy()
    s_bel = s_tum[op_s.loc[s_tum.index].str.upper().eq("SETUP")]
    kapsam.update(setup_kayit=int(len(s_op)), setup_saat=float(s_op["Sure_Saat"].sum()),
                  setup_belirsiz_kayit=int(len(s_bel)), setup_belirsiz_saat=float(s_bel["Sure_Saat"].sum()))
    s_op["Operator"] = s_op[c_kisi].map(op_adi).map(ad_norm)
    setup = s_op[[c for c in ["TARİH", "VARDİYA", "TEZGAH", "Operator", c_op, "Sure_Saat", "S/N"] if c in s_op.columns]] \
        .sort_values("TARİH").reset_index(drop=True)

    u = df[parca_mask & (df["Durum"] == "Üretim") & (op_s == str(operasyon))].copy()
    kapsam.update(uretim_kaydi=int(len(u)), sn_bos_kayit=int(u["S/N"].isna().sum()),
                  sn_bos_saat=float(u.loc[u["S/N"].isna(), "Sure_Saat"].sum()))
    u = u[u["S/N"].notna()]
    if u.empty:
        return bos, bos, setup, kapsam

    u["Operator"] = u[c_kisi].map(op_adi).map(ad_norm)
    c_bitti = _bitti_kolonu(df)
    u["_bitti"] = _bitti_maske(u[c_bitti]) if c_bitti else False
    u = u.sort_values(["S/N", "TARİH"] + (["BAŞLANGIÇ_SAAT"] if "BAŞLANGIÇ_SAAT" in u.columns else []), kind="stable")

    kol = [c for c in ["S/N", "TARİH", "VARDİYA", "BAŞLANGIÇ_SAAT", "BİTİŞ_SAAT", "TEZGAH", "Operator", "Sure_Saat"] if c in u.columns]
    sirala = ["S/N", "TARİH"] + (["BAŞLANGIÇ_SAAT"] if "BAŞLANGIÇ_SAAT" in u.columns else [])
    detay = u[kol].sort_values(sirala).reset_index(drop=True)

    agg = dict(
        Toplam_Saat=("Sure_Saat", "sum"), Kayit=("Sure_Saat", "size"),
        Ilk_Tarih=("TARİH", "min"), Son_Tarih=("TARİH", "max"),
        Operatorler=("Operator", lambda s: " + ".join(dict.fromkeys(s))),
        Bitti=("_bitti", "max"),
    )
    if "VARDİYA" in u.columns:
        agg["Vardiyalar"] = ("VARDİYA", lambda s: " + ".join(dict.fromkeys(s.dropna().astype(str))))
    if "TEZGAH" in u.columns:
        agg["Tezgah"] = ("TEZGAH", lambda s: " / ".join(dict.fromkeys(s)))
    if c_ct:
        agg["Katalog_CT"] = (c_ct, "max")
    
    ozet = u.groupby("S/N").agg(**agg).reset_index()
    _k = u.groupby(["S/N", "Operator"], sort=False)["Sure_Saat"].sum().reset_index()
    _k["x"] = _k["Operator"].fillna("?") + " (" + _k["Sure_Saat"].map("{:.2f}".format) + " sa)"
    ozet["Kim_Ne_Kadar"] = ozet["S/N"].map(_k.groupby("S/N", sort=False)["x"].agg(" → ".join))
    ozet["Adet"] = 1
    ozet["Bolunmus_Is"] = ozet["Kayit"] > 1
    med = ozet["Toplam_Saat"].median()
    ozet["Medyana_Gore_%"] = (ozet["Toplam_Saat"] - med) / med * 100 if med > 0 else np.nan
    if "Katalog_CT" in ozet:
        ref = ozet["Katalog_CT"].where(ozet["Katalog_CT"] > 0)
        yavas = ref.notna() & (ozet["Toplam_Saat"] > ref * (1 + ALARM_TOLERANS))
        ozet["Alarm"] = _alarm(yavas, ozet["Bitti"], c_bitti)
    ozet["Hafta"] = hafta_no(ozet["Son_Tarih"])
    ozet = ozet.sort_values("Son_Tarih").reset_index(drop=True)
    kapsam.update(sn_sayisi=int(len(ozet)), medyan_saat=float(med), bolunmus_sn=int(ozet["Bolunmus_Is"].sum()),
                  yarim_sn=int((~ozet["Bitti"].astype(bool)).sum()) if c_bitti else 0, bitti_kolonu=str(c_bitti) if c_bitti else "")
    return ozet.round({"Toplam_Saat": 2, "Medyana_Gore_%": 1}), detay.round({"Sure_Saat": 2}), setup, kapsam

def sn_hafta_ozeti(ozet: pd.DataFrame, setup: pd.DataFrame) -> pd.DataFrame:
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
    if "Setup_Sa" not in h.columns:
        h["Setup_Sa"] = 0.0
    h["Setup_Sa"] = h["Setup_Sa"].fillna(0)
    return h.round({"Ort_Sure_Sa": 2, "Medyan_Sure_Sa": 2, "Toplam_Uretim_Sa": 1, "Gunluk_Ort_Adet": 2, "Setup_Sa": 1})


def _op_sade(seri: pd.Series) -> pd.Series:
    return seri.astype(str).str.strip()


def _pay_analizi(detay: pd.DataFrame, ozet: pd.DataFrame, kolon: str, medyan: float) -> pd.DataFrame:
    if detay.empty or kolon not in detay.columns:
        return pd.DataFrame()
    o = detay.merge(ozet[["S/N", "Toplam_Saat", "Operatorler"]], on="S/N", suffixes=("", "_sn"))
    o["Pay"] = o["Sure_Saat"] / o["Toplam_Saat"]
    o["Tek_Kisi"] = ~o["Operatorler"].str.contains(r"\+", regex=True)
    g = o.groupby(kolon)
    r = g.agg(Kayit=("Sure_Saat", "size"), SN_Sayisi=("S/N", "nunique"), Toplam_Saat=("Sure_Saat", "sum"),
              Adet_Payi=("Pay", "sum"), Ilk_Tarih=("TARİH", "min"), Son_Tarih=("TARİH", "max")).reset_index()
    r["Esdeger_Ort_Sa"] = r["Toplam_Saat"] / r["Adet_Payi"]
    if kolon == "Operator":
        solo = o[o["Tek_Kisi"]].groupby(kolon).agg(Tek_Basina_Adet=("S/N", "nunique")).reset_index()
        solo_ort = o[o["Tek_Kisi"]].drop_duplicates(["S/N"]).groupby(kolon)["Toplam_Saat"].mean().rename("Tek_Basina_Ort_Sa").reset_index()
        r = r.merge(solo, on=kolon, how="left").merge(solo_ort, on=kolon, how="left")
    r["Medyana_Gore_%"] = (r["Esdeger_Ort_Sa"] - medyan) / medyan * 100 if medyan > 0 else np.nan
    return r.sort_values("Adet_Payi", ascending=False).round(
        {"Toplam_Saat": 2, "Adet_Payi": 2, "Esdeger_Ort_Sa": 2, "Tek_Basina_Ort_Sa": 2, "Medyana_Gore_%": 1}).reset_index(drop=True)


def sn_gunluk(ozet: pd.DataFrame) -> pd.DataFrame:
    if ozet.empty:
        return pd.DataFrame()
    o = ozet.copy()
    o["Gun"] = o["Son_Tarih"].dt.normalize()
    g = o.groupby("Gun").agg(Adet=("Adet", "sum"), Ort_Sure_Sa=("Toplam_Saat", "mean"),
                            Toplam_Uretim_Sa=("Toplam_Saat", "sum"), Bolunmus=("Bolunmus_Is", "sum")).reset_index()
    g["Haftanin_Gunu"] = g["Gun"].dt.weekday.map(dict(enumerate(GUN_ADI)))
    g["Hafta"] = hafta_no(g["Gun"])
    return g.round({"Ort_Sure_Sa": 2, "Toplam_Uretim_Sa": 1})


def sn_rota(df: pd.DataFrame, parca_kisa: str = "M01G03", op_adi=lambda k: k) -> pd.DataFrame:
    c_parca = _kol(df, "PARÇA NO_ÜRETİM_GİRİLEN", "PARÇA NO ÜRETİM GİRİLEN")
    c_op = _kol(df, "OPERASYON_NO", "OPERASYON NO")
    c_kisi = _kol(df, "Operator_Kod", "OPERATÖR")
    if None in (c_parca, c_op, c_kisi) or "S/N" not in df.columns:
        return pd.DataFrame()
    u = df[df[c_parca].astype(str).str.contains(parca_kisa, na=False, regex=False) & (df["Durum"] == "Üretim") & df["S/N"].notna()].copy()
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
    diger = df[df["S/N"].isin(r.index) & ~df[c_parca].astype(str).str.contains(parca_kisa, na=False, regex=False)]
    r["Baska_Parca_Kodu"] = diger.groupby("S/N")[c_parca].agg(lambda s: ", ".join(sorted(set(s.astype(str)))))
    sayisal_kolonlar = r.select_dtypes(include=[np.number]).columns
    r[sayisal_kolonlar] = r[sayisal_kolonlar].round(2)
    return r.reset_index().sort_values("Ilk_Tarih").reset_index(drop=True)


def parca_duruslari(df: pd.DataFrame, parca_kisa: str = "M01G03", operasyon: str = "60", op_adi=lambda k: k,
                    sadece_op: bool = False) -> pd.DataFrame:
    c_parca = _kol(df, "PARÇA NO_ÜRETİM_GİRİLEN", "PARÇA NO ÜRETİM GİRİLEN")
    c_op = _kol(df, "OPERASYON_NO", "OPERASYON NO")
    c_kisi = _kol(df, "Operator_Kod", "OPERATÖR")
    c_sebep = _kol(df, "Durus_Sebebi_Ham") or c_op
    if None in (c_parca, c_kisi, c_sebep, c_op):
        return pd.DataFrame()
    d = df[df[c_parca].astype(str).str.contains(parca_kisa, na=False, regex=False) & df["Durum"].astype(str).str.contains("Duruş")].copy()
    if d.empty:
        return pd.DataFrame()
    op_txt = d[c_op].astype(str).str.strip()
    kesin = op_txt.str.match(rf"^{re.escape(str(operasyon))}(?!\d)")
    d["Operasyon_Kaydi"] = np.where(kesin, str(operasyon), "yazılmamış")
    if sadece_op:
        d = d[kesin]
    d["Operator"] = d[c_kisi].map(op_adi).map(ad_norm)
    d["Sebep"] = d[c_sebep]
    kol = [c for c in ["TARİH", "VARDİYA", "TEZGAH", "Operator", "Sebep", "Operasyon_Kaydi", "Sure_Saat", "S/N"] if c in d.columns]
    return d[kol].sort_values("TARİH").round({"Sure_Saat": 2}).reset_index(drop=True)


# =========================================================================== HAFTA DETAYI (tüm fabrika)
def _kolonlar(df):
    return {"parca": _kol(df, "PARÇA NO_ÜRETİM_GİRİLEN", "PARÇA NO ÜRETİM GİRİLEN"), "op": _kol(df, "OPERASYON_NO", "OPERASYON NO"),
            "kisi": _kol(df, "Operator_Kod", "OPERATÖR")}


def hafta_listesi(df: pd.DataFrame) -> pd.DataFrame:
    if "TARİH" not in df.columns or df["TARİH"].notna().sum() == 0:
        return pd.DataFrame()
    d = df[df["TARİH"].notna()].copy()
    d["Hafta"] = hafta_no(d["TARİH"])
    g = d.groupby("Hafta").agg(Ilk_Kayit=("TARİH", "min"), Son_Kayit=("TARİH", "max"), Kayit=("Sure_Saat", "size"),
                               Toplam_Sa=("Sure_Saat", "sum")).reset_index()
    g["Hafta_Baslangic"] = hafta_baslangic(g["Ilk_Kayit"])
    g["Hafta_Bitis"] = g["Hafta_Baslangic"] + pd.Timedelta(days=6)
    return g.sort_values("Hafta_Baslangic").reset_index(drop=True)


def hafta_kayitlari(df: pd.DataFrame, hafta: int, op_adi=lambda k: k) -> pd.DataFrame:
    c = _kolonlar(df)
    d = df[df["TARİH"].notna() & (hafta_no(df["TARİH"]) == int(hafta)).fillna(False).astype(bool)].copy()
    d["Operatör"] = d[c["kisi"]].map(op_adi).map(ad_norm) if c["kisi"] else None
    d["Parça"] = d[c["parca"]].astype(str).where(d[c["parca"]].notna(), "(parça yok)") if c["parca"] else "(parça yok)"
    d["Operasyon"] = d[c["op"]].astype(str).str.strip() if c["op"] else ""
    d["Op_No"] = d["Operasyon"].str.extract(r"^(\d+)")[0].fillna(d["Operasyon"])      
    cu, ck = _uzun_kolonu(df), _kol(df, "KOD & OPE.")
    uz = d[cu].astype(str).str.strip().where(d[cu].notna(), None) if cu else pd.Series([None] * len(d), index=d.index)
    d["Uzun_Ref"] = uz.fillna(d["Parça"].map(UZUN_HARITA)).fillna("")
    d["Kod_Ope"] = d[ck].astype(str).str.strip() if ck else (d["Parça"] + " " + d["Operasyon"])
    return d


def hafta_operator_parca(d: pd.DataFrame, ayrinti: bool = True) -> pd.DataFrame:
    if d.empty:
        return pd.DataFrame()
    p = d.pivot_table(index=["Operatör", "Parça"], columns="Durum", values="Sure_Saat", aggfunc="sum", fill_value=0)
    for x in DURUM_SIRASI:
        if x not in p.columns:
            p[x] = 0.0
    r = pd.DataFrame({"Uretim_Sa": p["Üretim"], "Set_Up_Sa": p["Set_Up"], "Plansiz_Durus_Sa": p["Plansız_Duruş"],
                      "Rework_Sa": p["Rework"], "Yemek_Sa": p["Yemek_Mola"]})
    r["Toplam_Sa"] = r.sum(axis=1)
    g = d.groupby(["Operatör", "Parça"])
    r["Kayit"] = g.size()
    if ayrinti:
        if "S/N" in d.columns:   
            sn = d[d["S/N"].notna()].groupby(["Operatör", "Parça"])["S/N"]
            r["SN_Adedi"] = sn.nunique()
            r["SN_Listesi"] = sn.agg(lambda s: ", ".join(sorted(set(s.astype(str).str.strip()))))
            r["SN_Adedi"] = r["SN_Adedi"].fillna(0).astype(int)
            r["SN_Listesi"] = r["SN_Listesi"].fillna("")
        r["Gun_Sayisi"] = g["TARİH"].nunique()
        r["Operasyonlar"] = g["Operasyon"].agg(lambda s: ", ".join(sorted({x for x in s if x and x != "nan"})[:8]))
    out = r.reset_index().sort_values(["Toplam_Sa"], ascending=False).round(2).reset_index(drop=True)
    if "Uzun_Ref" in d.columns:
        m = d.groupby("Parça")["Uzun_Ref"].agg(_ilk_dolu)
        out.insert(out.columns.get_loc("Parça") + 1, "Uzun_Ref", out["Parça"].map(m).fillna(""))
    return out


def hafta_operator_parca_tum(df: pd.DataFrame, op_adi=lambda k: k) -> pd.DataFrame:
    hl = hafta_listesi(df)
    if hl.empty:
        return pd.DataFrame()
    parcalar = []
    for h in hl["Hafta"]:
        t = hafta_operator_parca(hafta_kayitlari(df, h, op_adi), ayrinti=False)
        if t.empty:
            continue
        t = t.drop(columns=["Yemek_Sa", "Toplam_Sa"])
        t.insert(0, "Hafta", int(h))
        parcalar.append(t)
    if not parcalar:
        return pd.DataFrame()
    r = pd.concat(parcalar, ignore_index=True)
    return r[(r[["Uretim_Sa", "Set_Up_Sa", "Plansiz_Durus_Sa", "Rework_Sa"]].sum(axis=1) > 0)].reset_index(drop=True)


def hafta_cumleleri(d: pd.DataFrame, hafta: int, bas, bit, pilot_ozet: pd.DataFrame | None = None, uzun: str = "", op: str = "") -> list[str]:
    if d.empty:
        return ["Bu hafta için kayıt yok."]
    toplam = d["Sure_Saat"].sum()
    saat = d.groupby("Durum")["Sure_Saat"].sum()
    u, du, su = (float(saat.get(x, 0)) for x in ("Üretim", "Plansız_Duruş", "Set_Up"))
    c = []
    c.append(f"**Hafta {hafta}** ({bas:%d.%m.%Y} Perşembe – {bit:%d.%m.%Y} Çarşamba): toplam **{toplam:.1f} saat** kayıt; "
             f"bunun **{u:.1f} saati üretim** (%{u / toplam * 100:.1f}), {du:.1f} saati plansız duruş, {su:.1f} saati set-up.")
    c.append(f"{d['TEZGAH'].nunique() if 'TEZGAH' in d.columns else '?'} tezgahta, {d['Operatör'].nunique()} operatör ve {d['Parça'].nunique()} farklı parça kodu ile çalışıldı.")
    ou = d[d["Durum"] == "Üretim"].groupby("Operatör")["Sure_Saat"].sum().sort_values(ascending=False)
    if len(ou):
        c.append(f"Üretimde en çok saat: **{ou.index[0]}** ({ou.iloc[0]:.1f} sa)" + (f", ardından {ou.index[1]} ({ou.iloc[1]:.1f} sa)." if len(ou) > 1 else "."))
    pu = d[d["Durum"] == "Üretim"].groupby("Parça")["Sure_Saat"].sum().sort_values(ascending=False)
    if len(pu):
        c.append(f"En çok üretim saati harcanan parça: **{_p_etiket(pu.index[0])}** ({pu.iloc[0]:.1f} sa)" + (f", sonra {_p_etiket(pu.index[1])} ({pu.iloc[1]:.1f} sa)." if len(pu) > 1 else "."))
    dk = d[d["Durum"] == "Plansız_Duruş"]
    if len(dk):
        sebep_kol = _kol(dk, "Durus_Kategorisi", "OPERASYON_NO", "OPERASYON NO")
        if sebep_kol:
            sb = dk.groupby(dk[sebep_kol].astype(str).str.strip())["Sure_Saat"].sum().sort_values(ascending=False)
            c.append(f"Plansız duruşta en büyük kalem: **{sb.index[0]}** ({sb.iloc[0]:.1f} sa; toplam duruş {du:.1f} sa).")
    if pilot_ozet is not None and not pilot_ozet.empty:
        p = pilot_ozet[pilot_ozet["Hafta"] == hafta]
        if len(p):
            c.append(f"{uzun} operasyon {op}: bu hafta **{len(p)} adet tamamlandı**, adet başına ortalama {p['Toplam_Saat'].mean():.2f} sa "
                     f"({int(p['Bolunmus_Is'].sum())} tanesi birden fazla kayıtta/vardiyada bölünerek yapıldı).")
        else:
            c.append(f"{uzun} operasyon {op}: bu hafta tamamlanan adet yok.")
    return c


# =========================================================================== ANALİZ + AI BAĞLAMI
def sn_tum_analiz(df: pd.DataFrame, parca_kisa: str, operasyon: str, op_adi=lambda k: k) -> dict:
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
    _o = ozet[ozet["Bitti"].astype(bool)] if "Bitti" in ozet and ozet["Bitti"].any() else ozet
    sirali = _o.sort_values(["Toplam_Saat", "Son_Tarih"], ascending=[False, True])
    cols = [c for c in ["S/N", "Toplam_Saat", "Kayit", "Operatorler", "Vardiyalar", "Tezgah", "Son_Tarih"] if c in ozet.columns]
    t["uclar"] = pd.concat([sirali.head(5)[cols].assign(Grup="EN YAVAŞ 5"),
                            sirali.tail(5).iloc[::-1][cols].assign(Grup="EN HIZLI 5")]).reset_index(drop=True)
    t["rota"] = sn_rota(df, parca_kisa, op_adi)
    t["durus"] = parca_duruslari(df, parca_kisa, operasyon, op_adi)
    return t


# =========================================================================== TÜMÜ (pilot sınırı yok) + SEÇİCİ
TUMU = (None, None)
_SN_ANAHTAR = ["Parça", "Operasyon", "S/N"]


def _gun_etiketi(g) -> str:
    return "Tüm günler" if g is None else f"{g:%d.%m.%Y} {GUN_ADI[g.weekday()]}"


def parca_op_secenekleri(df: pd.DataFrame, pilot=PILOT) -> list[tuple]:
    c_parca = _kol(df, "PARÇA NO_ÜRETİM_GİRİLEN", "PARÇA NO ÜRETİM GİRİLEN")
    c_op = _kol(df, "OPERASYON_NO", "OPERASYON NO")
    sayim = pd.Series(dtype=int)
    if c_parca and c_op and "S/N" in df.columns and "Durum" in df.columns:
        u = df[(df["Durum"] == "Üretim") & df["S/N"].notna() & df[c_parca].notna() & df[c_op].notna()]
        if not u.empty:
            g = pd.DataFrame({"p": u[c_parca].astype(str).str.strip(), "o": u[c_op].astype(str).str.strip(),
                              "sn": u["S/N"].astype(str).str.strip()}).drop_duplicates()
            sayim = g.groupby(["p", "o"]).size()
    sec = [(TUMU, f"📋 Tümü — {len(sayim)} parça/operasyon · {int(sayim.sum())} adet (S/N)")]
    for k_, o_ in pilot:
        n = 0
        if len(sayim):
            m = (np.asarray(sayim.index.get_level_values(0).str.contains(k_, regex=False))
                 & np.asarray(sayim.index.get_level_values(1) == str(o_)))
            n = int(sayim[m].sum())
        sec.append(((k_, str(o_)), f"{uzun_kod(k_)} — op {o_} · {n} adet"))
    return sec


def sn_tumu(df: pd.DataFrame, op_adi=lambda k: k):
    bos = pd.DataFrame()
    c_parca = _kol(df, "PARÇA NO_ÜRETİM_GİRİLEN", "PARÇA NO ÜRETİM GİRİLEN")
    c_op = _kol(df, "OPERASYON_NO", "OPERASYON NO")
    c_kisi = _kol(df, "Operator_Kod", "OPERATÖR")
    c_ct = _kol(df, "ÜRETİM_C/T", "ÜRETİM C/T")
    if any(c is None for c in [c_parca, c_op, c_kisi, _kol(df, "Durum"), _kol(df, "Sure_Saat"), _kol(df, "S/N"), _kol(df, "TARİH")]):
        return bos, bos
    u = df[(df["Durum"] == "Üretim") & df["S/N"].notna() & df[c_parca].notna() & df[c_op].notna()].copy()
    if u.empty:
        return bos, bos
    u["Parça"] = u[c_parca].astype(str).str.strip()
    u["Operasyon"] = u[c_op].astype(str).str.strip()
    u["S/N"] = u["S/N"].astype(str).str.strip()
    cu = _uzun_kolonu(df)
    uz = u[cu].astype(str).str.strip().where(u[cu].notna(), None) if cu else pd.Series([None] * len(u), index=u.index)
    u["Uzun_Ref"] = uz.fillna(u["Parça"].map(UZUN_HARITA)).fillna(u["Parça"].map(lambda p: uzun_kod(p) if p in UZUN_KOD_ONEKI else "")).fillna("")
    u["Operator"] = u[c_kisi].map(op_adi).map(ad_norm)
    c_bitti = _bitti_kolonu(df)
    u["_bitti"] = _bitti_maske(u[c_bitti]) if c_bitti else False
    u = u.sort_values(_SN_ANAHTAR + ["TARİH"] + (["BAŞLANGIÇ_SAAT"] if "BAŞLANGIÇ_SAAT" in u.columns else []), kind="stable")
    
    kol = [c for c in ["TARİH", "VARDİYA", "BAŞLANGIÇ_SAAT", "BİTİŞ_SAAT", "TEZGAH", "Operator", "Sure_Saat"] if c in u.columns]
    sirala = _SN_ANAHTAR + ["TARİH"] + (["BAŞLANGIÇ_SAAT"] if "BAŞLANGIÇ_SAAT" in u.columns else [])
    detay = u[["Parça", "Uzun_Ref", "Operasyon", "S/N"] + kol].sort_values(sirala).reset_index(drop=True)

    def _birles(s, ayrac):
        return ayrac.join(dict.fromkeys(s.dropna().astype(str)))

    agg = dict(
        Toplam_Saat=("Sure_Saat", "sum"), Kayit=("Sure_Saat", "size"),
        Ilk_Tarih=("TARİH", "min"), Son_Tarih=("TARİH", "max"),
        Operatorler=("Operator", lambda s: _birles(s, " + ")),
        Uzun_Ref=("Uzun_Ref", _ilk_dolu),
        Bitti=("_bitti", "max"),
    )
    if "VARDİYA" in u.columns:
        agg["Vardiyalar"] = ("VARDİYA", lambda s: _birles(s, " + "))
    if "TEZGAH" in u.columns:
        agg["Tezgah"] = ("TEZGAH", lambda s: _birles(s, " / "))
    if c_ct:
        agg["Katalog_CT"] = (c_ct, "max")
        
    ozet = u.groupby(_SN_ANAHTAR).agg(**agg).reset_index()
    
    _k = u.groupby(_SN_ANAHTAR + ["Operator"], sort=False)["Sure_Saat"].sum().reset_index()
    _k["x"] = _k["Operator"].fillna("?") + " (" + _k["Sure_Saat"].map("{:.2f}".format) + " sa)"
    ozet = ozet.merge(_k.groupby(_SN_ANAHTAR, sort=False)["x"].agg(" → ".join).rename("Kim_Ne_Kadar").reset_index(), on=_SN_ANAHTAR, how="left")
    
    sira_ = [c for c in ozet.columns if c != "Uzun_Ref"]
    sira_.insert(sira_.index("Parça") + 1, "Uzun_Ref")
    ozet = ozet[sira_]
    ozet["Adet"] = 1
    ozet["Bolunmus_Is"] = ozet["Kayit"] > 1
    med = ozet.groupby(["Parça", "Operasyon"])["Toplam_Saat"].transform("median")
    ozet["Medyana_Gore_%"] = np.where(med > 0, (ozet["Toplam_Saat"] - med) / med * 100, np.nan)
    if "Katalog_CT" in ozet:
        ref = ozet["Katalog_CT"].where(ozet["Katalog_CT"] > 0)
        yavas = ref.notna() & (ozet["Toplam_Saat"] > ref * (1 + ALARM_TOLERANS))
        ozet["Alarm"] = _alarm(yavas, ozet["Bitti"], c_bitti)
    ozet["Hafta"] = hafta_no(ozet["Son_Tarih"])
    ozet = ozet.sort_values(["Son_Tarih", "Parça", "Operasyon", "S/N"]).reset_index(drop=True)
    return ozet.round({"Toplam_Saat": 2, "Medyana_Gore_%": 1}), detay.round({"Sure_Saat": 2})

def sn_tumu_parca_ozeti(ozet: pd.DataFrame) -> pd.DataFrame:
    if ozet is None or ozet.empty:
        return pd.DataFrame()
    ek_ = {"Uzun_Ref": ("Uzun_Ref", _ilk_dolu)} if "Uzun_Ref" in ozet.columns else {}
    g = ozet.groupby(["Parça", "Operasyon"]).agg(
        SN_Adedi=("S/N", "nunique"), Toplam_Uretim_Sa=("Toplam_Saat", "sum"), Ort_Sure_Sa=("Toplam_Saat", "mean"),
        Medyan_Sure_Sa=("Toplam_Saat", "median"), Bolunmus=("Bolunmus_Is", "sum"),
        Ilk_Tarih=("Ilk_Tarih", "min"), Son_Tarih=("Son_Tarih", "max"), **ek_,
    ).reset_index()
    if "Uzun_Ref" in g.columns:
        k_ = [c for c in g.columns if c != "Uzun_Ref"]
        k_.insert(k_.index("Parça") + 1, "Uzun_Ref")
        g = g[k_]
    return g.sort_values(["SN_Adedi", "Parça", "Operasyon"], ascending=[False, True, True]) \
        .round({"Toplam_Uretim_Sa": 1, "Ort_Sure_Sa": 2, "Medyan_Sure_Sa": 2}).reset_index(drop=True)


def _tum_sn_gorunumu(st, df, op_adi, tablo, kpi_alan):
    ozet, detay = sn_tumu(df, op_adi)
    if ozet.empty:
        st.info("Seçili filtrelerde S/N'li üretim kaydı yok.")
        return
    st.markdown("### 📋 Tüm parça / operasyonlar — S/N bazlı adet")
    st.caption("Her satır 1 adet = parça + operasyon + seri numarası. Bir gün seçerseniz o günün tüm S/N'leri listelenir; "
               "tek bir parça/operasyona bakmak için yukarıdaki seçiciden onu seçin ya da aşağıdan süzün.")
    pairs = sn_tumu_parca_ozeti(ozet)
    pair_etiket = {(p, o): f"{_p_etiket(p)} — op {o} ({int(n)} adet)" for p, o, n in zip(pairs["Parça"], pairs["Operasyon"], pairs["SN_Adedi"])}
    gunler = sorted((pd.Timestamp(g) for g in detay["TARİH"].dt.normalize().dropna().unique()), reverse=True)

    with st.expander("🔍 Detaylı Süzgeçler"):
        f1, f2, f3 = st.columns([2, 3, 2])
        gun = f1.selectbox("Gün", [None] + gunler, format_func=_gun_etiketi, key="sn_tum_gun")
        olcu = f2.radio("Gün ölçüsü", ["Tamamlandığı gün", "O gün kaydı olan"], horizontal=True, key="sn_tum_olcu", disabled=gun is None,
                        help="Tamamlandığı gün: S/N'nin son kaydı o gün olanlar. O gün kaydı olan: o gün çalışılan tüm S/N'ler (devredenler dahil).")
        opsec = f3.selectbox("Operatör", ["Tümü"] + sorted(detay["Operator"].dropna().unique().tolist()), key="sn_tum_op")
        secili = st.multiselect("Parça / operasyon (boşsa hepsi)", list(pair_etiket), format_func=lambda a: pair_etiket[a], key="sn_tum_pairs")
        g2, g3 = st.columns([1, 2])
        ara = g2.text_input("S/N ara", key="sn_tum_ara", placeholder="örn. 2G7J")
        kod_ara = g3.text_input("🔎 Ara: parça · uzun referans · operasyon", key="sn_tum_kod",
                                placeholder="örn. 2547M01G03 · M01G03 60",
                                help="Kısa kod, uzun referans ve operasyon birlikte yazılabilir; 'op' yazmak zorunda değilsiniz.")

    x = ozet
    if gun is not None:
        if olcu.startswith("Tamam"):
            x = x[x["Son_Tarih"].dt.normalize() == gun]
        else:
            gk = detay.loc[detay["TARİH"].dt.normalize() == gun, _SN_ANAHTAR].drop_duplicates()
            x = x.merge(gk, on=_SN_ANAHTAR, how="inner")
    if secili:
        ps = set(secili)
        x = x[np.fromiter(((p, o) in ps for p, o in zip(x["Parça"], x["Operasyon"])), dtype=bool, count=len(x))]
    if opsec != "Tümü":
        x = x.merge(detay.loc[detay["Operator"] == opsec, _SN_ANAHTAR].drop_duplicates(), on=_SN_ANAHTAR, how="inner")
    if ara:
        x = x[x["S/N"].astype(str).str.contains(ara, case=False, na=False, regex=False)]
    if kod_ara:
        x = x[_ara_maske(_hay(x), kod_ara)]
    x = x.reset_index(drop=True)

    if x.empty:
        st.info("Süzgeçlere uyan S/N yok.")
    else:
        _uy = uzun_uyumsuz(x)
        if len(_uy):
            st.warning(f"⚠️ {len(_uy)} S/N'de kısa kod ile uzun referans uyuşmuyor (örn. {_uy.iloc[0]['Parça']} ↔ {_uy.iloc[0]['Uzun_Ref']}); "
                       "Uzun Referans sütununa bakın, giriş hatası olabilir.")
        with kpi_alan:
            k1, k2, k3, k4 = st.columns(4)
            k1.metric("📦 Adet (S/N)", len(x), help="Parça + operasyon + seri numarası sayısı.")
            k2.metric("🔩 Parça/operasyon", len(x[["Parça", "Operasyon"]].drop_duplicates()))
            k3.metric("⚖️ Bölünerek yapılan", int(x["Bolunmus_Is"].sum()))
            k4.metric("⏱️ Toplam süre", f"{x['Toplam_Saat'].sum():.1f} sa")
        st.caption(f"{_gun_etiketi(gun)}" + ("" if gun is None else f" ({olcu.lower()})") + f" · {len(x)} / {len(ozet)} adet gösteriliyor.")
        st.markdown("**Parça / operasyon bazında adet**")
        tablo(sn_tumu_parca_ozeti(x))
        st.markdown("**Tüm S/N'ler**")
        tablo(x)
        st.download_button("⬇️ Listelenen S/N'ler (CSV)", x.to_csv(index=False).encode("utf-8-sig"),
                           file_name="tum_sn_listesi.csv", mime="text/csv", key="dl_tum_sn")
        if gun is not None:
            with st.expander(f"🧾 {_gun_etiketi(gun)} günü ham kayıtları (listelenen S/N'lere ait)"):
                tablo(detay[detay["TARİH"].dt.normalize() == gun].merge(x[_SN_ANAHTAR], on=_SN_ANAHTAR, how="inner"))

    st.markdown("---")
    with st.expander("📅 Gün ve hafta özeti (tüm parça/operasyonlar)"):
        st.markdown("**Gün bazlı adet (tamamlanma tarihine göre)**")
        tablo(sn_gunluk(ozet).drop(columns=["Ort_Sure_Sa"], errors="ignore"))
        st.markdown("**Hafta özeti (Perşembe→Çarşamba)**")
        tablo(sn_hafta_ozeti(ozet, None).drop(columns=["Setup_Sa", "Ort_Sure_Sa", "Medyan_Sure_Sa"], errors="ignore"))
    st.markdown("---")
    _hafta_detayi(st, df, {"ozet": ozet}, op_adi, None, None, tablo)


_AI_TABLOLAR = [
    ("ozet", 10, "S/N BAZLI ADET — her satır 1 ADET. Toplam_Saat = S/N'nin tüm kayıtları; Bolunmus_Is=True: birden fazla kayıt; "
                 "Alarm: yalnız Bitti=True olanlar için Katalog_CT'yi %{tol} aşarsa Yavaş, aksi Normal; Bitti=False '⏳ Yarım Kaldı' (süre eksik, kıyaslama yapma); Hafta Perşembe→Çarşamba, S/N tamamlandığı (Son_Tarih) haftaya yazılır"),
    ("detay", 11, "S/N KAYIT DETAYI — her S/N için kim, hangi vardiya, saat aralığı, süre"),
    ("hafta", 12, "HAFTALIK ADET ÖZETİ (Perşembe→Çarşamba; Gunluk_Ort_Adet = Adet / üretim yapılan gün)"),
    ("setup", 13, "SET-UP KAYITLARI (parça+operasyona bağlı, S/N'siz olabilir)"),
    ("gunluk", 14, "GÜNLÜK ADET (tamamlanma tarihine göre)"),
    ("operator", 15, "OPERATÖR ANALİZİ — Adet_Payi: bölünmüş S/N'de adet saat payı oranında paylaşılır; Esdeger_Ort_Sa = Toplam_Saat/Adet_Payi; "
                     "Tek_Basina_*: S/N'yi tek başına bitirdiği işler; Medyana_Gore_% medyan süreye göre fark"),
    ("vardiya", 16, "VARDİYA ANALİZİ (aynı Adet_Payi mantığı)"),
    ("tezgah", 17, "TEZGAH ANALİZİ (aynı Adet_Payi mantığı)"),
    ("rota", 18, "PARÇA ROTASI A→Z — S/N'nin TÜM operasyonlardaki saati ve kimin yaptığı; Baska_Parca_Kodu doluysa aynı S/N başka parça koduna da yazılmış (veri hatası şüphesi)"),
    ("durus", 19, "PARÇAYA YAZILMIŞ DURUŞLAR (sebep metniyle; Operasyon_Kaydi='yazılmamış': duruş satırında operasyon no yok, parçanın duruşu olarak listelenir)"),
    ("uclar", 20, "EN YAVAŞ 5 ve EN HIZLI 5 S/N (hazır sıralama; eşit sürelerde Son_Tarih'i eski olan önce)"),
]


def sn_ai_baglami(df: pd.DataFrame, pilot=PILOT, op_adi=lambda k: k) -> str:
    uzun_haritasi_guncelle(df)
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
    hop = hafta_operator_parca_tum(df, op_adi)
    if not hop.empty:
        parcalar.append("### TABLO 21: HAFTALIK OPERATÖR × PARÇA SAATLERİ (TÜM FABRİKA; hafta Perşembe→Çarşamba, tarih hafta numarasına göre; "
                        "Uretim_Sa/Set_Up_Sa/Plansiz_Durus_Sa/Rework_Sa = o operatörün o parçada o haftada harcadığı saat; Kayit = satır sayısı; yemek hariç)\n"
                        + hop.to_csv(index=False))
    return "\n\n".join(parcalar)


SN_AI_KURALI = (
    "6. Kullanıcı seri numarası (S/N), adet, parça süresi, kim çalıştı, vardiya, günlük ortalama, set-up, "
    "operatör/vardiya/tezgah karşılaştırması, parçanın operasyonlar arası yolculuğu (rota) veya o parçadaki duruşları sorarsa "
    "TABLO 10-20'yi kullan. 'X. haftada kim hangi parçayla ne kadar çalıştı' gibi hafta bazlı fabrika sorularında TABLO 21'i kullan. "
    "Adet = S/N sayısıdır (aynı S/N'nin kayıtları birleşir, 1 adet sayılır). Hafta Perşembe'den başlar. "
    "Bölünmüş işte operatör kıyasını Adet_Payi/Esdeger_Ort_Sa ile yap, 'tek başına bitirdiği' işleri ayrıca belirt. "
    "'En yavaş/en hızlı' sorularında TABLO 20'yi kullan, sıralamayı KESİNLİKLE kendin yapma.\n"
    "7. ÜSLUP KURALI: Asla 'fırlamış', 'bozmuş', 'çakılmış', 'maalesef' gibi duygusal, abartılı veya yorum içeren kelimeler kullanma. "
    "Verileri tamamen objektif, kurumsal ve istatistiksel bir dille ifade et (Örn: 'artış göstermiştir', 'sapma gözlemlenmiştir'). Kullanıcıya hitap sözcüğü kullanma.\n"
    "8. MATEMATİK KURALI: Tablolardaki genel toplam, ortalama veya oranları (örneğin aylık ortalama) kendin hesaplamaya çalışma, doğrudan tablodaki hazır değeri oku. Ancak kullanıcı spesifik olarak 'şu 3 parçanın ortalamasını al' gibi basit bir aritmetik işlem isterse, işlemi açıkça göstererek yap (Örn: '3.0 + 2.5 + 3.5 = 9.0 saat, 9.0 / 3 = 3.0 saat')."
    "\n9. SÜTUN KARIŞTIRMA YASAĞI (KRİTİK): 'S/N' (Seri Numarası) ile 'Parça Kodu'nu (örn: M01G03) ASLA birbirine karıştırma! Kullanıcı senden S/N listesi istediğinde, KESİNLİKLE 'S/N' kolonundaki (genellikle 2G... veya 2H... ile başlayan) spesifik kodları ver. Parça kodunu S/N imiş gibi cevaplara yazma.\n"
    "10. PARÇA KODU: Uzun referans (örn. 2547M01G03) ile kısa kod (M01G03) AYNI parçadır; kullanıcı hangisini yazarsa yazsın TABLO 21'de 'Uzun_Ref' ve 'Parça' kolonlarında ara. "
    "'M01G03 60' = parça M01G03, operasyon 60 (KOD & OPE.)."
)


# =========================================================================== EKRAN: okunabilir görünüm katmanı
_COL = {
    "S/N": "Seri No (S/N)", "Toplam_Saat": "Toplam Süre (sa)", "Kayit": "Kayıt Sayısı", "Ilk_Tarih": "İlk Kayıt", "Son_Tarih": "Tamamlanma",
    "Operatorler": "Çalışanlar", "Vardiyalar": "Vardiya(lar)", "Tezgah": "Tezgah", "Katalog_CT": "Katalog Süresi (sa)", "Adet": "Adet",
    "Bolunmus_Is": "Bölünerek mi yapıldı?", "Medyana_Gore_%": "Medyana Göre Fark %", "Alarm": "Katalog Süresine Göre", "Hafta": "Hafta",
    "Hafta_Baslangic": "Hafta Başı (Per)", "Hafta_Bitis": "Hafta Sonu (Çar)", "Ort_Sure_Sa": "Ort. Süre (sa/adet)",
    "Medyan_Sure_Sa": "Medyan Süre (sa/adet)", "Toplam_Uretim_Sa": "Toplam Üretim Süresi (sa)", "Bolunmus": "Bölünerek Yapılan",
    "Uretim_Gunu": "Üretim Yapılan Gün", "Gunluk_Ort_Adet": "Günlük Ort. Adet", "Setup_Sa": "Set-up (sa)", "Gun": "Gün",
    "Haftanin_Gunu": "Gün Adı", "Operator": "Operatör", "Operatör": "Operatör", "VARDİYA": "Vardiya", "TEZGAH": "Tezgah",
    "SN_Sayisi": "Dokunduğu Parça (S/N)", "Adet_Payi": "Net Üretim (adet)", "Esdeger_Ort_Sa": "Ort. Süre (sa/adet)",
    "Tek_Basina_Adet": "Tek Başına Bitirdiği", "Tek_Basina_Ort_Sa": "Tek Başına Ort. Süre (sa)", "Sure_Saat": "Süre (sa)",
    "BAŞLANGIÇ_SAAT": "Başlangıç", "BİTİŞ_SAAT": "Bitiş", "TARİH": "Tarih", "Toplam_Sa_Tum_Op": "Tüm Operasyonlar Toplamı (sa)",
    "Op_Sayisi": "Geçtiği Operasyon Sayısı", "Baska_Parca_Kodu": "⚠️ Başka Parça Kodunda da Yazılı", "Sebep": "Sebep",
    "Operasyon_Kaydi": "Operasyon Bilgisi", "Parça": "Parça", "Uretim_Sa": "Üretim (sa)", "Set_Up_Sa": "Set-up (sa)",
    "Plansiz_Durus_Sa": "Plansız Duruş (sa)", "Rework_Sa": "Rework (sa)", "Yemek_Sa": "Yemek/Mola (sa)", "Toplam_Sa": "Toplam (sa)",
    "Gun_Sayisi": "Çalıştığı Gün", "Operasyonlar": "Operasyon No / Metin", "Grup": "Grup",
    "Operasyon": "Operasyon No", "SN_Adedi": "Adet (S/N sayısı)", "SN_Listesi": "Seri Numaraları (S/N)",
}
_DEGER = {"🔴 Yavaş": "🔴 Katalog süresini aştı", "🟢 Normal": "🟢 Katalog süresi içinde","⏳ Yarım Kaldı": "⏳ Yarım kaldı (üretim sürüyor)", True: "Evet", False: "Hayır"}


def gorsel(tablo: pd.DataFrame, ek: dict | None = None) -> pd.DataFrame:
    if tablo is None or tablo.empty:
        return tablo
    t = tablo.copy()
    for c in list(t.columns):
        if pd.api.types.is_datetime64_any_dtype(t[c]):
            t[c] = t[c].dt.strftime("%d.%m.%Y")
        elif t[c].dtype == bool:
            t[c] = t[c].map({True: "Evet", False: "Hayır"})
    if "Alarm" in t.columns:
        t["Alarm"] = t["Alarm"].replace(_DEGER)
    ren = {}
    for c in t.columns:
        m = re.fullmatch(r"Op(.+)_(Sa|Kim)", str(c))
        if m:
            ren[c] = f"Op {m.group(1)} · {'süre (sa)' if m.group(2) == 'Sa' else 'kim yaptı'}"
        else:
            ren[c] = (ek or {}).get(c, _COL.get(c, c))
    return t.rename(columns=ren)


_COL.update({"Uzun_Ref": "Uzun Referans (Parça No)", "Kod_Ope": "Kod & Operasyon", "Op_No": "Operasyon No", "Kim_Ne_Kadar": "Kim Ne Kadar Çalıştı (sırayla)","Parça": "Parça (kısa kod)","Bitti": "Bitti mi?"})

_OP_EK = {"Kayit": "İşlem Kaydı", "Toplam_Saat": "Toplam Çalışma (sa)", "Ilk_Tarih": "İlk Çalıştığı Gün", "Son_Tarih": "Son Çalıştığı Gün"}


def ozet_cumleleri(t: dict, veri_aralik=None) -> list[str]:
    k, o = t["kapsam"], t["ozet"]
    c = []
    c.append(f"**{k['uzun_kod']}** parçasının **{k['operasyon']}. operasyonunda** {o['Ilk_Tarih'].min():%d.%m.%Y} – {o['Son_Tarih'].max():%d.%m.%Y} "
             f"arasında **{k['sn_sayisi']} adet** tamamlandı (adet = farklı seri numarası sayısı).")
    ort, med = o["Toplam_Saat"].mean(), k["medyan_saat"]
    ct = o["Katalog_CT"].max() if "Katalog_CT" in o else None
    c.append(f"Bir adet ortalama **{ort:.2f} saatte** işlendi (ortadaki değer/medyan {med:.2f} sa)"
             + (f"; katalogdaki süre {ct:g} sa." if ct and ct > 0 else "."))
    if "Alarm" in o:
        y = o[o["Alarm"] == "🔴 Yavaş"]
        if len(y):
            en = y.sort_values("Toplam_Saat", ascending=False).iloc[0]
            c.append(f"**{len(y)} adet** katalog süresini %{int(ALARM_TOLERANS * 100)}'den fazla aştı; en uzunu {en['S/N']} ({en['Toplam_Saat']:.2f} sa, {en['Operatorler']}).")
        else:
            c.append("Hiçbir adet katalog süresini aşmadı.")
    c.append(f"**{k['bolunmus_sn']} adet** tek kayıtta değil, vardiya/operatör değişimiyle birden fazla kayıtta tamamlandı; "
             "operatör karşılaştırmalarında bu işler saat paylarına göre bölüştürüldü (haksız kıyası önlemek için).")
    h = t.get("hafta")
    if h is not None and not h.empty:
        en = h.sort_values("Adet", ascending=False).iloc[0]
        dus = h.sort_values("Adet").iloc[0]
        kismi = bool(veri_aralik) and (dus["Hafta_Baslangic"] < veri_aralik[0] or dus["Hafta_Bitis"] > veri_aralik[1])
        c.append(f"En yoğun hafta **Hafta {int(en['Hafta'])}** ({int(en['Adet'])} adet, günlük ortalama {en['Gunluk_Ort_Adet']:.1f}); "
                 f"en düşük hafta Hafta {int(dus['Hafta'])} ({int(dus['Adet'])} adet" + ("; verinin kapsadığı hafta tam değil" if kismi else "") + ").")
    if k["setup_kayit"]:
        c.append(f"Bu operasyon için **{k['setup_saat']:.1f} saat set-up** ({k['setup_kayit']} kayıt) yazılmış.")
    op = t.get("operator")
    if op is not None and not op.empty:
        yeter = op[op["SN_Sayisi"] >= 5].sort_values("Esdeger_Ort_Sa")
        if len(yeter) >= 2:
            c.append(f"En az 5 parçaya dokunan operatörler arasında adet başına süre: en kısa **{yeter.iloc[0]['Operator']}** "
                     f"({yeter.iloc[0]['Esdeger_Ort_Sa']:.2f} sa), en uzun **{yeter.iloc[-1]['Operator']}** ({yeter.iloc[-1]['Esdeger_Ort_Sa']:.2f} sa). "
                     "Fark küçük örneklem ve iş karmaşasından da etkilenebilir.")
    return c

def _sn_anahtar(df: pd.DataFrame) -> list:
    c_p, c_o = _kol(df, "PARÇA NO_ÜRETİM_GİRİLEN", "PARÇA NO ÜRETİM GİRİLEN"), _kol(df, "OPERASYON_NO", "OPERASYON NO")
    return [c for c in (c_p, c_o) if c] + ["S/N"]


def _sn_idx(df: pd.DataFrame, anahtar: list) -> pd.MultiIndex:
    return pd.MultiIndex.from_frame(df[anahtar].astype(str).apply(lambda x: x.str.strip()))


def sn_genislet(df: pd.DataFrame, secilen_baslangic, secilen_bitis) -> pd.DataFrame:
    gun = df["TARİH"].dt.normalize()
    secili = ((gun >= pd.Timestamp(secilen_baslangic)) & (gun <= pd.Timestamp(secilen_bitis))).to_numpy()
    mi, sn_var = _sn_idx(df, _sn_anahtar(df)), df["S/N"].notna().to_numpy()
    dokunulan = mi[secili & sn_var]
    return df[secili | (mi.isin(dokunulan) & sn_var)]


def tam_tarihce(df: pd.DataFrame, df_ham) -> pd.DataFrame:
    if df_ham is None or "S/N" not in df.columns or "S/N" not in df_ham.columns:
        return df
    anahtar = [c for c in _sn_anahtar(df) if c in df_ham.columns]
    dok = _sn_idx(df[df["S/N"].notna()], anahtar)
    tam = _sn_idx(df_ham, anahtar).isin(dok) & df_ham["S/N"].notna().to_numpy()
    r = pd.concat([df[df["S/N"].isna()], df_ham[tam]])
    return r.sort_values("TARİH", kind="stable").reset_index(drop=True)
def _tarih_filtresi(st, df: pd.DataFrame) -> pd.DataFrame:
    if "TARİH" not in df.columns or "S/N" not in df.columns or df["TARİH"].notna().sum() == 0:
        return df
    mn, mx = df["TARİH"].min().date(), df["TARİH"].max().date()
    aralik = st.date_input("📅 Tarih aralığı", value=(mn, mx), min_value=mn, max_value=mx, key="sn_tarih_aralik",
                           help="Aralıkta dokunulan S/N'lerin aralık dışındaki (sarkan) kayıtları da dahil edilir.")
    if not (isinstance(aralik, (tuple, list)) and len(aralik) == 2) or (aralik[0] == mn and aralik[1] == mx):
        return df
    sonuc = sn_genislet(df, *aralik)
    gun = df["TARİH"].dt.normalize()
    ic = int(((gun >= pd.Timestamp(aralik[0])) & (gun <= pd.Timestamp(aralik[1]))).sum())
    st.caption(f"{ic} kayıt aralıkta + {len(sonuc) - ic} sarkan kayıt (aynı S/N'lerin aralık dışı kayıtları).")
    return sonuc

    
def render_sn_sekmesi(df: pd.DataFrame, pilot=PILOT, op_adi=lambda k: k, df_ham=None):
    import streamlit as st

    df = tam_tarihce(df, df_ham)
    uzun_haritasi_guncelle(df)   

    if "S/N" not in df.columns:
        st.error("Veride **S/N** kolonu yok. config.CANONICAL_COLUMNS + ETL'e S/N eklenmeden bu sekme ve AI S/N sorularına cevap veremez.")
        return
    secenekler = parca_op_secenekleri(df, pilot)
    etiketler = dict(secenekler)
    kpi_alan = st.container()          

    with st.expander("🔍 Detaylı Süzgeçler", expanded=False):
        s1, s2 = st.columns([3, 2])
        with s1:
            secim = st.selectbox("Parça / operasyon", [s[0] for s in secenekler], format_func=lambda a: etiketler[a], key="sn_parca")
        with s2:
            df = _tarih_filtresi(st, df)

    def tablo(df_, ek=None, **kw):
        st.dataframe(gorsel(df_, ek), use_container_width=True, hide_index=True, **kw)

    if secim == TUMU:
        _tum_sn_gorunumu(st, df, op_adi, tablo, kpi_alan)
        return
    kisa, op = secim
    t = sn_tum_analiz(df, kisa, op, op_adi)
    ozet, detay, setup, k = t["ozet"], t["detay"], t["setup"], t["kapsam"]
    if ozet.empty:
        st.info("Seçili filtrelerde bu parça/operasyon için S/N'li üretim kaydı yok.")
        return

    def indir(tab, ad, anahtar):
        st.download_button(f"⬇️ {ad} (CSV)", tab.to_csv(index=False).encode("utf-8-sig"),
                           file_name=f"{uzun_kod(kisa)}_op{op}_{anahtar}.csv", mime="text/csv", key=f"dl_{anahtar}")

    with kpi_alan:
        st.markdown("### 📈 Genel Üretim Özeti")
        c1, c2, c3, c4, c5 = st.columns(5)
        c1.metric("📦 Tamamlanan Adet", f"{k['sn_sayisi']}", help="Adet = farklı seri numarası (S/N) sayısı.")
        c2.metric("⏱️ Medyan Süre / Adet", f"{k['medyan_saat']:.2f} sa", help="Parçaların yarısı bundan kısa, yarısı uzun sürede işlendi.")
        c3.metric("🔴 Katalog Süresini Aşan", int((ozet["Alarm"] == "🔴 Yavaş").sum()) if "Alarm" in ozet else 0,
                  help=f"Süresi katalog süresinin %{int(ALARM_TOLERANS * 100)}'den fazla üstünde olan adetler.")
        c4.metric("⚖️ Bölünerek Yapılan", k["bolunmus_sn"], help="Birden fazla kayıtta (vardiya/operatör değişimiyle) tamamlanan adetler.")
        c5.metric("🛠️ Set-up", f"{k['setup_saat']:.1f} sa", help="Bu parça ve operasyona yazılmış set-up süreleri toplamı.")
        with st.expander("📝 Özet yorumlar"):
            for cumle in ozet_cumleleri(t, (df["TARİH"].min(), df["TARİH"].max()) if "TARİH" in df.columns else None):
                st.markdown(f"- {cumle}")    
    with st.expander("📘 Okuma rehberi — rakamlar nasıl bulunuyor?"):
        st.markdown(
            "- **Adet:** Satır sayısı değil, farklı seri numarası (S/N) sayısıdır. Bir parça iki vardiyada, iki ustayla bitse bile 1 adettir.\n"
            "- **Hafta:** Perşembe'den başlar, Çarşamba biter. Bir parça, **tamamlandığı (son kaydı girilen)** haftaya yazılır.\n"
            "- **Net üretim (operatör tablosu):** Bölünen bir işte adet, kişinin harcadığı saat oranında paylaşılır. Yarım iş yapana yarım adet yazılır.\n"
            f"- **Katalog süresine göre:** Parçanın toplam süresi katalog süresinin %{int(ALARM_TOLERANS * 100)} üstünü aşıyorsa işaretlenir; kayıt değil parça toplamına bakılır.\n"
            "- **Set-up ve duruşlar:** Seri numarası taşımazlar; parça ve operasyona bağlanır, adedin yanında ayrıca gösterilir.\n"
            "- **Medyana göre fark %:** Eksi değer parçanın medyandan kısa, artı değer uzun sürdüğünü gösterir.")
    if k["sn_bos_kayit"]:
        st.warning(f"Seri numarası girilmemiş {k['sn_bos_kayit']} üretim kaydı var ({k['sn_bos_saat']:.1f} saat); bu süreler adede dahil edilemedi.")
    if k["setup_belirsiz_kayit"]:
        st.caption(f"Operasyonu belirsiz 'SETUP' kaydı: {k['setup_belirsiz_kayit']} adet ({k['setup_belirsiz_saat']:.1f} sa) — hesaplamalara eklenmedi.")
    st.markdown("---")

    sek = st.tabs(["📅 Hafta & Gün", "📦 Parçalar (S/N)", "🔎 Tek Parçayı Doğrula", "🧭 Parça Rotası (A→Z)", "👷 Operatör",
                   "🕒 Vardiya", "🏭 Tezgah", "🛠️ Set-up & Duruş", "🤖 AI'a giden veri"])

    with sek[0]:
        st.caption("Önce özet tablolar (seçili parça), altta bir haftayı seçip o haftada fabrikada ne olduğuna tek tek bakabilirsiniz.")
        st.markdown("**Hafta özeti (Perşembe→Çarşamba)**")
        tablo(t["hafta"])
        indir(t["hafta"], "Hafta özeti", "hafta")
        st.markdown("**Gün bazlı adet (tamamlanma tarihine göre)**")
        tablo(t["gunluk"])
        st.markdown("---")
        _hafta_detayi(st, df, t, op_adi, kisa, op, tablo)

    with sek[1]:
        gunler_ = sorted((pd.Timestamp(g) for g in ozet["Son_Tarih"].dt.normalize().dropna().unique()), reverse=True)
        with st.expander("🔍 Detaylı Süzgeçler"):
            f0, f1, f2 = st.columns(3)
            gun_ = f0.selectbox("Tamamlandığı gün", [None] + gunler_, format_func=_gun_etiketi, key="sn_adet_gun")
            ara = f1.text_input("S/N ara", key="sn_ara", placeholder="örn. 2G7J")
            opsec = f2.selectbox("Operatör", ["Tümü"] + sorted(detay["Operator"].dropna().unique().tolist()), key="sn_adet_op")
            sadece_bol = f0.checkbox("Sadece bölünenler", key="sn_bol")
            sadece_yav = f1.checkbox("Sadece katalogu aşanlar", key="sn_yav")
        x = ozet
        if gun_ is not None:
            x = x[x["Son_Tarih"].dt.normalize() == gun_]
        if ara:
            x = x[x["S/N"].astype(str).str.contains(ara, case=False, na=False)]
        if opsec != "Tümü":
            x = x[x["S/N"].isin(detay[detay["Operator"] == opsec]["S/N"])]
        if sadece_bol:
            x = x[x["Bolunmus_Is"]]
        if sadece_yav and "Alarm" in x:
            x = x[x["Alarm"] == "🔴 Yavaş"]
        st.caption(f"{len(x)} / {len(ozet)} parça gösteriliyor.")
        tablo(x)
        indir(ozet, "Tüm parçalar", "sn")

    with sek[2]:
        st.caption("Bir parçanın ham kayıtlarını görüp toplamı kendiniz doğrulayın.")
        tum_operatorler = ["Tümü"] + sorted(detay["Operator"].dropna().unique().tolist())
        secili_op = st.selectbox("Önce operatör seç (isteğe bağlı):", tum_operatorler, key="op_sec")
        uygun_snler = (detay[detay["Operator"] == secili_op]["S/N"].unique().tolist() if secili_op != "Tümü" else ozet["S/N"].tolist())
        sn = st.selectbox("Seri numarası", uygun_snler, key="sn_sec")
        r = ozet[ozet["S/N"] == sn].iloc[0]
        st.info(f"**{sn}** — toplam **{r['Toplam_Saat']:.2f} saat**, {int(r['Kayit'])} kayıt. Çalışanlar: {r['Operatorler']}. "
                f"Vardiya: {r.get('Vardiyalar', '—')}. Tezgah: {r.get('Tezgah', '—')}. Tamamlanma: {r['Son_Tarih']:%d.%m.%Y} (Hafta {int(r['Hafta'])}).")
        st.markdown("**Bu operasyondaki ham kayıtlar** (süreleri toplayınca yukarıdaki toplam çıkar)")
        tablo(detay[detay["S/N"] == sn])
        if not t["rota"].empty:
            st.markdown("**Bu parçanın tüm operasyonlardaki yolculuğu**")
            tablo(t["rota"][t["rota"]["S/N"] == sn])
        indir(detay, "Tüm parça kayıtları", "detay")

    with sek[3]:
        with st.expander("ℹ️ Bu sekme ne gösteriyor?"):
            st.info("Bir parçanın fabrikadaki yolculuğu: her operasyonda kaç saat harcandı ve kim yaptı. "
                    "'Başka Parça Kodunda da Yazılı' dolu ise aynı seri numarası iki farklı parça koduna girilmiş demektir (veri hatası şüphesi).")
        tablo(t["rota"])
        indir(t["rota"], "Parça rotası", "rota")

    medyan_cumle = f"Karşılaştırma ölçüsü: adet başına süre; parçaların medyanı {k['medyan_saat']:.2f} sa."
    with sek[4]:
        with st.expander("ℹ️ Nasıl okunur?"):
            st.info("* **Net Üretim (adet):** Bölünen işte kişinin saat payı kadar adet yazılır; yarım iş yapana yarım adet.\n"
                    "* **Ort. Süre (sa/adet):** Toplam çalışma saati ÷ net üretim. Ortak işler dahil kişinin genel hızıdır.\n"
                    "* **Tek Başına Bitirdiği / Tek Başına Ort. Süre:** Karışıksız kıyas; başkasıyla paylaşmadan bitirdiği parçalar.\n"
                    "* **Medyana Göre Fark %:** Eksi: medyandan hızlı, artı: yavaş. Az sayıda parçaya dokunanların farkı güvenilir değildir.")
        st.caption(medyan_cumle)
        if not t["operator"].empty:
            st.bar_chart(t["operator"].set_index("Operator")["Esdeger_Ort_Sa"], height=220)
        tablo(t["operator"], _OP_EK)
        indir(t["operator"], "Operatör analizi", "operator")
    with sek[5]:
        st.caption(medyan_cumle)
        tablo(t["vardiya"], _OP_EK)
    with sek[6]:
        st.caption(medyan_cumle)
        tablo(t["tezgah"], _OP_EK)

    with sek[7]:
        st.markdown(f"**Set-up (op {op}, toplam {k['setup_saat']:.1f} sa)**")
        tablo(setup)
        st.markdown("**Parçaya yazılmış duruşlar**")
        st.caption("Duruş kayıtlarının çoğunda operasyon numarası değil sebep yazılıdır; bu yüzden parçanın tüm duruşları listelenir. "
                   "'Operasyon Bilgisi' sütunu, kaydın bu operasyona açıkça yazılıp yazılmadığını gösterir.")
        dsec = st.radio("Göster", [f"Parçanın tüm duruşları", f"Yalnızca op {op}'a açıkça yazılanlar"], horizontal=True, key="sn_durus_sec")
        d_ = t["durus"] if dsec.startswith("Parçanın") else parca_duruslari(df, kisa, op, op_adi, sadece_op=True)
        tablo(d_)

    with sek[8]:
        st.caption("Asistan bu metni okuyor; yukarıdaki tablolarla birebir aynı veri.")
        st.code(sn_ai_baglami(df, pilot, op_adi), language="csv")


def _hafta_detayi(st, df, t, op_adi, kisa, op, tablo):
    st.markdown("### 🔍 Hafta Detayı — seçtiğiniz haftada ne olmuş?")
    hl = hafta_listesi(df)
    if hl.empty:
        st.info("Tarih bilgisi olmadığı için hafta detayı gösterilemiyor.")
        return
    etiket = {int(r.Hafta): f"Hafta {int(r.Hafta)} · {r.Hafta_Baslangic:%d.%m} – {r.Hafta_Bitis:%d.%m.%Y} · {int(r.Kayit)} kayıt" for r in hl.itertuples()}
    h1, h2 = st.columns([2, 2])
    hafta = h1.selectbox("Hafta", list(etiket), format_func=lambda w: etiket[w], key="sn_hf_sec")
    if kisa:
        kapsam = h2.radio("Kapsam", ["Tüm fabrika", f"Yalnızca {uzun_kod(kisa)} (tüm operasyonları)"], horizontal=True, key="sn_hf_kapsam")
    else:   
        kapsam = "Tüm fabrika"
    satir = hl[hl["Hafta"] == hafta].iloc[0]
    bas, bit = satir["Hafta_Baslangic"], satir["Hafta_Bitis"]
    d = hafta_kayitlari(df, hafta, op_adi)
    if kapsam.startswith("Yalnızca"):
        d = d[d["Parça"].astype(str).str.contains(kisa, na=False, regex=False)]
    if d.empty:
        st.info("Bu kapsamda kayıt yok.")
        return

    for cumle in hafta_cumleleri(d, hafta, bas, bit, t.get("ozet") if kisa else None, uzun_kod(kisa) if kisa else "", op or ""):
        st.markdown(f"- {cumle}")

    kx = f"sn_hf_{hafta}_{'f' if kapsam.startswith('Tüm') else 'p'}"
    durumlar = [x for x in DURUM_SIRASI if x in set(d["Durum"].dropna())] + sorted(set(d["Durum"].dropna()) - set(DURUM_SIRASI))
    tezgahlar = sorted(d["TEZGAH"].dropna().astype(str).unique()) if "TEZGAH" in d.columns else []
    operatorler = sorted(d["Operatör"].dropna().unique())
    p_say = d.groupby("Parça").size().sort_values(ascending=False)
    uz_map = d.groupby("Parça")["Uzun_Ref"].agg(_ilk_dolu)
    p_etk = {p_: (f"{uz_map.get(p_)} ({p_}) · {n_} kayıt" if uz_map.get(p_) and uz_map.get(p_) != p_ else f"{p_} · {n_} kayıt") for p_, n_ in p_say.items()}

    with st.expander("🔍 Detaylı Süzgeçler (durum · tezgah · operatör · parça · operasyon · arama)"):
        st.caption("Süzgeçler aşağıdaki grafik ve tüm tablolara uygulanır.")
        f1, f2, f3 = st.columns(3)
        s_durum = f1.multiselect("Durum", durumlar, default=durumlar, key=kx + "_d")
        s_tez = f2.multiselect("Tezgah", tezgahlar, default=tezgahlar, key=kx + "_t")
        s_op = f3.multiselect("Operatör", operatorler, default=operatorler, key=kx + "_o")
        g1, g2, g3 = st.columns([3, 2, 3])
        s_parca = g1.multiselect("Parça (uzun referans · kısa kod) — boşsa hepsi", list(p_etk), format_func=lambda p_: p_etk[p_], key=kx + "_p")
        d_p = d[d["Parça"].isin(s_parca)] if s_parca else d
        op_sec = sorted(d_p["Op_No"].dropna().astype(str).unique(), key=lambda z: (not z.isdigit(), int(z) if z.isdigit() else 0, z))
        s_opn = g2.multiselect("Operasyon (60, 20 …; SETUP dahil) — boşsa hepsi", op_sec, key=kx + "_opn")
        s_ara = g3.text_input("Ara: parça · uzun referans · operasyon", key=kx + "_a", placeholder="örn. 2547M01G03 veya M01G03 60")
    x = d[d["Durum"].isin(s_durum) & d["Operatör"].isin(s_op)]
    if tezgahlar:
        x = x[x["TEZGAH"].astype(str).isin(s_tez)]
    if s_parca:
        x = x[x["Parça"].isin(s_parca)]
    if s_opn:
        x = x[x["Op_No"].astype(str).isin(s_opn)]
    if s_ara:
        x = x[_ara_maske(_hay(x), s_ara)]
    st.caption(f"{len(x)} kayıt · toplam {x['Sure_Saat'].sum():.2f} saat")
    _uy = uzun_uyumsuz(x)
    if len(_uy):
        st.warning(f"⚠️ {len(_uy)} kayıtta kısa kod ile uzun referans uyuşmuyor (örn. {_uy.iloc[0]['Parça']} ↔ {_uy.iloc[0]['Uzun_Ref']}). "
                   "Parça aramasında kısa kod esas alınır; bu kayıtlar 'Tüm kayıtlar' sekmesinde Uzun Referans sütunundan görülebilir.")
    if x.empty:
        st.info("Süzgeçlere uyan kayıt yok.")
        return

    gun = pd.date_range(bas, bit, freq="D")
    g = x.groupby([x["TARİH"].dt.normalize(), "Durum"])["Sure_Saat"].sum().unstack(fill_value=0).reindex(gun, fill_value=0)
    kolonlar = [c for c in DURUM_SIRASI if c in g.columns] + [c for c in g.columns if c not in DURUM_SIRASI]
    g = g[kolonlar]
    g.index = [f"{d_:%d.%m} {GUN_ADI[d_.weekday()]}" for d_ in g.index]
    st.markdown("**Günlük çalışma ve duruş (saat)**")
    try:
        st.bar_chart(g, color=[DURUM_RENK.get(c, "#90A4AE") for c in g.columns], height=260)
    except Exception:
        st.bar_chart(g, height=260)

    sub = st.tabs(["👷 Operatör × Parça", "👤 Operatör", "🏭 Tezgah", "🔩 Parça", "📦 Tamamlanan S/N", "🧾 Tüm kayıtlar"])
    
    with sub[0]:
        st.caption("Kim hangi parçada kaç saat çalıştı (üretim, set-up, duruş ayrı). S/N sütunları: o operatörün o parçada kaydı bulunan seri numaraları.")
        op_parca = hafta_operator_parca(x)
        tablo(op_parca)
        st.download_button("⬇️ Operatör × Parça (CSV)", op_parca.to_csv(index=False).encode("utf-8-sig"),
                           file_name=f"hafta{hafta}_operator_parca.csv", mime="text/csv", key=kx + "_dl1")
        with st.expander("Isı haritası: operatör (satır) × parça (sütun) — toplam saat"):
            piv = op_parca.pivot_table(index="Operatör", columns="Parça", values="Toplam_Sa", aggfunc="sum", fill_value=0).round(1)
            st.dataframe(piv, use_container_width=True)
            
    with sub[1]:
        p = x.pivot_table(index="Operatör", columns="Durum", values="Sure_Saat", aggfunc="sum", fill_value=0)
        p["Toplam"] = p.sum(axis=1)
        if "S/N" in x.columns:
            p["İşlenen S/N Adedi"] = x.groupby("Operatör")["S/N"].nunique()
        else:
            p["İşlem Gören Parça Kodu"] = x.groupby("Operatör")["Parça"].nunique()
        p["Çalıştığı Gün"] = x.groupby("Operatör")["TARİH"].nunique()
        st.dataframe(p.sort_values("Toplam", ascending=False).round(2).reset_index(), use_container_width=True, hide_index=True)
        
    with sub[2]:
        if "TEZGAH" in x.columns:
            p = x.pivot_table(index="TEZGAH", columns="Durum", values="Sure_Saat", aggfunc="sum", fill_value=0)
            p["Toplam"] = p.sum(axis=1)
            p["Operatör Sayısı"] = x.groupby("TEZGAH")["Operatör"].nunique()
            if "S/N" in x.columns:
                p["İşlenen S/N Adedi"] = x.groupby("TEZGAH")["S/N"].nunique()
            else:
                p["İşlem Gören Parça Kodu"] = x.groupby("TEZGAH")["Parça"].nunique()
            st.dataframe(p.sort_values("Toplam", ascending=False).round(2).reset_index(), use_container_width=True, hide_index=True)
            
    with sub[3]:
        p = x.pivot_table(index="Parça", columns="Durum", values="Sure_Saat", aggfunc="sum", fill_value=0)
        p["Toplam"] = p.sum(axis=1)
        p["Operatör Sayısı"] = x.groupby("Parça")["Operatör"].nunique()
        p["Kayıt"] = x.groupby("Parça").size()
        
        if "S/N" in x.columns:
            sn_x = x[x["S/N"].notna()]
            # 1. O hafta o parçada toplam kaç eşsiz (S/N + Operasyon) kombinasyonuna DOKUNULDU?
            p["Dokunulan S/N (Op Bazlı)"] = sn_x.drop_duplicates(["S/N", "Op_No"]).groupby("Parça").size()
            
            # BİTTİ kolonunu bul (önceden yazdığımız _bitti_kolonu_bul fonksiyonu)
            c_b = _bitti_kolonu(x)
            if c_b:
                # 2. O hafta BİTTİ yazan satırları ayıkla (Büyük/Küçük harf İ-I duyarlılığı kapatılmış güvenli maske)
                maske_bitti = sn_x[c_b].astype(str).str.contains("BİTTİ|bitti|Bitti|BITTI|bıttı", regex=True, na=False)
                b_ = sn_x[maske_bitti].drop_duplicates(["S/N", "Op_No"])
                p["Tamamlanan (BİTTİ)"] = b_.groupby("Parça").size()
                
                # 3. Dokunulanlardan Bitenleri çıkararak Yarım Kalanı bul
                p["Yarım Kalan (WIP)"] = (p["Dokunulan S/N (Op Bazlı)"].fillna(0) - p["Tamamlanan (BİTTİ)"].fillna(0)).clip(lower=0)
            
            # Oluşan yeni kolonlardaki NaN değerleri 0'a çevir ve tam sayı (int) yap
            yeni_kolonlar = [c for c in p.columns if "S/N" in c or "BİTTİ" in c or "WIP" in c]
            for c_ in yeni_kolonlar:
                p[c_] = p[c_].fillna(0).astype(int)
                
        p.insert(0, "Uzun Referans", p.index.map(x.groupby("Parça")["Uzun_Ref"].agg(_ilk_dolu)))
        st.dataframe(p.sort_values("Toplam", ascending=False).round(2).reset_index(), use_container_width=True, hide_index=True)
        
    with sub[4]:
        o = t.get("ozet")
        if o is not None and not o.empty:
            w = o[o["Hafta"] == hafta]
            st.caption((f"{uzun_kod(kisa)} op {op}" if kisa else "Tüm parça/operasyonlar") + f": bu hafta tamamlanan {len(w)} adet (süzgeçlerden bağımsız).")
            tablo(w)
        else:
            st.info("Seçili parça için S/N verisi yok.")
            
    with sub[5]:
        kol = [c for c in ["TARİH", "VARDİYA", "BAŞLANGIÇ_SAAT", "BİTİŞ_SAAT", "TEZGAH", "Operatör", "Parça", "Uzun_Ref", "Operasyon", "Kod_Ope", "Durum",
                           "Durus_Kategorisi", "Durus_Sebebi_Ham", "Sure_Saat", "ÜRETİM_C/T", "Sapma_Yuzdesi", "S/N"] if c in x.columns]
        ek = {"Parça": "Parça No (kısa)", "Operasyon": "Operasyon No", "Durus_Kategorisi": "Duruş Kategorisi", "Durus_Sebebi_Ham": "Duruş Sebebi",
              "ÜRETİM_C/T": "Hedef C/T (sa)", "Sapma_Yuzdesi": "Sapma %"}
        
        df_gosterim = x[kol].copy().dropna(axis=1, how='all')
        sirala_kolonu = ["TARİH"] + (["BAŞLANGIÇ_SAAT"] if "BAŞLANGIÇ_SAAT" in df_gosterim.columns else [])
        
        tablo(df_gosterim.sort_values(sirala_kolonu), ek)
        st.download_button("⬇️ Bu haftanın kayıtları (CSV)", df_gosterim.to_csv(index=False).encode("utf-8-sig"),
                           file_name=f"hafta{hafta}_kayitlar.csv", mime="text/csv", key=kx + "_dl2")