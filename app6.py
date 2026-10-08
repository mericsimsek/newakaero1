# -*- coding: utf-8 -*-
"""
app6.py — Günlük Üretim Akışı (Sade, Anlaşılır Yönetici Görünümü)
"""
from __future__ import annotations

import re

import numpy as np
import pandas as pd
import streamlit as st

from common import veri_yukle_ve_filtrele

OPERATOR_SOZLUGU = {
    "Op_001": "AŞKIN SAKALLI", "Op_002": "BERAT TUNÇ", "Op_003": "CİHAN SARIGÜL", "Op_004": "EMRE TOPRAK",
    "Op_005": "EREN TAMER YELEKÇİ", "Op_006": "ERTUĞRUL ÖZDEMİR", "Op_007": "HAMZA KARAASLAN", "Op_008": "HÜSEYİN YENİCE",
    "Op_009": "HÜSEYİN YILMAZ", "Op_010": "İLKER SEZDİ", "Op_011": "MEHMET ALİ ÇOLAK", "Op_012": "MEHMET EMRE KARAKAŞ",
    "Op_013": "MERT KUŞCU", "Op_014": "MESUT ARDIÇ", "Op_015": "MUHAMMED KORKMAZ", "Op_016": "NAZİF GÜÇLÜDAĞ",
    "Op_017": "NURULLAH ZENGİN", "Op_018": "ÖZCAN ÇELİK", "Op_019": "TANER GÜÇLÜDAĞ", "Op_020": "UMUT ÖZKAN", "Op_021": "YAŞAR ŞENOL",
}
CALISMA = ["Üretim", "Set_Up", "Rework"]
_TR = str.maketrans("İıŞşĞğÜüÖöÇç", "IiSsGgUuOoCc")
_BITTI_ADAYLARI = ["finish", "durum2", "islem durumu"]

def op_ad(kod): return OPERATOR_SOZLUGU.get(kod, kod)

def _txt(v):
    if v is None or (not isinstance(v, str) and pd.isna(v)): return None
    s = str(v).strip()
    return None if s.lower() in ("", "nan", "none", "nat", "-") else s

def _opno(v):
    s = _txt(v)
    return s[:-2] if s and s.endswith(".0") else s

def _dk(v):
    if v is None: return np.nan
    if isinstance(v, (int, float, np.integer, np.floating)):
        return np.nan if pd.isna(v) else round((float(v) % 1) * 1440)
    if hasattr(v, "hour") and hasattr(v, "minute"):
        return v.hour * 60 + v.minute
    m = re.search(r"(\d{1,2})[:.](\d{2})", str(v))
    return int(m[1]) * 60 + int(m[2]) if m else np.nan

def _vardiya(v):
    m = re.findall(r"(\d{1,2})[:.](\d{2})", str(v or ""))
    if len(m) < 2: return np.nan, np.nan
    return int(m[0][0]) * 60 + int(m[0][1]), int(m[1][0]) * 60 + int(m[1][1])

def _hm(dk):
    return "-" if pd.isna(dk) else f"{int(dk) % 1440 // 60:02d}:{int(dk) % 60:02d}"

def _bas_zaman(tarih, vard, bas_dk):
    if pd.isna(tarih) or pd.isna(bas_dk): return pd.NaT
    gun = pd.Timestamp(tarih).normalize()
    vb, ve = _vardiya(vard)
    if not pd.isna(vb) and ve < vb and bas_dk < vb:
        gun += pd.Timedelta(days=1)
    return gun + pd.Timedelta(minutes=float(bas_dk))

def _bitti_maske(s: pd.Series) -> pd.Series:
    return (s.astype(str).str.replace("\u0307", "", regex=False).str.translate(_TR)
            .str.upper().str.contains("BITTI", regex=False, na=False))

def _bitti_kolonu(x: pd.DataFrame):
    adaylar = [c for c in x.columns if str(c).strip() == "parça"]
    adaylar += [c for c in x.columns if c not in adaylar and str(c).strip().translate(_TR).lower() in _BITTI_ADAYLARI]
    for c in adaylar:
        if _bitti_maske(x[c]).any(): return c
    en, en_n = None, 0
    for c in x.columns:
        if pd.api.types.is_numeric_dtype(x[c]) or pd.api.types.is_datetime64_any_dtype(x[c]): continue
        n = int(_bitti_maske(x[c]).sum())
        if n > en_n: en, en_n = c, n
    return en or (adaylar[0] if adaylar else None)

def _op_sirala(v):
    return (not v.isdigit(), int(v) if v.isdigit() else 0, v)

