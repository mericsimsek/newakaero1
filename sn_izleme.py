# -*- coding: utf-8 -*-
"""
sn_izleme.py — Seri numarası (S/N) bazlı parça izleme, adet, hafta özeti ve hafta detayı.

KURAL: parça adedi S/N'ye göre sayılır. Aynı S/N'nin bütün kayıtları (vardiya/operatör değişse de) birleşir, 1 ADET olur.
Pilot: 2547M01G03 (kısa kod M01G03), operasyon 60.
HAFTA: Perşembe başlar, Çarşamba biter. Hafta no = (tarih − 3 gün)'ün ISO hafta numarası. S/N, tamamlandığı haftaya yazılır.
SET-UP / DURUŞ: ham veride operasyon numarası değil metin ("60-SETUP", "ITEM TEKRARI") taşırlar; parça+operasyona bağlanırlar.
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


def uzun_kod(kisa: str) -> str:
    return UZUN_KOD_ONEKI.get(kisa, "") + kisa


def hafta_no(tarih: pd.Series) -> pd.Series:
    w = (tarih - pd.Timedelta(days=3)).dt.isocalendar().week
    return w.astype("Int64") if w.isna().any() else w.astype(int)   # tarihi boş kayıt varsa <NA>


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

    parca_mask = df[c_parca].astype(str).str.contains(parca_kisa, na=False)
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
    sayisal_kolonlar = r.select_dtypes(include=[np.number]).columns
    r[sayisal_kolonlar] = r[sayisal_kolonlar].round(2)
    return r.reset_index().sort_values("Ilk_Tarih").reset_index(drop=True)


def parca_duruslari(df: pd.DataFrame, parca_kisa: str = "M01G03", operasyon: str = "60", op_adi=lambda k: k,
                    sadece_op: bool = False) -> pd.DataFrame:
    """Parçaya yazılmış duruş kayıtları. Ham veride duruş satırında 'OPERASYON NO' alanı çoğunlukla operasyon numarası değil
    SEBEP METNİ taşır (örn. 'ITEM TEKRARI'; duruşların yalnızca ~%10'unda numara var). Bu yüzden varsayılan olarak parçanın
    tüm duruşları listelenir ve 'Operasyon_Kaydi' kolonu ilgili operasyona açıkça yazılıp yazılmadığını gösterir.
    sadece_op=True: yalnızca operasyon numarası açıkça yazılanlar."""
    c_parca = _kol(df, "PARÇA NO_ÜRETİM_GİRİLEN", "PARÇA NO ÜRETİM GİRİLEN")
    c_op = _kol(df, "OPERASYON_NO", "OPERASYON NO")
    c_kisi = _kol(df, "Operator_Kod", "OPERATÖR")
    c_sebep = _kol(df, "Durus_Sebebi_Ham") or c_op
    if None in (c_parca, c_kisi, c_sebep, c_op):
        return pd.DataFrame()
    d = df[df[c_parca].astype(str).str.contains(parca_kisa, na=False) & df["Durum"].astype(str).str.contains("Duruş")].copy()
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
    """Veride kayıt olan haftalar (Perşembe→Çarşamba)."""
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
    """Seçili haftanın TÜM ham kayıtları (operatör adı normalize, parça/operasyon kolonları standart)."""
    c = _kolonlar(df)
    d = df[df["TARİH"].notna() & (hafta_no(df["TARİH"]) == int(hafta)).fillna(False).astype(bool)].copy()
    d["Operatör"] = d[c["kisi"]].map(op_adi).map(ad_norm) if c["kisi"] else None
    d["Parça"] = d[c["parca"]].astype(str).where(d[c["parca"]].notna(), "(parça yok)") if c["parca"] else "(parça yok)"
    d["Operasyon"] = d[c["op"]].astype(str).str.strip() if c["op"] else ""
    return d


def hafta_operator_parca(d: pd.DataFrame, ayrinti: bool = True) -> pd.DataFrame:
    """Operatör × Parça: kim hangi parçada kaç saat (üretim / set-up / duruş / rework / yemek)."""
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
        r["Gun_Sayisi"] = g["TARİH"].nunique()
        r["Operasyonlar"] = g["Operasyon"].agg(lambda s: ", ".join(sorted({x for x in s if x and x != "nan"})[:8]))
    return r.reset_index().sort_values(["Toplam_Sa"], ascending=False).round(2).reset_index(drop=True)


def hafta_operator_parca_tum(df: pd.DataFrame, op_adi=lambda k: k) -> pd.DataFrame:
    """AI / teyit için: tüm haftalar, kompakt (Hafta, Operatör, Parça, saatler, kayıt). Yemek hariç."""
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
    """Hafta için düz Türkçe özet cümleleri (patron okusun diye)."""
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
        c.append(f"En çok üretim saati harcanan parça: **{pu.index[0]}** ({pu.iloc[0]:.1f} sa)" + (f", sonra {pu.index[1]} ({pu.iloc[1]:.1f} sa)." if len(pu) > 1 else "."))
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
    sirali = ozet.sort_values(["Toplam_Saat", "Son_Tarih"], ascending=[False, True])
    cols = [c for c in ["S/N", "Toplam_Saat", "Kayit", "Operatorler", "Vardiyalar", "Tezgah", "Son_Tarih"] if c in ozet.columns]
    t["uclar"] = pd.concat([sirali.head(5)[cols].assign(Grup="EN YAVAŞ 5"),
                            sirali.tail(5).iloc[::-1][cols].assign(Grup="EN HIZLI 5")]).reset_index(drop=True)
    t["rota"] = sn_rota(df, parca_kisa, op_adi)
    t["durus"] = parca_duruslari(df, parca_kisa, operasyon, op_adi)
    return t


_AI_TABLOLAR = [
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
    ("durus", 19, "PARÇAYA YAZILMIŞ DURUŞLAR (sebep metniyle; Operasyon_Kaydi='yazılmamış': duruş satırında operasyon no yok, parçanın duruşu olarak listelenir)"),
    ("uclar", 20, "EN YAVAŞ 5 ve EN HIZLI 5 S/N (hazır sıralama; eşit sürelerde Son_Tarih'i eski olan önce)"),
]


def sn_ai_baglami(df: pd.DataFrame, pilot=PILOT, op_adi=lambda k: k) -> str:
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
)


# =========================================================================== EKRAN: okunabilir görünüm katmanı
# Ana veri (AI / teyit / indirme) teknik adlarını korur; yalnızca ekranda gösterirken sade adlar kullanılır.
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
}
_DEGER = {"🔴 Yavaş": "🔴 Katalog süresini aştı", "🟢 Normal": "🟢 Katalog süresi içinde", True: "Evet", False: "Hayır"}


def gorsel(tablo: pd.DataFrame, ek: dict | None = None) -> pd.DataFrame:
    """Ekran için sade kolon adları, okunur tarih/evet-hayır. Ana veriyi DEĞİŞTİRMEZ (kopya döner)."""
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


_OP_EK = {"Kayit": "İşlem Kaydı", "Toplam_Saat": "Toplam Çalışma (sa)", "Ilk_Tarih": "İlk Çalıştığı Gün", "Son_Tarih": "Son Çalıştığı Gün"}


def ozet_cumleleri(t: dict, veri_aralik=None) -> list[str]:
    """Patron için düz Türkçe genel özet (tablolardan türetilir; ek bir hesap sayısı uydurmaz)."""
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


def render_sn_sekmesi(df: pd.DataFrame, pilot=PILOT, op_adi=lambda k: k):
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

    def tablo(df_, ek=None, **kw):
        st.dataframe(gorsel(df_, ek), use_container_width=True, hide_index=True, **kw)

    def indir(tab, ad, anahtar):
        st.download_button(f"⬇️ {ad} (CSV)", tab.to_csv(index=False).encode("utf-8-sig"),
                           file_name=f"{uzun_kod(kisa)}_op{op}_{anahtar}.csv", mime="text/csv", key=f"dl_{anahtar}")

    # ------------------------------------------------------------ ÜST: kartlar + düz Türkçe özet + okuma rehberi
    st.markdown("### 📈 Genel Üretim Özeti")
    c1, c2, c3, c4, c5 = st.columns(5)
    c1.metric("📦 Tamamlanan Adet", f"{k['sn_sayisi']}", help="Adet = farklı seri numarası (S/N) sayısı. Aynı parçanın birden fazla kaydı 1 adet sayılır.")
    c2.metric("⏱️ Medyan Süre / Adet", f"{k['medyan_saat']:.2f} sa", help="Parçaların yarısı bundan kısa, yarısı bundan uzun sürede işlendi.")
    c3.metric("🔴 Katalog Süresini Aşan", int((ozet["Alarm"] == "🔴 Yavaş").sum()) if "Alarm" in ozet else 0,
              help=f"Toplam süresi katalog süresinin %{int(ALARM_TOLERANS * 100)}'den fazla üstünde olan adetler.")
    c4.metric("⚖️ Bölünerek Yapılan", k["bolunmus_sn"], help="Tek kayıtta değil, vardiya/operatör değişimiyle birden fazla kayıtta tamamlanan adetler.")
    c5.metric("🛠️ Set-up", f"{k['setup_saat']:.1f} sa", help="Bu parça ve operasyona yazılmış set-up süreleri toplamı.")
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

    # ------------------------------------------------------------ 1) HAFTA & GÜN (+ hafta detayı)
    with sek[0]:
        st.caption("Önce özet tablolar (seçili parça), altta bir haftayı seçip o haftada fabrikada ne olduğuna tek tek bakabilirsiniz.")
        st.markdown("**Hafta özeti (Perşembe→Çarşamba)**")
        tablo(t["hafta"])
        indir(t["hafta"], "Hafta özeti", "hafta")
        st.markdown("**Gün bazlı adet (tamamlanma tarihine göre)**")
        tablo(t["gunluk"])
        st.markdown("---")
        _hafta_detayi(st, df, t, op_adi, kisa, op, tablo)

    # ------------------------------------------------------------ 2) PARÇALAR
    with sek[1]:
        st.caption("Her satır 1 adet. Aşağıdaki süzgeçlerle istediğiniz parçalara bakın.")
        f1, f2, f3, f4 = st.columns([2, 2, 1, 1])
        ara = f1.text_input("S/N ara", key="sn_ara", placeholder="örn. 2G7J")
        opsec = f2.selectbox("Operatör", ["Tümü"] + sorted(detay["Operator"].dropna().unique().tolist()), key="sn_adet_op")
        sadece_bol = f3.checkbox("Sadece bölünenler", key="sn_bol")
        sadece_yav = f4.checkbox("Sadece katalogu aşanlar", key="sn_yav")
        x = ozet
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

    # ------------------------------------------------------------ 3) TEK PARÇA DOĞRULA
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

    # ------------------------------------------------------------ 4) ROTA
    with sek[3]:
        with st.expander("ℹ️ Bu sekme ne gösteriyor?"):
            st.info("Bir parçanın fabrikadaki yolculuğu: her operasyonda kaç saat harcandı ve kim yaptı. "
                    "'Başka Parça Kodunda da Yazılı' dolu ise aynı seri numarası iki farklı parça koduna girilmiş demektir (veri hatası şüphesi).")
        tablo(t["rota"])
        indir(t["rota"], "Parça rotası", "rota")

    # ------------------------------------------------------------ 5-7) OPERATÖR / VARDİYA / TEZGAH
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

    # ------------------------------------------------------------ 8) SET-UP & DURUŞ
    with sek[7]:
        st.markdown(f"**Set-up (op {op}, toplam {k['setup_saat']:.1f} sa)**")
        tablo(setup)
        st.markdown("**Parçaya yazılmış duruşlar**")
        st.caption("Duruş kayıtlarının çoğunda operasyon numarası değil sebep yazılıdır; bu yüzden parçanın tüm duruşları listelenir. "
                   "'Operasyon Bilgisi' sütunu, kaydın bu operasyona açıkça yazılıp yazılmadığını gösterir.")
        dsec = st.radio("Göster", [f"Parçanın tüm duruşları", f"Yalnızca op {op}'a açıkça yazılanlar"], horizontal=True, key="sn_durus_sec")
        d_ = t["durus"] if dsec.startswith("Parçanın") else parca_duruslari(df, kisa, op, op_adi, sadece_op=True)
        tablo(d_)

    # ------------------------------------------------------------ 9) AI
    with sek[8]:
        st.caption("Asistan bu metni okuyor; yukarıdaki tablolarla birebir aynı veri.")
        st.code(sn_ai_baglami(df, pilot, op_adi), language="csv")


def _hafta_detayi(st, df, t, op_adi, kisa, op, tablo):
    """Seçili haftada ne olmuş: filtreler, günlük grafik, operatör×parça, tezgah, parça, ham kayıtlar."""
    st.markdown("### 🔍 Hafta Detayı — seçtiğiniz haftada ne olmuş?")
    hl = hafta_listesi(df)
    if hl.empty:
        st.info("Tarih bilgisi olmadığı için hafta detayı gösterilemiyor.")
        return
    etiket = {int(r.Hafta): f"Hafta {int(r.Hafta)} · {r.Hafta_Baslangic:%d.%m} – {r.Hafta_Bitis:%d.%m.%Y} · {int(r.Kayit)} kayıt" for r in hl.itertuples()}
    h1, h2 = st.columns([2, 2])
    hafta = h1.selectbox("Hafta", list(etiket), format_func=lambda w: etiket[w], key="sn_hf_sec")
    kapsam = h2.radio("Kapsam", ["Tüm fabrika", f"Yalnızca {uzun_kod(kisa)} (tüm operasyonları)"], horizontal=True, key="sn_hf_kapsam")
    satir = hl[hl["Hafta"] == hafta].iloc[0]
    bas, bit = satir["Hafta_Baslangic"], satir["Hafta_Bitis"]
    d = hafta_kayitlari(df, hafta, op_adi)
    if kapsam.startswith("Yalnızca"):
        d = d[d["Parça"].astype(str).str.contains(kisa, na=False)]
    if d.empty:
        st.info("Bu kapsamda kayıt yok.")
        return

    for cumle in hafta_cumleleri(d, hafta, bas, bit, t.get("ozet"), uzun_kod(kisa), op):
        st.markdown(f"- {cumle}")

    # ---- filtreler (screenshot'taki gibi): Durum / Tezgah / Operatör / Parça-kod ara
    st.caption("Süzgeçler aşağıdaki grafik ve tüm tablolara uygulanır.")
    kx = f"sn_hf_{hafta}_{'f' if kapsam.startswith('Tüm') else 'p'}"
    durumlar = [x for x in DURUM_SIRASI if x in set(d["Durum"].dropna())] + sorted(set(d["Durum"].dropna()) - set(DURUM_SIRASI))
    tezgahlar = sorted(d["TEZGAH"].dropna().astype(str).unique()) if "TEZGAH" in d.columns else []
    operatorler = sorted(d["Operatör"].dropna().unique())
    f1, f2, f3, f4 = st.columns([2, 2, 3, 2])
    s_durum = f1.multiselect("Durum", durumlar, default=durumlar, key=kx + "_d")
    s_tez = f2.multiselect("Tezgah", tezgahlar, default=tezgahlar, key=kx + "_t")
    s_op = f3.multiselect("Operatör", operatorler, default=operatorler, key=kx + "_o")
    s_ara = f4.text_input("Parça / kod ara", key=kx + "_a", placeholder="örn. M14P04")
    x = d[d["Durum"].isin(s_durum) & d["Operatör"].isin(s_op)]
    if tezgahlar:
        x = x[x["TEZGAH"].astype(str).isin(s_tez)]
    if s_ara:
        x = x[x["Parça"].astype(str).str.contains(s_ara, case=False, na=False) | x["Operasyon"].astype(str).str.contains(s_ara, case=False, na=False)]
    st.caption(f"{len(x)} kayıt · toplam {x['Sure_Saat'].sum():.2f} saat")
    if x.empty:
        st.info("Süzgeçlere uyan kayıt yok.")
        return

    # ---- günlük durum grafiği
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
        st.caption("Kim hangi parçada kaç saat çalıştı (üretim, set-up, duruş ayrı).")
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
        p["Parça Sayısı"] = x.groupby("Operatör")["Parça"].nunique()
        p["Çalıştığı Gün"] = x.groupby("Operatör")["TARİH"].nunique()
        st.dataframe(p.sort_values("Toplam", ascending=False).round(2).reset_index(), use_container_width=True, hide_index=True)
    with sub[2]:
        if "TEZGAH" in x.columns:
            p = x.pivot_table(index="TEZGAH", columns="Durum", values="Sure_Saat", aggfunc="sum", fill_value=0)
            p["Toplam"] = p.sum(axis=1)
            p["Operatör Sayısı"] = x.groupby("TEZGAH")["Operatör"].nunique()
            p["Parça Sayısı"] = x.groupby("TEZGAH")["Parça"].nunique()
            st.dataframe(p.sort_values("Toplam", ascending=False).round(2).reset_index(), use_container_width=True, hide_index=True)
    with sub[3]:
        p = x.pivot_table(index="Parça", columns="Durum", values="Sure_Saat", aggfunc="sum", fill_value=0)
        p["Toplam"] = p.sum(axis=1)
        p["Operatör Sayısı"] = x.groupby("Parça")["Operatör"].nunique()
        p["Kayıt"] = x.groupby("Parça").size()
        st.dataframe(p.sort_values("Toplam", ascending=False).round(2).reset_index(), use_container_width=True, hide_index=True)
    with sub[4]:
        o = t.get("ozet")
        if o is not None and not o.empty:
            w = o[o["Hafta"] == hafta]
            st.caption(f"{uzun_kod(kisa)} op {op}: bu hafta tamamlanan {len(w)} adet (süzgeçlerden bağımsız).")
            tablo(w)
        else:
            st.info("Seçili parça için S/N verisi yok.")
    with sub[5]:
        kol = [c for c in ["TARİH", "VARDİYA", "BAŞLANGIÇ_SAAT", "BİTİŞ_SAAT", "TEZGAH", "Operatör", "Parça", "Operasyon", "Durum",
                           "Durus_Kategorisi", "Durus_Sebebi_Ham", "Sure_Saat", "ÜRETİM_C/T", "Sapma_Yuzdesi", "S/N"] if c in x.columns]
        ek = {"Parça": "Parça No", "Operasyon": "Operasyon No", "Durus_Kategorisi": "Duruş Kategorisi", "Durus_Sebebi_Ham": "Duruş Sebebi",
              "ÜRETİM_C/T": "Hedef C/T (sa)", "Sapma_Yuzdesi": "Sapma %"}
        tablo(x[kol].sort_values(["TARİH"] + (["BAŞLANGIÇ_SAAT"] if "BAŞLANGIÇ_SAAT" in kol else [])), ek)
        st.download_button("⬇️ Bu haftanın kayıtları (CSV)", x[kol].to_csv(index=False).encode("utf-8-sig"),
                           file_name=f"hafta{hafta}_kayitlar.csv", mime="text/csv", key=kx + "_dl2")