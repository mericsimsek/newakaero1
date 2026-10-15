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
from uretim_akisi import hazirla, sn_sayilari, biten_operasyon, biten_parcalar_tablosu, gunluk_ai_baglami_olustur, op_ad, _op_sirala, CALISMA

st.set_page_config(page_title="Günlük Üretim Akışı", layout="wide", page_icon="🏭")
st.markdown("<style>[data-testid='stMetricValue']{font-size:2.4rem;font-weight:700}</style>", unsafe_allow_html=True)
st.title("🏭 Günlük Üretim Akışı")

df_ham, _df, kaynak_notu = veri_yukle_ve_filtrele("home")


T, c_bitti = hazirla(df_ham)

if T.empty or T["Tarih"].notna().sum() == 0:
    st.warning("Gösterilecek tarih/veri bulunamadı.")
    st.stop()

mn, mx = T["Tarih"].min().date(), T["Tarih"].max().date()
col_tarih, col_bos = st.columns([2, 6]) # Ekranı 2'ye 6 oranında böler
with col_tarih:
    sec = st.date_input("📅 Tarih", value=(mx, mx), min_value=mn, max_value=mx, format="DD.MM.YYYY", key="f_tarih")
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
sn_islem, sn_biten = sn_sayilari(G)

k1, k2, k3, k4, k5 = st.columns(5)
k1.metric("👷 Toplam Çalışan", calisma["Operatör"].nunique())
k2.metric("🏭 Aktif Tezgah", calisma["Tezgah"].nunique())
k3.metric("⏱️ Kayıtlı Süre", f"{G['Süre'].sum():,.1f} sa")
k4.metric("📦 İşlem Gören Parça (benzersiz S/N)", sn_islem,
          help="Seçili tarihte üretim kaydı olan farklı S/N sayısı.")
k5.metric("✅ Biten Parça", sn_biten if c_bitti else "—",
          help="Rotasının SON (FİNİSH) operasyonu BİTTİ olan farklı S/N sayısı (nihai mamul).")
if st.toggle("📋 Biten parçaları göster", key="goster_biten", disabled=not c_bitti):
    bt = biten_parcalar_tablosu(G)
    if bt.empty:
        st.info("Seçili tarihte nihai mamul olarak biten parça yok.")
    else:
        ozet = bt.groupby("Parça").size().sort_values(ascending=False)
        st.markdown(f"**Toplam {len(bt)} parça bitti:** " + "  ·  ".join(f"{p}: **{n}**" for p, n in ozet.items()))
        st.dataframe(
            bt.rename(columns={"Op": "Son Op", "Saat": "Son Kayıt Saati"}),
            use_container_width=True, hide_index=True,
            height=min(80 + 35 * len(bt), 600),
            column_config={"Tarih": st.column_config.DateColumn(format="DD.MM.YYYY")},
        )
st.markdown("---")

st.markdown("### 🔍 Detaylı İnceleme ve Filtreler")
c1, c2, c3 = st.columns(3)
s_tez = c1.multiselect("🏭 Tezgah", sorted(T["Tezgah"].unique()), placeholder="Tümü", key="f_tez")
s_kisi = c2.multiselect("👷 Operatör", sorted(T["Operatör"].unique()), placeholder="Tümü", key="f_kisi")
s_durum = c3.multiselect("Tür (Üretim/Duruş)", sorted(T["Durum"].unique()), placeholder="Tümü",
                         format_func=lambda d: d.replace("_", " "), key="f_durum")

d1, d2, d3, d4, d5 = st.columns([2, 2, 1, 2, 2])
s_parca = d1.multiselect("🔩 Parça Kodu", sorted(T["Parça"].dropna().unique()), placeholder="Tümü", key="f_parca")
s_uzun = d2.multiselect("🏷️ Uzun Referans", sorted(T["Uzun Ref"].replace("-", np.nan).dropna().unique()),
                        placeholder="Tümü", key="f_uzun")
s_op = d3.multiselect("⚙️ Op", sorted(T["Op"].dropna().unique(), key=_op_sirala), placeholder="Tümü", key="f_op")
s_ara = d4.text_input("🔎 Arama (S/N)", placeholder="Örn: 2H50M", key="f_ara")
s_sira = d5.radio("Sırala", ["⏱ Tezgah ve Zaman", "👤 Operatör ve Zaman", "🔗 S/N'ye Göre (Grupla)"], horizontal=True, key="f_sira")


x = G
for kol, secim in (("Tezgah", s_tez), ("Operatör", s_kisi), ("Durum", s_durum), ("Parça", s_parca), ("Uzun Ref", s_uzun), ("Op", s_op)):
    if secim:
        x = x[x[kol].isin(secim)]

if s_ara:
    s_ara_kck = s_ara.strip().lower()
    x = x[x["S/N"].fillna("").str.lower().str.contains(s_ara_kck, regex=False)]

if "Tezgah" in s_sira:
    x = x.sort_values(["Tezgah", "_sira", "_bit_dk"], na_position="last")
elif "Operatör" in s_sira:
    x = x.sort_values(["Operatör", "_sira", "_bit_dk"], na_position="last")
else:
    anahtar = x["_a"].fillna(pd.Series("~" + x.index.astype(str), index=x.index))
    x = (x.assign(_k=anahtar, _ilk=x.groupby(anahtar)["_pz"].transform("min"))
         .sort_values(["_ilk", "_k", "_pz"], na_position="last"))
ai_baglam = gunluk_ai_baglami_olustur(G, (d_bas, d_bit))
if x.empty:
    st.info("Seçili tarih ve filtrelerle eşleşen kayıt yok. Filtre seçimlerin korunuyor; tarihi ya da filtreleri değiştirebilirsin.")
    st.stop()

if not x.empty and (s_tez or s_kisi or s_durum or s_parca or s_uzun):

    o_uretim = x[x["Durum"] == "Üretim"]["Süre"].sum()
    o_durus = x[x["Durum"] == "Plansız_Duruş"]["Süre"].sum()
    o_setup = x[x["Durum"] == "Set_Up"]["Süre"].sum()
    o_rework = x[x["Durum"] == "Rework"]["Süre"].sum()
    
    oz1, oz2, oz3, oz4 = st.columns(4)
    oz1.metric("⚙️ Net Üretim", f"{o_uretim:,.2f} sa")
    oz2.metric("🛑 Plansız Duruş", f"{o_durus:,.2f} sa")
    oz3.metric("🔧 Set-up & Rework", f"{(o_setup + o_rework):,.2f} sa")
    oz4.metric("✅ Biten Parça", sn_sayilari(x)[1] if c_bitti else "—")

gos = x[["Tarih", "Vardiya", "Saat", "Tezgah", "Operatör", "Parça – Op", "Uzun Ref", "S/N", 
         "İşlem Adımı", "Süre", "Adet", "Durum / Sebep"]].copy()

gos["Tarih"] = gos["Tarih"].dt.date
gos["S/N"] = gos["S/N"].fillna("")
gos = gos.rename(columns={"Süre": "Süre (sa)"})

st.dataframe(gos, use_container_width=True, hide_index=True, height=650,key=f"tablo_{s_sira}_{len(x)}", column_config={
    "Tarih": st.column_config.DateColumn(format="DD.MM.YYYY"),
    "Süre (sa)": st.column_config.NumberColumn(format="%.2f"),
})

st.session_state["gunluk_ai_baglami"] = ai_baglam   # AI sayfası buradan okuyabilir

with st.expander("🤖 AI'a giden günlük özet (önizleme)"):
    st.code(ai_baglam, language=None)