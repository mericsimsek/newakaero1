# -*- coding: utf-8 -*-
"""
1_Zaman_Cizelgesi.py — "Şu gün şu tezgah niye durmuş, ne kadar durmuş, kim
varmış, ne işlemiş" sorusuna PAT DİYE cevap veren sayfa.

İçerik:
  - Seçilen gün(ler) + tezgah(lar) için gerçek zamanlı (chained) Gantt çizelgesi
  - Her tezgah için: çalışma oranı, duruş oranı, duruş sayısı, ortalama/en uzun
    duruş, en sık duruş sebebi — hepsi formülüyle birlikte
  - Olay olay (satır satır) kronolojik tablo: saat, kim, ne, ne kadar, neden
  - Duruş sebeplerinin kendi içinde dağılımı (o gün/tezgah için mini pareto)
"""
from __future__ import annotations

import pandas as pd
import plotly.express as px
import streamlit as st

from common import (
    RENK, veri_yukle_ve_filtrele, compute_zaman_cizelgesi, metrik_aciklamasi,
)

st.set_page_config(page_title="Zaman Çizelgesi", layout="wide", page_icon="🕐")
st.title("🕐 Zaman Çizelgesi — Saat Saat, Olay Olay")
st.caption(
    "Bir tezgahın bir gün içinde ne zaman çalıştığını, ne zaman durduğunu, "
    "kimin çalıştığını ve her duruşun sebebini tek ekranda, dakikası dakikasına gösterir."
)

df_ham, df, kaynak_notu = veri_yukle_ve_filtrele("gantt")
st.caption(kaynak_notu)

if df.empty:
    st.warning("Seçili filtrelerle eşleşen veri yok.")
    st.stop()

gerekli_kolonlar = ["TEZGAH", "TARİH", "VARDİYA", "BAŞLANGIÇ_SAAT", "Sure_Saat", "Durum"]
eksikler = [c for c in gerekli_kolonlar if c not in df.columns]
if eksikler:
    st.error(
        f"Bu analiz için şu kolonlar gerekli ama veride yok: **{', '.join(eksikler)}**. "
        "BAŞLANGIÇ_SAAT kolonu olmadan gerçek saat bilgisi üretilemez."
    )
    st.stop()

# ---------------------------------------------------------------- SEÇİM
c1, c2, c3 = st.columns([1, 1, 2])
with c1:
    tarihler = sorted(df["TARİH"].dt.date.unique())
    secilen_gun = st.selectbox("📅 Gün", tarihler, index=len(tarihler) - 1)
with c2:
    tezgah_opts = sorted(df["TEZGAH"].dropna().unique())
    secim_modu = st.radio("Tezgah kapsamı", ["Tüm tezgahlar", "Tek tezgah"], horizontal=True)
with c3:
    if secim_modu == "Tek tezgah":
        secilen_tezgah = st.selectbox("Tezgah seç", tezgah_opts)
    else:
        secilen_tezgah = None

gunluk = df[df["TARİH"].dt.date == secilen_gun].copy()
if secilen_tezgah:
    gunluk = gunluk[gunluk["TEZGAH"] == secilen_tezgah]

if gunluk.empty:
    st.info("Seçilen gün/tezgah için kayıt yok.")
    st.stop()

# ---------------------------------------------------------------- ZİNCİRLEME ZAMAN
zaman_df = compute_zaman_cizelgesi(gunluk)
if zaman_df.empty:
    st.warning("Zaman çizelgesi hesaplanamadı (BAŞLANGIÇ_SAAT ayrıştırılamıyor olabilir).")
    st.stop()

with st.expander("ℹ️ Bu saatler nasıl hesaplanıyor? (yöntem)"):
    st.markdown(
        "Her **(Tezgah, Tarih, Vardiya)** grubundaki **ilk kayıt**, o vardiyanın "
        "`BAŞLANGIÇ_SAAT` değerine sabitlenir. Sonraki her kayıt, **bir önceki "
        "kaydın bitiş saatinden** başlar (`Bitiş = Başlangıç + Sure_Saat`). "
        "Bu yöntem sayesinde gece yarısını geçen vardiyalarda saat metnini yanlış "
        "yorumlama riski ortadan kalkar.\n\n"
        "⚠️ **Sınırlama:** Eğer bir olay sisteme hiç girilmemişse (loglanmamış boşluk), "
        "o noktadan sonraki saatler gerçek duvar saatinden kayabilir — sıralama ve "
        "göreceli süreler doğru kalır, sadece mutlak saat (örn. 'tam 14:32'de') yaklaşık olur."
    )

# ---------------------------------------------------------------- GANTT
hover_kolonlar = {c: True for c in ["Operator_Kod", "Durus_Kategorisi", "Durus_Sebebi_Ham", "KOD & OPE.", "Sure_Saat"] if c in zaman_df.columns}
fig = px.timeline(
    zaman_df, x_start="Baslangic_Zaman", x_end="Bitis_Zaman", y="TEZGAH", color="Durum",
    color_discrete_map=RENK, hover_data=hover_kolonlar,
    title=f"{secilen_gun} — {'Tüm Tezgahlar' if not secilen_tezgah else secilen_tezgah}",
)
fig.update_yaxes(autorange="reversed")
fig.update_layout(height=max(300, 60 * zaman_df["TEZGAH"].nunique()))
st.plotly_chart(fig, use_container_width=True)