@st.cache_data(show_spinner="Veri hazırlanıyor…")
def hazirla(veri: pd.DataFrame):
    x = veri.reset_index(drop=True)
    n = len(x)
    def k(ad): return x[ad].tolist() if ad in x.columns else [None] * n

    tarih = pd.to_datetime(x["TARİH"], errors="coerce").dt.normalize()
    vard = [_txt(v) for v in k("VARDİYA")]
    bas = [_dk(v) for v in k("BAŞLANGIÇ_SAAT")]
    bit = [_dk(v) for v in k("BİTİŞ_SAAT")]
    
    parca = [_txt(v) for v in k("PARÇA NO_ÜRETİM_GİRİLEN")]
    if all(p is None for p in parca):
        parca = [_txt(v) for v in k("KOD & OPE.")]
        
    uzun_ref = [_txt(v) for v in k("PARÇA NO_UZUN_REFERANS")]
    sebep = [_txt(v) for v in k("Durus_Sebebi_Ham")]
    op = [_opno(v) for v in k("OPERASYON_NO")]
    kisi = [_txt(v) for v in k("Operator_Kod")]
    c_bitti = _bitti_kolonu(x)

    T = pd.DataFrame({
        "Tarih": tarih,
        "Vardiya": [v or "-" for v in vard],
        "Saat": [f"{_hm(a)} – {_hm(b)}" for a, b in zip(bas, bit)],
        "_bas": pd.to_datetime([_bas_zaman(t, v, b) for t, v, b in zip(tarih, vard, bas)]),
        "_bit_dk": bit,
        "Tezgah": [_txt(v) or "-" for v in k("TEZGAH")],
        "Operatör": [op_ad(v) if v else "-" for v in kisi],
        "Parça": parca,
        "Uzun Ref": [v or "-" for v in uzun_ref],
        "Op": op,
        "Parça – Op": [(p or "-") + (f" – Op {o}" if o else "") for p, o in zip(parca, op)],
        "S/N": [_txt(v) for v in k("S/N")],
        "Durum": [_txt(v) or "-" for v in k("Durum")],
        "Sebep": [v or "" for v in sebep],
        "Süre": pd.to_numeric(x["Sure_Saat"], errors="coerce").fillna(0.0),
        "_bitti": _bitti_maske(x[c_bitti]).tolist() if c_bitti else [False] * n,
    })
    
    def format_durum(d, s):
        d_clean = str(d).replace("_", " ")
        if d_clean in ["Plansız Duruş", "Set Up"] and s:
            return f"{d_clean} ({s})"
        return d_clean
    T["Durum / Sebep"] = [format_durum(d, s) for d, s in zip(T["Durum"], T["Sebep"])]

    # --- S/N Süreci: Anlaşılır Zincir Mantığı (Kafa Karıştıran Devir İptal) ---
    U = T[(T["Durum"] == "Üretim") & T["S/N"].notna()].copy()
    U["_a"] = U["Parça"].fillna("-") + "|" + U["Op"].fillna("-") + "|" + U["S/N"]
    U = U.sort_values(["_a", "_bas", "_bit_dk"], na_position="last")
    
    def surec_zinciri(df_grup):
        parcalar = [f"{row['Operatör'].split()[0]} ({row['Süre']:.2f}sa)" for _, row in df_grup.iterrows()]
        return " ➔ ".join(parcalar)
    
    if not U.empty:
        surec_map = U.groupby("_a", sort=False).apply(surec_zinciri)
        U["S/N İşlem Geçmişi"] = U["_a"].map(surec_map)
    else:
        U["S/N İşlem Geçmişi"] = ""

    g = U.groupby("_a", sort=False)
    if c_bitti:
        U["_bs"] = g["_bitti"].cumsum()
        U["_adet"] = (U["_bitti"] & (U["_bs"] == 1)).astype(int)
        U["Adet"] = np.where(U["_adet"] == 1, "1", "")
    else:
        U["_adet"], U["Adet"] = 0, ""

    T["_a"] = U["_a"].reindex(T.index)
    T["S/N İşlem Geçmişi"] = U["S/N İşlem Geçmişi"].reindex(T.index).fillna("")
    T["Adet"] = U["Adet"].reindex(T.index).fillna("")
    T["_adet"] = U["_adet"].reindex(T.index).fillna(0).astype(int)
    return T, c_bitti

st.set_page_config(page_title="Günlük Üretim Akışı", layout="wide", page_icon="🏭")
st.markdown("<style>[data-testid='stMetricValue']{font-size:2.4rem;font-weight:700}</style>", unsafe_allow_html=True)
st.title("🏭 Günlük Üretim Akışı")

df_ham, _df, kaynak_notu = veri_yukle_ve_filtrele("home")
st.caption(kaynak_notu)

T, c_bitti = hazirla(df_ham)
if T.empty or T["Tarih"].notna().sum() == 0:
    st.warning("Gösterilecek tarih/veri bulunamadı.")
    st.stop()

mn, mx = T["Tarih"].min().date(), T["Tarih"].max().date()
sec = st.date_input("📅 Tarih (tek gün ya da aralık)", value=(mx, mx), min_value=mn, max_value=mx, format="DD.MM.YYYY")
if isinstance(sec, (tuple, list)):
    if not sec: st.stop()
    d_bas, d_bit = sec[0], sec[-1]
