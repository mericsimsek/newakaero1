# -*- coding: utf-8 -*-
"""
app3.py — Ana Sayfa (Sunum Odaklı Optimize Edilmiş Dashboard)
Patron/Yönetim sunumu için detaylandırılmış, bol grafikli ve şeffaflığı artırılmış versiyon.
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

OPERATOR_SOZLUGU = {
  "Op_001": "AŞKIN SAKALLI", "Op_002": "BERAT TUNÇ", "Op_003": "CİHAN SARIGÜL", "Op_004": "EMRE TOPRAK", 
  "Op_005": "EREN TAMER YELEKÇİ", "Op_006": "ERTUĞRUL ÖZDEMİR", "Op_007": "HAMZA KARAASLAN", "Op_008": "HÜSEYİN YENİCE", 
  "Op_009": "HÜSEYİN YILMAZ", "Op_010": "İLKER SEZDİ", "Op_011": "MEHMET ALİ ÇOLAK", "Op_012": "MEHMET EMRE KARAKAŞ", 
  "Op_013": "MERT KUŞCU", "Op_014": "MESUT ARDIÇ", "Op_015": "MUHAMMED KORKMAZ", "Op_016": "NAZİF GÜÇLÜDAĞ", 
  "Op_017": "NURULLAH ZENGİN", "Op_018": "ÖZCAN ÇELİK", "Op_019": "TANER GÜÇLÜDAĞ", "Op_020": "UMUT ÖZKAN", "Op_021": "YAŞAR ŞENOL"
}

st.set_page_config(page_title="Üretim Zekası Dashboard", layout="wide", page_icon="🏭")
st.title("🏭 Üretim Zekası ve Karar Destek Dashboard'u")

df_ham, df, kaynak_notu = veri_yukle_ve_filtrele("home")
st.caption(kaynak_notu)

if df.empty:
    st.warning("Seçili filtrelerle eşleşen veri yok.")
    st.stop()

if "Operator_Kod" in df.columns:
    df["Operator_Kod"] = df["Operator_Kod"].replace(OPERATOR_SOZLUGU)

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
st.markdown("---")

sekmeler = st.tabs([
    "Tezgah Özeti", 
    "Duruş Pareto ve Analizi", 
    "Operatör Karnesi", 
    "Kronolojik Üretim Dökümü",  
    "⚠️ CT Güvenilirlik", 
    "📐 Metrikler ve Formüller"  
])

# --- SEKME 1: TEZGAH ÖZETİ ---
with sekmeler[0]:
    tezgah_ozet = compute_tezgah_oee(df)
    if tezgah_ozet.empty:
        st.info("Veri yok.")
    else:
        # Önce Tabloyu ve Yüzdeleri Hazırlayalım (Grafiğin doğru sayılarla çalışması için)
        tezgah_ozet_gosterim = tezgah_ozet.rename(columns={
            "Uretim_Saat": "Üretim (Sa)", "Plansiz_Durus_Saat": "Plansız Duruş (Sa)", "Set_Up_Saat": "Set-Up (Sa)",
            "Yemek_Saat": "Yemek Mola (Sa)", "Rework_Saat": "Rework (Sa)", "Kayit_Sayisi": "Kayıt Sayısı",
            "Planlanan_Sure": "Planlanan Süre", "Kullanilabilirlik": "Kullanılabilirlik (%)", "Kalite_Orani": "Kalite Oranı (%)",
            "OEE_Yaklasik": "OEE Yaklaşık (%)", "Performans_Orani": "Performans Oranı (%)", "Gercek_OEE": "Gerçek OEE (%)", "En_Buyuk_Kayip_Kategorileri": "En Büyük Kayıp Kategorileri"
        })
        
        # Yüzdeleri 100 ile çarpıp daha okunabilir göster
        for oran in ["Kullanılabilirlik (%)", "Kalite Oranı (%)", "OEE Yaklaşık (%)", "Performans Oranı (%)", "Gerçek OEE (%)"]:
            if oran in tezgah_ozet_gosterim.columns:
                tezgah_ozet_gosterim[oran] = (tezgah_ozet_gosterim[oran] * 100).round(1)

        c1, c2 = st.columns([1, 2])
        # 1. Fabrika Geneli Durum Dağılımı (Pasta Grafik)
        with c1:
            durum_toplamlari = df.groupby("Durum")["Sure_Saat"].sum().reset_index()
            renk_haritasi = {k: RENK.get(k, "#BDBDBD") for k in durum_toplamlari["Durum"]}
            fig_pie = px.pie(durum_toplamlari, values='Sure_Saat', names='Durum', title='Fabrika Geneli Zaman Dağılımı', color='Durum', color_discrete_map=renk_haritasi, hole=0.4, height=450)
            fig_pie.update_traces(textposition='inside', textinfo='percent+label', textfont_size=14)
            st.plotly_chart(fig_pie, use_container_width=True)

        # 2. Tezgah Başına Gerçek OEE (Büyük Yatay Bar Grafik)
        with c2:
            if "Gerçek OEE (%)" in tezgah_ozet_gosterim.columns:
                tezgah_sirali = tezgah_ozet_gosterim.sort_values("Gerçek OEE (%)", ascending=True)
                max_x = max(100, tezgah_sirali["Gerçek OEE (%)"].max() + 10) # 150 ise grafiği 160'a kadar uzatır
                fig_oee = px.bar(tezgah_sirali, x="Gerçek OEE (%)", y="TEZGAH", orientation="h",
                                 title="Tezgah OEE Skorları (Hedef: %85)", 
                                 text_auto='.1f', color="Gerçek OEE (%)", 
                                 color_continuous_scale="RdYlGn", range_color=[50, 100], height=450)
                
                fig_oee.add_vline(x=85, line_dash="dash", line_color="red", annotation_text="Dünya Standardı (%85) ", annotation_font_size=12)
                fig_oee.update_layout(xaxis_title="OEE Skoru (%)", yaxis_title="Tezgahlar", xaxis=dict(range=[0, max_x]), font=dict(size=14))
                st.plotly_chart(fig_oee, use_container_width=True)

        st.markdown("##### Tezgah Başına Detaylı Tablo")
        st.dataframe(tezgah_ozet_gosterim, use_container_width=True, hide_index=True)


# --- SEKME 2: DURUŞ PARETO VE DETAYLI ANALİZİ ---
with sekmeler[1]:
    pareto = compute_pareto(df)
    if pareto.empty:
        st.info("Plansız duruş kaydı yok.")
    else:
        fig = go.Figure()
        fig.add_bar(x=pareto["Durus_Kategorisi"], y=pareto["Toplam_Saat"], marker_color="#C62828", name="Toplam Saat")
        fig.add_trace(go.Scatter(x=pareto["Durus_Kategorisi"], y=pareto["Kumulatif_%"], yaxis="y2", mode="lines+markers", name="Kümülatif %", line=dict(color="#1565C0")))
        fig.update_layout(title="Duruş Sebepleri Pareto", yaxis2=dict(overlaying="y", side="right", range=[0, 105]), height=500, font=dict(size=14))
        st.plotly_chart(fig, use_container_width=True)
        
        c1, c2 = st.columns([1, 2])
        with c1:
            st.subheader("Duruş Özeti")
            pareto_gosterim = pareto.rename(columns={"Durus_Kategorisi": "Duruş Kategorisi", "Toplam_Saat": "Toplam Saat", "Kumulatif_%": "Kümülatif %"})
            st.dataframe(pareto_gosterim, use_container_width=True, hide_index=True)
        with c2:
            st.subheader("Olay Bazlı Detaylı Duruş/Rework Kayıtları")
            st.caption("Kim, Hangi Parçada, Ne Kadar Süre Durdu?")
            durus_detay_df = df[df["Durum"].isin(["Plansız_Duruş", "Rework"])].copy()
            if not durus_detay_df.empty:
                durus_detay_df = durus_detay_df.sort_values(by="Sure_Saat", ascending=False)
                durus_detay_gosterim = durus_detay_df[[
                    "TARİH", "TEZGAH", "Operator_Kod", "PARÇA NO_ÜRETİM_GİRİLEN", "Durum", "Durus_Kategorisi", "Durus_Sebebi_Ham", "Sure_Saat"
                ]].rename(columns={"TARİH": "Tarih", "TEZGAH": "Tezgah", "Operator_Kod": "Operatör", "PARÇA NO_ÜRETİM_GİRİLEN": "Parça No", "Durus_Kategorisi": "Kategori", "Durus_Sebebi_Ham": "Açıklama (Sebep)", "Sure_Saat": "Süre (Saat)"})
                if "Tarih" in durus_detay_gosterim.columns:
                    durus_detay_gosterim["Tarih"] = pd.to_datetime(durus_detay_gosterim["Tarih"]).dt.date
                durus_detay_gosterim["Süre (Saat)"] = durus_detay_gosterim["Süre (Saat)"].round(2)
                st.dataframe(durus_detay_gosterim.fillna("-"), use_container_width=True, hide_index=True)
            else:
                st.info("Detaylı duruş kaydı bulunamadı.")


# --- SEKME 3: OPERATÖR KARNESİ ---
with sekmeler[2]:
    karne = compute_operator_karne(df)
    if karne.empty:
        st.info("Yeterli veri yok.")
    else:
        if "Operator_Kod" in karne.columns:
            karne["Operator_Kod"] = karne["Operator_Kod"].replace(OPERATOR_SOZLUGU)
            
        # Hatayı çözdüğümüz isim değiştirme alanı
        karne_gosterim = karne.rename(columns={
            "Operator_Kod": "Operatör", "Toplam_Saat": "Toplam Saat", "Islem_Sayisi": "İşlem Sayısı", "Islem_Saat_Hizi": "İşlem / Saat Hızı",
            "CT_Bazli_Sapma_Yuzde": "CT Sapması (%)", "CT_Dusuk_Guven_Satir_Sayisi": "Düşük Güven (Satır)", "CT_Dusuk_Guven_Yuzde": "Düşük Güven (%)", "Emsal_Bazli_Sapma_Yuzde": "Emsal Sapması (%)"
        })

        if "Emsal Sapması (%)" in karne_gosterim.columns:
            karne_sirali = karne_gosterim.sort_values("Emsal Sapması (%)", ascending=False)
            fig_op = px.bar(karne_sirali, x="Emsal Sapması (%)", y="Operatör", orientation="h",
                         title="Operatör Hız Kıyaslaması (Emsallerine Göre)",
                         text_auto='.1f', color="Emsal Sapması (%)",
                         color_continuous_scale="RdYlGn_r", height=550) 
            
            fig_op.add_vline(x=0, line_dash="solid", line_color="black", annotation_text="Ortalama Hız ", annotation_font_size=12)
            fig_op.update_layout(xaxis_title="Emsal Sapması (%) [Negatif Değerler Ortalamadan Hızlıdır]", yaxis_title="Operatörler", font=dict(size=14))
            st.plotly_chart(fig_op, use_container_width=True)

        for col in ["Toplam Saat", "İşlem / Saat Hızı", "CT Sapması (%)", "Emsal Sapması (%)"]:
            if col in karne_gosterim.columns:
                karne_gosterim[col] = karne_gosterim[col].round(2)
        
        st.dataframe(
            karne_gosterim,
            use_container_width=True,
            hide_index=True,
            column_config={
                "CT Sapması (%)": st.column_config.NumberColumn(format="%.1f%%"),
                "Emsal Sapması (%)": st.column_config.NumberColumn(format="%.1f%%")
            }
        )


# --- SEKME 4: KRONOLOJİK ÜRETİM DÖKÜMÜ ---
with sekmeler[3]:
    st.subheader("📅 Detaylı Kronolojik Üretim Dökümü")
    st.caption("Aşağıdaki grafik ve tablo, fabrikadaki hareketlerin zaman içindeki dizilimini sunar.")
    
    df_krono = df.copy()
    
    # Görsel Zaman Eksenli Grafik (Büyük Yığılı Bar Grafiği)
    if "TARİH" in df_krono.columns and "Sure_Saat" in df_krono.columns:
        gunluk_durum = df_krono.groupby(["TARİH", "Durum"])["Sure_Saat"].sum().reset_index()
        fig_zaman = px.bar(gunluk_durum, x="TARİH", y="Sure_Saat", color="Durum",
                           title="Günlük Fabrika Çalışma ve Duruş Trendi",
                           text_auto='.1f',
                           color_discrete_map={k: RENK.get(k, "#BDBDBD") for k in gunluk_durum["Durum"].unique()},
                           barmode="stack", height=500)
                           
        fig_zaman.update_layout(xaxis_title="Tarih", yaxis_title="Toplam Üretim ve Duruş Süresi (Saat)", font=dict(size=14))
        st.plotly_chart(fig_zaman, use_container_width=True)

    # Detaylı Tablo
    if "Baslangic_Zaman" in df_krono.columns:
        df_krono = df_krono.sort_values("Baslangic_Zaman")
    else:
        df_krono = df_krono.sort_values(["TARİH", "BAŞLANGIÇ_SAAT"])
    if "TARİH" in df_krono.columns: 
        df_krono["TARİH"] = pd.to_datetime(df_krono["TARİH"]).dt.date
        
    krono_sutunlar = {
        "TARİH": "Tarih", "VARDİYA": "Vardiya", "BAŞLANGIÇ_SAAT": "Başlangıç Saati", "BİTİŞ_SAAT": "Bitiş Saati", "TEZGAH": "Tezgah", "Operator_Kod": "Operatör",
        "PARÇA NO_ÜRETİM_GİRİLEN": "Parça No", "OPERASYON_NO": "Operasyon No", "Durum": "Durum", "Durus_Kategorisi": "Duruş Kategorisi", "Durus_Sebebi_Ham": "Duruş Sebebi",
        "Sure_Saat": "Gerçekleşen Süre (Saat)", "ÜRETİM_C/T": "Hedef C/T (Saat)", "Sapma_Saat": "Sapma (Saat)", "Sapma_Yuzdesi": "Sapma (%)"
    }
    df_gosterim = df_krono[[c for c in krono_sutunlar.keys() if c in df_krono.columns]].rename(columns=krono_sutunlar)
    
    for col in ["Gerçekleşen Süre (Saat)", "Hedef C/T (Saat)", "Sapma (Saat)", "Sapma (%)"]:
        if col in df_gosterim.columns:
            df_gosterim[col] = pd.to_numeric(df_gosterim[col], errors='coerce').round(2)
            
    st.dataframe(df_gosterim.fillna("-"), use_container_width=True, hide_index=True)


# --- SEKME 5: CT GÜVENİLİRLİK ---
with sekmeler[4]:
    st.subheader("⚠️ Katalog C/T (Cycle Time) Analizi ve Kanıtlar")
    ct = compute_ct_guvenilirlik(df)
    if not ct.get("gecerli"):
        st.info("Bu filtrede CT güvenilirlik analizi için yeterli veri yok.")
    else:
        c1, c2 = st.columns(2)
        c1.metric("Sistemsel Kayma Puanı (Negatif = Gevşek Hedef)", f"{ct['sistemsel_kayma_puan']:+.1f}" if ct['sistemsel_kayma_puan'] is not None else "—")
        c2.metric("CT'den Hızlı Görünen Operatör Oranı", f"%{ct['negatif_ct_sapma_orani']:.1f}" if ct['negatif_ct_sapma_orani'] is not None else "—")
        st.warning("🚨 **Mevcut üretim kataloglarındaki hedef süreler (C/T) güncel değildir.** Operatörlerimizin neredeyse tamamı hedeflerden hızlı görünmektedir. Bu durum operatör hızından ziyade, kağıt üzerindeki hedeflerin gevşekliğini kanıtlar.")
        st.markdown("---")
        st.subheader("✅ Veri Odaklı Çözüm: Önerilen C/T")
        st.info("Aşağıdaki tablo, hatalı katalog süreleri yerine; ustalarımızın **fiili üretim medyanlarına** göre algoritmamız tarafından hesaplanmış **GERÇEKÇİ** hedef süreleridir. Bu tablodaki öneriler dikkate alınarak kataloglar güncellenmelidir.")
        onerilen = compute_onerilen_ct(df)
        if not onerilen.empty:
            gosterilecek_oneriler = onerilen[onerilen["Guncelleme_Onerisi"]] if "Guncelleme_Onerisi" in onerilen.columns else onerilen
            st.dataframe(gosterilecek_oneriler, use_container_width=True, hide_index=True)


# --- SEKME 6: METRİKLER VE FORMÜLLER ---
with sekmeler[5]:
    st.subheader("📐 Hesaplama Mantığı, Formüller ve Şeffaflık")
    st.markdown("Dashboard üzerinde gördüğünüz tüm metrikler, sistemdeki hiçbir veri tahmin edilmeden **matematiksel ve istatistiksel** yöntemlerle hesaplanmıştır. Neden türetildikleri ve nasıl hesaplandıkları aşağıda açıklanmıştır.")
    
    st.markdown("### 📊 Ana OEE ve Verimlilik Formülleri")
    with st.expander("1️⃣ Kullanılabilirlik (Availability) Oranı"):
        st.markdown("""**Mantık:** Makinenin planlanan çalışma süresinin ne kadarını üreterek geçirdiğini gösterir. 