st.markdown(
    "**Nasıl okunur?** Her renkli blok bir olayı temsil eder. Üzerine gelince "
    "operatör, duruş kategorisi/sebebi, işlenen parça/operasyon kodu ve süre görünür. "
    "Renk kodları: 🟢 Üretim · 🔴 Plansız Duruş · 🟡 Set-up · 🟤 Yemek/Mola · 🟠 Rework."
)

st.markdown("---")

# ---------------------------------------------------------------- TEZGAH BAZLI İSTATİSTİKLER
st.subheader("📊 Tezgah Bazlı Detaylı İstatistikler")

for tezgah in sorted(zaman_df["TEZGAH"].unique()):
    tg = zaman_df[zaman_df["TEZGAH"] == tezgah]
    uretim_saat = tg.loc[tg["Durum"] == "Üretim", "Sure_Saat"].sum()
    durus_saat = tg.loc[tg["Durum"] == "Plansız_Duruş", "Sure_Saat"].sum()
    setup_saat = tg.loc[tg["Durum"] == "Set_Up", "Sure_Saat"].sum()
    yemek_saat = tg.loc[tg["Durum"] == "Yemek_Mola", "Sure_Saat"].sum()
    rework_saat = tg.loc[tg["Durum"] == "Rework", "Sure_Saat"].sum()
    toplam_saat = tg["Sure_Saat"].sum()
    durus_sayisi = len(tg[tg["Durum"] == "Plansız_Duruş"])
    calisan_operatorler = ", ".join(sorted(tg["Operator_Kod"].dropna().unique())) if "Operator_Kod" in tg.columns else "-"

    with st.expander(f"🏭 **{tezgah}** — Üretim {uretim_saat:.1f}sa · Duruş {durus_saat:.1f}sa · {durus_sayisi} duruş", expanded=(secilen_tezgah is not None)):
        cc1, cc2, cc3, cc4 = st.columns(4)
        calisma_orani = (uretim_saat / toplam_saat * 100) if toplam_saat > 0 else 0
        cc1.metric("Çalışma Oranı", f"%{calisma_orani:.1f}")
        cc2.metric("Duruş Sayısı", durus_sayisi)
        ort_durus = (durus_saat / durus_sayisi * 60) if durus_sayisi > 0 else 0
        cc3.metric("Ort. Duruş Süresi", f"{ort_durus:.0f} dk")
        en_uzun_durus = tg.loc[tg["Durum"] == "Plansız_Duruş", "Sure_Saat"].max() if durus_sayisi > 0 else 0
        cc4.metric("En Uzun Duruş", f"{en_uzun_durus * 60:.0f} dk" if durus_sayisi > 0 else "—")

        st.caption(f"👷 Çalışan operatör(ler): {calisan_operatorler}")
        st.caption(
            f"⏱️ Süre dağılımı: Üretim {uretim_saat:.2f}sa · Plansız Duruş {durus_saat:.2f}sa · "
            f"Set-up {setup_saat:.2f}sa · Yemek/Mola {yemek_saat:.2f}sa · Rework {rework_saat:.2f}sa "
            f"(Toplam: {toplam_saat:.2f}sa)"
        )

        if durus_sayisi > 0 and "Durus_Kategorisi" in tg.columns:
            mini_pareto = (
                tg[tg["Durum"] == "Plansız_Duruş"].groupby("Durus_Kategorisi")["Sure_Saat"]
                .agg(Toplam_Saat="sum", Adet="count").reset_index()
                .sort_values("Toplam_Saat", ascending=False)
            )
            st.markdown("**Bu tezgahın bugünkü duruş sebepleri:**")
            st.dataframe(mini_pareto, use_container_width=True, hide_index=True)

        st.markdown(f"`Çalışma Oranı = Üretim_Saat / Toplam_Saat × 100 = {uretim_saat:.2f} / {toplam_saat:.2f} × 100 = %{calisma_orani:.1f}`")

st.markdown("---")

# ---------------------------------------------------------------- KRONOLOJİK OLAY TABLOSU
st.subheader("📋 Kronolojik Olay Dökümü — Her Şey Tek Tek")
st.caption("Seçilen gün/tezgah kapsamındaki her olay, gerçekleşme sırasına göre.")

gosterilecek = [c for c in [
    "TEZGAH", "Baslangic_Zaman", "Bitis_Zaman", "Sure_Saat", "Operator_Kod", "Durum",
    "Durus_Kategorisi", "Durus_Sebebi_Ham", "KOD & OPE.", "PARÇA NO_ÜRETİM_GİRİLEN", "ÜRETİM_C/T",
] if c in zaman_df.columns]

tablo = zaman_df[gosterilecek].sort_values(["TEZGAH", "Baslangic_Zaman"]).copy()
tablo["Sure_Dakika"] = (tablo["Sure_Saat"] * 60).round(1)

st.dataframe(
    tablo.style.format({"Sure_Saat": "{:.2f}", "Sure_Dakika": "{:.0f}"}),
    use_container_width=True, hide_index=True, height=min(600, 40 + 35 * len(tablo)),
)
st.caption(f"Toplam {len(tablo):,} olay · {tablo['Sure_Saat'].sum():,.1f} saat")

with st.expander("📖 İlgili formüller"):
    st.markdown(metrik_aciklamasi("kullanilabilirlik"))
    st.markdown("---")
    st.markdown(metrik_aciklamasi("calisma_orani_gunluk"))
