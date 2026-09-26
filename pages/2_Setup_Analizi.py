# -*- coding: utf-8 -*-
"""
2_Setup_Analizi.py — "Cihan M92'de 3 kere, toplam 2.5 saat set-up yapmış,
ortalama 50 dk" tarzı soruların cevabı: Operatör × Parça bazında set-up
süresi kırılımı + genel setup istatistikleri (SMED altyapısı).
"""
from __future__ import annotations

import plotly.express as px
import streamlit as st

from common import veri_yukle_ve_filtrele, compute_setup_parca_kirilimi, metrik_aciklamasi

st.set_page_config(page_title="Setup Analizi", layout="wide", page_icon="🔧")
st.title("🔧 Setup (Hazırlık) Süresi Analizi — Operatör × Parça Kırılımı")
st.caption(
    "Hangi operatör, hangi parçanın set-up'ını, kaç kere ve ortalama kaç dakikada yapmış — "
    "SMED (hızlı kalıp değişimi) çalışmasının başlangıç noktası."
)

df_ham, df, kaynak_notu = veri_yukle_ve_filtrele("setup")
st.caption(kaynak_notu)

if df.empty:
    st.warning("Seçili filtrelerle eşleşen veri yok.")
    st.stop()

kirilim = compute_setup_parca_kirilimi(df)
if kirilim.empty:
    st.info("Bu filtrede Set_Up kaydı yok (veya PARÇA NO_ÜRETİM_GİRİLEN / Operator_Kod kolonları eksik).")
    st.stop()

# ---------------------------------------------------------------- GENEL ÖZET
toplam_setup_saat = kirilim["Toplam_Setup_Saat"].sum()
toplam_setup_adet = kirilim["Setup_Adet"].sum()
ort_dakika_genel = (toplam_setup_saat * 60 / toplam_setup_adet) if toplam_setup_adet > 0 else 0

k1, k2, k3 = st.columns(3)
k1.metric("Toplam Setup Saati", f"{toplam_setup_saat:,.1f} sa")
k2.metric("Toplam Setup Adedi", f"{toplam_setup_adet:,}")
k3.metric("Genel Ortalama Süre", f"{ort_dakika_genel:,.1f} dk")

st.markdown("---")

# ---------------------------------------------------------------- OPERATÖR SEÇEREK İNCELE
st.subheader("👷 Operatör Bazında İnceleme")
operatorler = sorted(kirilim["Operator_Kod"].unique())
secilen_op = st.selectbox("Operatör seç (veya 'Tümü')", ["(Tümü)"] + operatorler)

goster = kirilim if secilen_op == "(Tümü)" else kirilim[kirilim["Operator_Kod"] == secilen_op]

if secilen_op != "(Tümü)":
    op_toplam_saat = goster["Toplam_Setup_Saat"].sum()
    op_toplam_adet = goster["Setup_Adet"].sum()
    st.markdown(
        f"**{secilen_op}**, incelenen dönemde toplam **{op_toplam_adet} kez** set-up yapmış, "
        f"bunlar toplam **{op_toplam_saat:.2f} saat** ({op_toplam_saat * 60:.0f} dakika) sürmüş. "
        f"Parça bazında dağılım aşağıda:"
    )
    fig = px.bar(
        goster.sort_values("Toplam_Setup_Saat", ascending=False),
        x="PARÇA NO_ÜRETİM_GİRİLEN", y="Ort_Setup_Dakika", color="Setup_Adet",
        color_continuous_scale="Oranges", hover_data=["Toplam_Setup_Saat", "Setup_Adet"],
        title=f"{secilen_op} — Parça Bazında Ortalama Setup Süresi (dk)",
    )
    st.plotly_chart(fig, use_container_width=True)
else:
    op_ozet = kirilim.groupby("Operator_Kod").agg(
        Toplam_Setup_Saat=("Toplam_Setup_Saat", "sum"),
        Toplam_Adet=("Setup_Adet", "sum"),
        Farkli_Parca_Sayisi=("PARÇA NO_ÜRETİM_GİRİLEN", "nunique"),
    ).reset_index()
    op_ozet["Ort_Setup_Dakika"] = (op_ozet["Toplam_Setup_Saat"] * 60 / op_ozet["Toplam_Adet"]).round(1)
    fig = px.bar(
        op_ozet.sort_values("Ort_Setup_Dakika", ascending=False),
        x="Operator_Kod", y="Ort_Setup_Dakika", color="Toplam_Adet",
        color_continuous_scale="Oranges", hover_data=["Toplam_Setup_Saat", "Farkli_Parca_Sayisi"],
        title="Operatör Bazında Ortalama Setup Süresi (dk)",
    )
    st.plotly_chart(fig, use_container_width=True)
    st.dataframe(op_ozet.sort_values("Toplam_Setup_Saat", ascending=False), use_container_width=True, hide_index=True)

st.markdown("---")
st.subheader("📋 Tam Kırılım Tablosu (Operatör × Parça)")
st.dataframe(
    goster.sort_values("Toplam_Setup_Saat", ascending=False),
    use_container_width=True, hide_index=True,
)

with st.expander("📖 Formül"):
    st.markdown(metrik_aciklamasi("setup_ortalama"))
    st.markdown(
        "`Ort_Setup_Dakika = MEAN(Sure_Saat) × 60` — bir operatörün belirli bir parçadaki "
        "TÜM set-up kayıtlarının ortalama süresi (dakika cinsinden)."
    )
