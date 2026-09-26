# -*- coding: utf-8 -*-
"""
3_Operator_Gunlugu.py — "Bugün Ahmet ne yaptı?" sorusuna dakika dakika cevap.
Bir operatör + bir gün seçilince: hangi tezgahta, ne zaman, ne kadar süreyle,
hangi parçayı işlediği / neden durduğu, sıralı biçimde gösterilir.
"""
from __future__ import annotations

import plotly.express as px
import streamlit as st

from common import RENK, veri_yukle_ve_filtrele, compute_zaman_cizelgesi, metrik_aciklamasi

st.set_page_config(page_title="Operatör Günlüğü", layout="wide", page_icon="📋")
st.title("📋 Operatör Günlüğü — Bugün Kim Ne Yaptı?")
st.caption("Bir operatörün seçilen gündeki TÜM hareketini dakika dakika, tezgah değişimleri dahil gösterir.")

df_ham, df, kaynak_notu = veri_yukle_ve_filtrele("opgunluk")
st.caption(kaynak_notu)

if df.empty or "Operator_Kod" not in df.columns:
    st.warning("Seçili filtrelerle eşleşen veri yok veya Operator_Kod kolonu eksik.")
    st.stop()

c1, c2 = st.columns(2)
with c1:
    operatorler = sorted(df["Operator_Kod"].dropna().unique())
    secilen_op = st.selectbox("👷 Operatör", operatorler)
with c2:
    op_tarihler = sorted(df[df["Operator_Kod"] == secilen_op]["TARİH"].dt.date.unique())
    if not op_tarihler:
        st.info("Bu operatöre ait kayıt yok.")
        st.stop()
    secilen_gun = st.selectbox("📅 Gün", op_tarihler, index=len(op_tarihler) - 1)

gunluk = df[(df["Operator_Kod"] == secilen_op) & (df["TARİH"].dt.date == secilen_gun)].copy()
if gunluk.empty:
    st.info("Seçilen operatör/gün için kayıt yok.")
    st.stop()

# ---------------------------------------------------------------- GÜNÜN ÖZETİ
uretim_saat = gunluk.loc[gunluk["Durum"] == "Üretim", "Sure_Saat"].sum()
durus_saat = gunluk.loc[gunluk["Durum"] == "Plansız_Duruş", "Sure_Saat"].sum()
setup_saat = gunluk.loc[gunluk["Durum"] == "Set_Up", "Sure_Saat"].sum()
yemek_saat = gunluk.loc[gunluk["Durum"] == "Yemek_Mola", "Sure_Saat"].sum()
rework_saat = gunluk.loc[gunluk["Durum"] == "Rework", "Sure_Saat"].sum()
toplam_saat = gunluk["Sure_Saat"].sum()
calisma_orani = (uretim_saat / toplam_saat * 100) if toplam_saat > 0 else 0
tezgahlar_bugun = sorted(gunluk["TEZGAH"].dropna().unique()) if "TEZGAH" in gunluk.columns else []
farkli_parca = gunluk["PARÇA NO_ÜRETİM_GİRİLEN"].nunique() if "PARÇA NO_ÜRETİM_GİRİLEN" in gunluk.columns else 0

k1, k2, k3, k4, k5 = st.columns(5)
k1.metric("Üretim Saati", f"{uretim_saat:.2f} sa")
k2.metric("Duruş Saati", f"{durus_saat:.2f} sa")
k3.metric("Çalışma Oranı", f"%{calisma_orani:.1f}")
k4.metric("Tezgah Sayısı", len(tezgahlar_bugun))
k5.metric("Farklı Parça", farkli_parca)

st.markdown(
    f"**{secilen_op}**, {secilen_gun} tarihinde **{', '.join(tezgahlar_bugun) if tezgahlar_bugun else '-'}** "
    f"tezgah(lar)ında çalışmış. Toplam {toplam_saat:.2f} saatlik kayıtlı sürenin "
    f"{uretim_saat:.2f} saati (%{calisma_orani:.1f}) fiili üretim, {durus_saat:.2f} saati plansız duruş, "
    f"{setup_saat:.2f} saati set-up, {yemek_saat:.2f} saati yemek/mola, {rework_saat:.2f} saati rework olarak geçmiş."
)

