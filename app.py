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
    compute_ct_guvenilirlik, compute_onerilen_ct, compute_ct_cozum_etkisi,
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
    "Vardiya Yorgunluk", "MTBF & MTTR", "⚠️ CT Güvenilirlik",
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

with sekmeler[6]:
    st.subheader("⚠️ Referans C/T (Cycle Time) Ne Kadar Güvenilir?")
    st.caption(
        "Tezgah/operatör performansını yorumlamadan ÖNCE bu sekmeyi kontrol edin — "
        "referans C/T güncel değilse, Performans_Oranı ve Gerçek_OEE değerleri "
        "yanıltıcı (sistematik şişirilmiş) çıkabilir."
    )
    ct = compute_ct_guvenilirlik(df)
    if not ct.get("gecerli"):
        st.info("Bu filtrede CT güvenilirlik analizi için yeterli veri yok.")
    else:
        c1, c2, c3 = st.columns(3)
        c1.metric("Sistemsel Kayma Puanı", f"{ct['sistemsel_kayma_puan']:+.1f}" if ct['sistemsel_kayma_puan'] is not None else "—")
        c2.metric("CT'den Hızlı Görünen Operatör %", f"%{ct['negatif_ct_sapma_orani']:.1f}" if ct['negatif_ct_sapma_orani'] is not None else "—")
        c3.metric("Korelasyon (CT sapma ↔ Emsal sapma)", f"{ct['korelasyon']:.2f}" if ct['korelasyon'] is not None else "—")

        negatif_oran = ct.get("negatif_ct_sapma_orani")
        if negatif_oran is not None and negatif_oran >= 80:
            st.error(
                f"🚨 Operatörlerin **%{negatif_oran:.0f}'i** referans C/T'den daha hızlı görünüyor. "
                "Bu, operatörlerin gerçekten çok hızlı olduğu anlamına gelmekten çok, "
                "**ÜRETİM_C/T referansının güncellenmesi gerektiğine** işaret eder. "
                "`Performans_Oranı` ve `Gerçek_OEE`'yi mutlak değeriyle değil, "
                "TEZGAHLAR/OPERATÖRLER ARASI KIYASLAMA için kullanın."
            )
        elif negatif_oran is not None and negatif_oran <= 20:
            st.warning(
                f"Operatörlerin sadece %{negatif_oran:.0f}'i C/T'den hızlı — referans C/T "
                "muhtemelen GERÇEKÇİ OLMAYAN KADAR SIKI (çok iddialı) olabilir."
            )
        else:
            st.success("Sistemsel kayma sınırlı görünüyor — referans C/T makul bir denge içinde.")

        with st.expander("📖 Bu puanlar nasıl hesaplandı?"):
            st.markdown(
                "**Sistemsel Kayma Puanı** = ORT(CT_Bazli_Sapma_%) − ORT(Emsal_Bazli_Sapma_%) "
                "[operatör bazında]. Sıfıra yakınsa C/T ile emsal-gerçeklik uyumlu demektir; "
                "büyük ve negatifse C/T gerçek hızdan YAVAŞ (gevşek) kalmış demektir.\n\n"
                "**CT'den Hızlı Görünen Operatör %** = operatörlerin CT_Bazli_Sapma_Yuzde'si "
                "negatif olanların oranı. Sağlıklı/güncel bir referansta ~%50 civarı beklenir; "
                "aşırı yüksek veya aşırı düşük oranlar referansın güncelliğini sorgulatır."
            )

        agnostik = ct.get("tezgah_agnostik_kodlar")
        if agnostik is not None and not agnostik.empty:
            st.markdown("---")
            st.markdown(
                f"**🔍 Tezgah-Agnostik {len(agnostik)} Kod Bulundu** — aynı 'KOD & OPE.' "
                "kodu için C/T sabit ama gerçek medyan süre tezgaha göre ≥%15 değişiyor. "
                "Yani bu kodlarda C/T, hangi tezgahın daha hızlı/yavaş olduğunu hiç hesaba "
                "katmıyor — operatörü değil, atandığı tezgahı ölçmüş oluyorsunuz demektir."
            )
            st.dataframe(agnostik, use_container_width=True, hide_index=True)

        # ---------------------------------------------------------- ÇÖZÜM
        st.markdown("---")
        st.subheader("✅ Çözüm: Veri-Türetilmiş 'Önerilen C/T'")
        st.caption(
            "Katalog C/T güncel değilse, verinin kendisinden (tüm operatörlerin gerçek "
            "medyan süresinden) türetilmiş bir referans kullanılabilir — zaten Emsal "
            "Karşılaştırması'nda kullanılan aynı güvenilir yöntem."
        )
        onerilen = compute_onerilen_ct(df)
        etki = compute_ct_cozum_etkisi(df)
        if onerilen.empty or not etki.get("gecerli"):
            st.info("Önerilen C/T hesaplamak için yeterli tekrarlı (n≥3) kod bulunamadı.")
        else:
            e1, e2, e3 = st.columns(3)
            e1.metric(
                "CT'den Hızlı Görünen % (ÖNCE → SONRA)",
                f"%{etki['yeni_negatif_ct_sapma_orani']:.1f}",
                delta=f"{etki['yeni_negatif_ct_sapma_orani'] - (negatif_oran or 0):+.1f} puan (sağlıklıya {'yaklaştı' if abs(etki['yeni_negatif_ct_sapma_orani']-50) < abs((negatif_oran or 0)-50) else 'uzaklaştı'})",
                delta_color="normal",
            )
            e2.metric("Güncelleme Önerilen Kod Sayısı", f"{int(onerilen['Guncelleme_Onerisi'].sum())} / {len(onerilen)}")
            e3.metric("Kapsanan İşlem", f"{etki['kapsanan_islem_sayisi']:,} / {int(df.loc[df['Durum']=='Üretim'].shape[0]):,}")

            if negatif_oran is not None:
                st.success(
                    f"Katalog C/T yerine **Önerilen C/T** kullanılsaydı, 'operatörlerin CT'den "
                    f"hızlı görünme oranı' **%{negatif_oran:.1f} → %{etki['yeni_negatif_ct_sapma_orani']:.1f}**'e "
                    f"inerdi (sağlıklı denge %50 civarıdır) — bu, referans C/T'nin gerçekten "
                    f"GÜNCEL OLMADIĞININ güçlü bir kanıtıdır."
                )

            st.markdown("**Güncellenmesi önerilen kodlar** (|fark| ≥ %15, en büyük farktan küçüğe):")
            st.dataframe(
                onerilen[onerilen["Guncelleme_Onerisi"]],
                use_container_width=True, hide_index=True,
            )
            with st.expander("📖 Bu tablo nasıl hesaplandı? / Tüm kodlar (öneri eşiği altındakiler dahil)"):
                st.markdown(metrik_aciklamasi("onerilen_ct"))
                st.dataframe(onerilen, use_container_width=True, hide_index=True)

st.markdown("---")
st.caption("Üretim Zekası Dashboard'u · Streamlit + Plotly · production.db uyumlu")