**Kural:** `Set_Up` ve `Yemek_Mola` "Planlı Duruş" kabul edildiği için paydaya eklenmez.
**Formül:** $$\\text{Kullanılabilirlik} = \\frac{\\text{Üretim} + \\text{Rework}}{\\text{Üretim} + \\text{Plansız Duruş} + \\text{Rework}} \\times 100$$""")

    with st.expander("2️⃣ Kalite Oranı (Quality)"):
        st.markdown("""**Mantık:** Üretilen parçaların ne kadarının ilk seferde doğru üretildiğini ölçer.
**Neden Türettik?** "Fire Adedi" verisi olmadığından, tekrar işleme alınan **Rework** süreleri kalite kaybının kanıtı olarak kullanılmıştır.
**Formül:** $$\\text{Kalite Oranı} = \\frac{\\text{Üretim}}{\\text{Üretim} + \\text{Rework}} \\times 100$$""")

    with st.expander("3️⃣ Performans Oranı"):
        st.markdown("""**Mantık:** Klasik OEE'deki "Üretilen / Hedeflenen Adet" mantığının **süre bazındaki** karşılığıdır. İşlemlerin hedefe kıyasla ne kadar hızlı yapıldığını ölçer.
**Formül:** $$\\text{Performans Oranı} = \\frac{\\sum (\\text{Hedef C/T})}{\\sum (\\text{Gerçek Süre})}$$""")

    st.markdown("### 👤 Operatör Karnesi Formülleri")
    with st.expander("4️⃣ CT Sapması (%)"):
        st.markdown("""**Neden Türettik?** Operatörün bir parçayı işleme süresinin **katalog hedefine** göre yüzde kaç saptığını bulmak için. Eksi (-) ise operatör hedeften hızlıdır.