st.markdown(f"`Çalışma Oranı = Üretim_Saat / Toplam_Saat × 100 = {uretim_saat:.2f} / {toplam_saat:.2f} × 100 = %{calisma_orani:.1f}`")

st.markdown("---")

# ---------------------------------------------------------------- ZAMAN ÇİZELGESİ (varsa)
gerekli = ["TEZGAH", "TARİH", "VARDİYA", "BAŞLANGIÇ_SAAT", "Sure_Saat"]
if all(c in gunluk.columns for c in gerekli):
    zaman_df = compute_zaman_cizelgesi(gunluk)
    if not zaman_df.empty:
        st.subheader("🕐 Günün Zaman Çizelgesi")
        fig = px.timeline(
            zaman_df, x_start="Baslangic_Zaman", x_end="Bitis_Zaman", y="TEZGAH", color="Durum",
            color_discrete_map=RENK,
            hover_data={c: True for c in ["Durus_Kategorisi", "Durus_Sebebi_Ham", "KOD & OPE.", "Sure_Saat"] if c in zaman_df.columns},
            title=f"{secilen_op} — {secilen_gun}",
        )
        fig.update_yaxes(autorange="reversed")
        st.plotly_chart(fig, use_container_width=True)
        gunluk_sirali = zaman_df.sort_values("Baslangic_Zaman")
    else:
        gunluk_sirali = gunluk.sort_values("BAŞLANGIÇ_SAAT") if "BAŞLANGIÇ_SAAT" in gunluk.columns else gunluk
else:
    gunluk_sirali = gunluk.sort_values("BAŞLANGIÇ_SAAT") if "BAŞLANGIÇ_SAAT" in gunluk.columns else gunluk

st.markdown("---")

# ---------------------------------------------------------------- KRONOLOJİK DÖKÜM
st.subheader("📋 Dakika Dakika Döküm")

# tezgah değişimlerini işaretle (aynı satırda üst üste aynı tezgah mı)
gunluk_sirali = gunluk_sirali.copy()
if "TEZGAH" in gunluk_sirali.columns:
    gunluk_sirali["Tezgah_Degisti_mi"] = gunluk_sirali["TEZGAH"].ne(gunluk_sirali["TEZGAH"].shift())

gosterilecek = [c for c in [
    "Baslangic_Zaman", "Bitis_Zaman", "BAŞLANGIÇ_SAAT", "TEZGAH", "Durum", "Durus_Kategorisi",
    "Durus_Sebebi_Ham", "KOD & OPE.", "PARÇA NO_ÜRETİM_GİRİLEN", "Sure_Saat", "ÜRETİM_C/T", "Sapma_Yuzdesi",
] if c in gunluk_sirali.columns]

tablo = gunluk_sirali[gosterilecek].copy()
tablo["Sure_Dakika"] = (tablo["Sure_Saat"] * 60).round(1)

st.dataframe(tablo, use_container_width=True, hide_index=True, height=min(600, 40 + 35 * len(tablo)))

n_tezgah_degisimi = int(gunluk_sirali["Tezgah_Degisti_mi"].sum()) - 1 if "Tezgah_Degisti_mi" in gunluk_sirali.columns else 0
st.caption(f"Gün içinde {max(n_tezgah_degisimi, 0)} kez tezgah değiştirmiş (ilk kayıt hariç).")

if durus_saat > 0 and "Durus_Kategorisi" in gunluk.columns:
    st.markdown("**Bugünkü duruş sebepleri:**")
    st.dataframe(
        gunluk[gunluk["Durum"] == "Plansız_Duruş"].groupby("Durus_Kategorisi")["Sure_Saat"]
        .agg(Toplam_Saat="sum", Adet="count").reset_index().sort_values("Toplam_Saat", ascending=False),
        use_container_width=True, hide_index=True,
    )

with st.expander("📖 İlgili formüller"):
    st.markdown(metrik_aciklamasi("calisma_orani_operator"))
    st.markdown("---")
    st.markdown(metrik_aciklamasi("ct_bazli_sapma"))
