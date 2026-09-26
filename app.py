# -*- coding: utf-8 -*-
"""
app.py — Ana Sayfa (Home)
Üretim Zekası ve Karar Destek Dashboard'u — genel özet + sol menüden diğer
detay sayfalarına (Zaman Çizelgesi, Setup Analizi, Operatör Günlüğü,
AI Asistan) geçiş.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

from common import (
    RENK, DURUM_SIRASI, METRIC_REGISTRY, metrik_aciklamasi, kolon_var,
    veri_yukle_ve_filtrele, compute_tezgah_oee, compute_operator_karne,
    compute_pareto, compute_vardiya_yorgunluk, compute_mtbf_mttr, compute_heatmap,
)

st.set_page_config(page_title="Üretim Zekası Dashboard", layout="wide", page_icon="🏭")

st.title("🏭 Üretim Zekası ve Karar Destek Dashboard'u")

df_ham, df, kaynak_notu = veri_yukle_ve_filtrele("home")
st.caption(kaynak_notu)

if df.empty:
    st.warning("Seçili filtrelerle eşleşen veri yok.")
    st.stop()

# ---------------------------------------------------------------- KPI
toplam_uretim = df.loc[df["Durum"] == "Üretim", "Sure_Saat"].sum() if kolon_var(df, "Durum", "Sure_Saat") else 0
toplam_durus = df.loc[df["Durum"] == "Plansız_Duruş", "Sure_Saat"].sum() if kolon_var(df, "Durum", "Sure_Saat") else 0
toplam_setup = df.loc[df["Durum"] == "Set_Up", "Sure_Saat"].sum() if kolon_var(df, "Durum", "Sure_Saat") else 0
toplam_rework = df.loc[df["Durum"] == "Rework", "Sure_Saat"].sum() if kolon_var(df, "Durum", "Sure_Saat") else 0
planlanan = toplam_uretim + toplam_durus + toplam_rework
kullanilabilirlik = (toplam_uretim + toplam_rework) / planlanan * 100 if planlanan > 0 else 0

k1, k2, k3, k4, k5 = st.columns(5)
k1.metric("Toplam Üretim Saati", f"{toplam_uretim:,.1f} sa")
k2.metric("Toplam Duruş Saati", f"{toplam_durus:,.1f} sa")
k3.metric("Setup Saati", f"{toplam_setup:,.1f} sa")
k4.metric("Rework Saati", f"{toplam_rework:,.1f} sa")
k5.metric("Kullanılabilirlik", f"%{kullanilabilirlik:,.1f}")

with st.expander("📖 Kullanılabilirlik nasıl hesaplandı?"):
    st.markdown(metrik_aciklamasi("kullanilabilirlik"))

st.markdown("---")

# ---------------------------------------------------------------- GEZİNME REHBERİ
st.info(
    "👈 Sol menüden **detay sayfalarına** geçebilirsiniz:\n\n"
    "- **1 Zaman Çizelgesi** — seçilen gün/tezgah için saat saat Gantt + olay olay dökümü\n"
    "- **2 Setup Analizi** — operatör × parça bazında set-up süreleri (kim, hangi parça, kaç dakika)\n"
    "- **3 Operatör Günlüğü** — bir operatörün bir gündeki dakika dakika hareketi\n"
    "- **4 AI Asistan** — soru sor, hangi formülle hesaplandığını da açıklasın"
)

sekmeler = st.tabs([
    "Tezgah Özeti", "Duruş Pareto", "Operatör Karnesi", "Parça-Tezgah Isı Haritası",
    "Vardiya Yorgunluk", "MTBF & MTTR",
])

with sekmeler[0]:
    tezgah_ozet = compute_tezgah_oee(df)
    if tezgah_ozet.empty:
        st.info("Veri yok.")
    else:
        uzun = tezgah_ozet.melt(id_vars="TEZGAH", value_vars=[c for c in ["Uretim_Saat", "Plansiz_Durus_Saat", "Set_Up_Saat", "Yemek_Saat", "Rework_Saat"] if c in tezgah_ozet.columns], var_name="Durum", value_name="Saat")
        renk_haritasi_ing = {"Uretim_Saat": RENK["Üretim"], "Plansiz_Durus_Saat": RENK["Plansız_Duruş"], "Set_Up_Saat": RENK["Set_Up"], "Yemek_Saat": RENK["Yemek_Mola"], "Rework_Saat": RENK["Rework"]}
        fig = px.bar(uzun, x="TEZGAH", y="Saat", color="Durum", color_discrete_map=renk_haritasi_ing, barmode="stack", title="Tezgah Başına Durum Dağılımı")
        st.plotly_chart(fig, use_container_width=True)
        st.dataframe(tezgah_ozet, use_container_width=True, hide_index=True)
        with st.expander("📖 Formüller"):
            for k in ["kullanilabilirlik", "kalite_orani", "performans_orani", "gercek_oee"]:
                st.markdown(metrik_aciklamasi(k))
                st.markdown("---")

with sekmeler[1]:
    pareto = compute_pareto(df)
    if pareto.empty:
        st.info("Plansız duruş kaydı yok.")
    else:
        fig = go.Figure()
        fig.add_bar(x=pareto["Durus_Kategorisi"], y=pareto["Toplam_Saat"], marker_color="#C62828", name="Toplam Saat")
        fig.add_trace(go.Scatter(x=pareto["Durus_Kategorisi"], y=pareto["Kumulatif_%"], yaxis="y2", mode="lines+markers", name="Kümülatif %", line=dict(color="#1565C0")))
        fig.update_layout(title="Duruş Sebepleri Pareto", yaxis2=dict(overlaying="y", side="right", range=[0, 105]))
        st.plotly_chart(fig, use_container_width=True)
        st.dataframe(pareto, use_container_width=True, hide_index=True)
        with st.expander("📖 Formül"):
            st.markdown(metrik_aciklamasi("pareto_kumulatif"))

with sekmeler[2]:
    karne = compute_operator_karne(df)
    if karne.empty:
        st.info("Yeterli veri yok (min saat eşiğini geçen operatör bulunamadı).")
    else:
        fig = px.scatter(karne, x="Toplam_Saat", y="Islem_Saat_Hizi", size="Islem_Sayisi", color="CT_Bazli_Sapma_Yuzde" if "CT_Bazli_Sapma_Yuzde" in karne.columns else None, hover_name="Operator_Kod", color_continuous_scale="RdYlGn_r", title="Operatör Karnesi")
        st.plotly_chart(fig, use_container_width=True)
        st.dataframe(karne, use_container_width=True, hide_index=True)
        with st.expander("📖 Formüller"):
            for k in ["ct_bazli_sapma", "emsal_bazli_sapma", "ct_dusuk_guven_yuzde"]:
                st.markdown(metrik_aciklamasi(k))
                st.markdown("---")

with sekmeler[3]:
    heat = compute_heatmap(df)
    if heat.empty:
        st.info("Yeterli veri yok.")
    else:
        fig = px.imshow(heat, aspect="auto", color_continuous_scale="Reds", labels=dict(x="Tezgah", y="Parça No", color="Saat"))
        st.plotly_chart(fig, use_container_width=True)

with sekmeler[4]:
    vy = compute_vardiya_yorgunluk(df)
    if vy.empty:
        st.info("Vardiya_Ici_Pozisyon_Yuzde hesaplanamadı (BAŞLANGIÇ_SAAT/VARDİYA eksik olabilir).")
    else:
        fig = px.bar(vy, x="Dilim", y="Hata_Orani_%", color="Hata_Orani_%", color_continuous_scale="Reds", title="Vardiya İçi Çeyreğe Göre Hata Oranı")
        st.plotly_chart(fig, use_container_width=True)
        st.dataframe(vy, use_container_width=True, hide_index=True)
        with st.expander("📖 Formül"):
            st.markdown(metrik_aciklamasi("vardiya_hata_orani"))

with sekmeler[5]:
    mtbf = compute_mtbf_mttr(df)
    if mtbf.empty:
        st.info("Arıza kaydı yok.")
    else:
        c1, c2 = st.columns(2)
        with c1:
            st.plotly_chart(px.bar(mtbf.dropna(subset=["MTBF_Saat"]), x="MTBF_Saat", y="TEZGAH", orientation="h", color="MTBF_Saat", color_continuous_scale="RdYlGn", title="MTBF"), use_container_width=True)
        with c2:
            st.plotly_chart(px.bar(mtbf, x="TEZGAH", y="MTTR_Saat", color="MTTR_Saat", color_continuous_scale="Reds", title="MTTR"), use_container_width=True)
        st.dataframe(mtbf, use_container_width=True, hide_index=True)
        with st.expander("📖 Formüller"):
            st.markdown(metrik_aciklamasi("mtbf")); st.markdown("---"); st.markdown(metrik_aciklamasi("mttr"))

st.markdown("---")
st.caption("Üretim Zekası Dashboard'u · Streamlit + Plotly · production.db uyumlu")