**Formül:** $$\\text{CT Sapması} = \\frac{\\text{Gerçek Süre} - \\text{Katalog C/T}}{\\text{Katalog C/T}} \\times 100$$""")

    with st.expander("5️⃣ Emsal Sapması (%)"):
        st.markdown("""**Neden Türettik?** Katalog hedefleri hatalıysa, operatörün hızını **aynı işi yapan diğer ustaların performansına** göre kıyaslamak için. Eksi değer ustayı emsallerinden hızlı yapar.
**Formül:** $$\\text{Emsal Sapması} = \\frac{\\text{Gerçek Süre} - \\text{Emsal Medyan Süre}}{\\text{Emsal Medyan Süre}} \\times 100$$""")

    with st.expander("6️⃣ Düşük Güven (Satır / % )"):
        st.markdown("**Neden Türettik?** Bir operatörün işlem süresi, hedef C/T'nin yarısından bile kısa sürdüyse, burada muhtemelen 'yanlış zaman girişi' vardır. Bu şüpheli kayıtları tespit etmek içindir.")

    st.markdown("### 🎯 Hedef Güncelleme (Önerilen C/T)")
    with st.expander("7️⃣ Önerilen C/T Nasıl Hesaplanır?"):
        st.markdown("""**Neden Türettik?** Hatalı katalog hedefleri yerine, parçayı işleyen **TÜM operatörlerin performansından** yola çıkılarak yeni hedef belirlemek için.