else:
    d_bas, d_bit = sec, sec

G = T[(T["Tarih"] >= pd.Timestamp(d_bas)) & (T["Tarih"] <= pd.Timestamp(d_bit))]
if G.empty:
    st.warning("Seçilen tarihte kayıt yok.")
    st.stop()

calisma = G[G["Durum"].isin(CALISMA)]
st.markdown("### 🏢 Fabrika Genel Durumu (Seçili Tarih)")
k1, k2, k3, k4 = st.columns(4)
k1.metric("👷 Toplam Çalışan", calisma["Operatör"].nunique())
k2.metric("🏭 Aktif Tezgah", calisma["Tezgah"].nunique())
k3.metric("⏱️ Toplam Kayıtlı Süre", f"{G['Süre'].sum():,.1f} sa")
k4.metric("📦 Toplam Biten S/N", int(G["_adet"].sum()) if c_bitti else "—")
st.markdown("---")

st.markdown("### 🔍 Detaylı İnceleme ve Filtreler")
c1, c2, c3 = st.columns(3)
s_tez = c1.multiselect("🏭 Tezgah", sorted(G["Tezgah"].unique()), placeholder="Tümü")
s_kisi = c2.multiselect("👷 Operatör", sorted(G["Operatör"].unique()), placeholder="Tümü")
s_durum = c3.multiselect("Tür (Üretim/Duruş)", sorted(G["Durum"].unique()), placeholder="Tümü", format_func=lambda d: d.replace("_", " "))

d1, d2, d3, d4, d5 = st.columns([2, 2, 1, 2, 2])
s_parca = d1.multiselect("🔩 Parça Kodu", sorted(G["Parça"].dropna().unique()), placeholder="Tümü")
s_uzun = d2.multiselect("🏷️ Uzun Referans", sorted(G["Uzun Ref"].replace("-", np.nan).dropna().unique()), placeholder="Tümü")
s_op = d3.multiselect("⚙️ Op", sorted(G["Op"].dropna().unique(), key=_op_sirala), placeholder="Tümü")
s_ara = d4.text_input("🔎 Arama (S/N)", placeholder="Örn: 2H50M")
s_sira = d5.radio("Sırala", ["⏱ Tezgah ve Zaman", "🔗 S/N'ye Göre (Grupla)"], horizontal=True)

x = G
for kol, secim in (("Tezgah", s_tez), ("Operatör", s_kisi), ("Durum", s_durum), ("Parça", s_parca), ("Uzun Ref", s_uzun), ("Op", s_op)):
    if secim:
        x = x[x[kol].isin(secim)]

if s_ara:
    s_ara_kck = s_ara.strip().lower()
    x = x[x["S/N"].fillna("").str.lower().str.contains(s_ara_kck, regex=False)]

if s_sira.startswith("⏱"):
    x = x.sort_values(["Tezgah", "_bas", "_bit_dk"], na_position="last")
else:
    anahtar = x["_a"].fillna(pd.Series("~" + x.index.astype(str), index=x.index))
    x = (x.assign(_k=anahtar, _ilk=x.groupby(anahtar)["_bas"].transform("min"))
         .sort_values(["_ilk", "_k", "_bas"], na_position="last"))

if not x.empty and (s_tez or s_kisi or s_durum or s_parca or s_uzun):
    st.info(f"**Aşağıdaki Tablonun Özeti:** Toplam {len(x)} satır listelendi.")
    o_uretim = x[x["Durum"] == "Üretim"]["Süre"].sum()
    o_durus = x[x["Durum"] == "Plansız_Duruş"]["Süre"].sum()
    o_setup = x[x["Durum"] == "Set_Up"]["Süre"].sum()
    o_rework = x[x["Durum"] == "Rework"]["Süre"].sum()
    
    oz1, oz2, oz3, oz4 = st.columns(4)
    oz1.metric("⚙️ Net Üretim", f"{o_uretim:,.2f} sa")
    oz2.metric("🛑 Plansız Duruş", f"{o_durus:,.2f} sa")
    oz3.metric("🔧 Set-up & Rework", f"{(o_setup + o_rework):,.2f} sa")
    oz4.metric("📦 Çıkan Net Adet", int(x["_adet"].sum()) if c_bitti else "—")

gos = x[["Tarih", "Vardiya", "Saat", "Tezgah", "Operatör", "Parça – Op", "Uzun Ref", "S/N", 
         "S/N İşlem Geçmişi", "Süre", "Adet", "Durum / Sebep"]].copy()

gos["Tarih"] = gos["Tarih"].dt.date
gos["S/N"] = gos["S/N"].fillna("")
gos = gos.rename(columns={"Süre": "Süre (sa)"})

st.dataframe(gos, use_container_width=True, hide_index=True, height=650, column_config={
    "Tarih": st.column_config.DateColumn(format="DD.MM.YYYY"),
    "Süre (sa)": st.column_config.NumberColumn(format="%.2f"),
})