**Kural:** Ortalama hesaplamak hatalıdır çünkü tek bir arıza kaydı ortalamayı bozar. Bu yüzden olağandışı verilerden etkilenmeyen **Medyan (Ortanca Değer)** kullanılır.
**Formül:** $$\\text{Önerilen C/T} = \\text{Medyan}(\\text{Gerçek Süre})$$""")

# ====================================================================================
# AŞAĞIDAKİ KODLAR GİZLENMİŞ MODÜLLERDİR (SİLİNMEMİŞTİR)
# Ekranda kötü görünmemesi için Python yorum satırı (#) ile devre dışı bırakılmıştır.
# ====================================================================================
#
# --- GİZLENEN SEKME: PARÇA-TEZGAH ISI HARİTASI ---
# with sekmeler[3]:
#     heat = compute_heatmap(df)
#     if heat.empty:
#         st.info("Yeterli veri yok.")
#     else:
#         fig = px.imshow(heat, aspect="auto", color_continuous_scale="Reds", labels=dict(x="Tezgah", y="Parça No", color="Saat"))
#         st.plotly_chart(fig, use_container_width=True)
#
# --- GİZLENEN SEKME: VARDİYA YORGUNLUK ---
# with sekmeler[4]:
#     vy = compute_vardiya_yorgunluk(df)
#     if vy.empty:
#         st.info("Vardiya_Ici_Pozisyon_Yuzde hesaplanamadı (BAŞLANGIÇ_SAAT/VARDİYA eksik olabilir).")
#     else:
#         fig = px.bar(vy, x="Dilim", y="Hata_Orani_%", color="Hata_Orani_%", color_continuous_scale="Reds", title="Vardiya İçi Çeyreğe Göre Hata Oranı")
#         st.plotly_chart(fig, use_container_width=True)
#         st.dataframe(vy, use_container_width=True, hide_index=True)
#
# --- GİZLENEN SEKME: MTBF & MTTR ---
# with sekmeler[5]:
#     mtbf = compute_mtbf_mttr(df)
#     if mtbf.empty:
#         st.info("Arıza kaydı yok.")
#     else:
#         c1, c2 = st.columns(2)
#         with c1:
#             st.plotly_chart(px.bar(mtbf.dropna(subset=["MTBF_Saat"]), x="MTBF_Saat", y="TEZGAH", orientation="h", color="MTBF_Saat", color_continuous_scale="RdYlGn", title="MTBF"), use_container_width=True)
#         with c2:
#             st.plotly_chart(px.bar(mtbf, x="TEZGAH", y="MTTR_Saat", color="MTTR_Saat", color_continuous_scale="Reds", title="MTTR"), use_container_width=True)
#         st.dataframe(mtbf, use_container_width=True, hide_index=True)