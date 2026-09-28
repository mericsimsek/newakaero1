# -*- coding: utf-8 -*-
"""
5_Sunum_Dogrulama.py — Sunum (31 Ağustos - 5 Eylül) ↔ dashboard hesapları.
Her sayı tek tek: sunumdaki değer, bizim canlı hesap, fark, ✅/⚠️ durumu.
Ayrıca günlük kapsam ısı haritası (eksik vardiya/gün tespiti) ve saniyeye kadar
ham kayıt dökümü.
"""
from __future__ import annotations

import pandas as pd
import plotly.express as px
import streamlit as st

from common import (
    veri_yukle_ve_filtrele, sunum_karsilastirma, compute_zaman_cizelgesi,
    SUNUM_SETUP_FABRIKA_ORT_DK, SUNUM_TOPLAM_KAYIP_SAAT,
)

st.set_page_config(page_title="Sunum Doğrulama", layout="wide", page_icon="🧪")
st.title("🧪 Sunum ↔ Hesap Doğrulaması")
st.caption(
    "Sunumdaki her rakamın gerçek veriyle canlı hesaplanan karşılığı. "
    "Emsal referansı TÜM veridir (sunumla aynı metodoloji). Sol paneldeki tarih/tezgah "
    "filtreleri bu sayfada kullanılmaz — pencereyi aşağıdan seçin."
)

df_ham, _df_filtreli, kaynak_notu = veri_yukle_ve_filtrele("sunum")
st.caption(kaynak_notu)

if df_ham.empty or "TARİH" not in df_ham.columns:
    st.warning("Veri yok.")
    st.stop()

min_t, max_t = df_ham["TARİH"].min().date(), df_ham["TARİH"].max().date()
c1, c2 = st.columns(2)
bas = c1.date_input("Pencere başı", value=max(min_t, pd.Timestamp("2026-08-31").date()), min_value=min_t, max_value=max_t)
bit = c2.date_input("Pencere sonu", value=min(max_t, pd.Timestamp("2026-09-05").date()), min_value=min_t, max_value=max_t)

s = sunum_karsilastirma(df_ham, str(bas), str(bit))
if not s or s.get("pencere_satir", 0) == 0:
    st.warning("Seçilen pencerede kayıt yok.")
    st.stop()

st.metric("Penceredeki kayıt sayısı", f"{s['pencere_satir']:,}")
st.markdown("---")

# ---------------------------------------------------------------- 1. TEZGAH
st.subheader("1️⃣ Tezgah: Kullanılabilirlik / Plansız kayıp / Üretim / Set-up")
t = s["tezgah"]
st.dataframe(t, use_container_width=True, hide_index=True)
ok_kul = int((t["Durum KUL"] == "✅").sum()) if "Durum KUL" in t else 0
ok_pln = int((t["Durum PLN"] == "✅").sum()) if "Durum PLN" in t else 0
k1, k2, k3 = st.columns(3)
k1.metric("Kullanılabilirlik eşleşen", f"{ok_kul}/{len(t)}")
k2.metric("Plansız kayıp eşleşen", f"{ok_pln}/{len(t)}")
k3.metric("Toplam kayıp (plansız+rework)", f"{s['toplam_kayip_bizim']:.2f} sa", delta=f"sunum {SUNUM_TOPLAM_KAYIP_SAAT} sa", delta_color="off")
st.info(
    "**Nasıl okunur?** Tolerans: Kullanılabilirlik ±0.6 puan, plansız kayıp ±0.8 sa. "
    "Set-up sütunları sunumdaki ham tabloyla karşılaştırılır: fark varsa genelde bir kaydın "
    "tarihi pencere dışında kalmıştır (örn. AB-07'de −1.75 sa = 28 Ağustos'a düşen 105 dk'lık kayıt)."
)

# ---------------------------------------------------------------- 2. OPERATÖR
st.subheader("2️⃣ Operatör: Emsal sapma % (10 operatör)")
op = s["operator"]
st.dataframe(op, use_container_width=True, hide_index=True)
if "Sıra (sunum)" in op:
    st.caption(
        f"Sıralama birebir aynı olan operatör: {int((op['Sıra (sunum)'] == op['Sıra (bizim)']).sum())}/{len(op)} · "
        f"±2 puan içinde: {int((op['Durum'] == '✅').sum())}/{len(op)}"
    )

# ---------------------------------------------------------------- 3. SETUP
st.subheader("3️⃣ Set-up kayıtları")
st.dataframe(s["setup"], use_container_width=True, hide_index=True)
st.caption(
    f"Pencerede bizim set-up adedi: {s['setup_adet_bizim']} · ortalama {s['setup_ort_bizim_dk']} dk "
    f"(sunum: 7 kayıt, ortalama {SUNUM_SETUP_FABRIKA_ORT_DK} dk — pencere-dışı kayıt dahil edilirse 7 kayıt / 68.6 dk çıkar)."
)

# ---------------------------------------------------------------- 4. REWORK
st.subheader("4️⃣ Rework")
if s["rework"].empty:
    st.info("Pencerede rework yok.")
else:
    st.dataframe(s["rework"], use_container_width=True, hide_index=True)
    st.caption("Sunum: Nazif Güçlüdağ / AB-07 / 2.5 sa, haftanın tek rework kaynağı.")

# ---------------------------------------------------------------- 5. VARDİYA
st.subheader("5️⃣ Vardiya yorgunluk (çeyrek başına hata oranı %)")
st.dataframe(s["vardiya"], use_container_width=True, hide_index=True)
st.caption(
    "Tek haftalık örneklemde çeyrek başına ~60-90 işlem olduğundan oranlar gürültülüdür; "
    "tam dönemde yön (son çeyrek > ilk çeyrek) sunumla tutarlıdır."
)

st.markdown("---")

# ---------------------------------------------------------------- KAPSAM ISI HARİTASI
st.subheader("🗓️ Günlük Kapsam: Tezgah × Gün toplam kayıtlı saat")
st.caption(
    "Bir tezgahın bir günde 3 vardiya boyunca ~20+ saat kayıt beklenir. Belirgin düşük/sıfır hücreler "
    "o gün bazı vardiya kayıtlarının dosyada EKSİK olabileceğini gösterir (sunumla üretim-saati farklarının olası nedeni)."
)
pen = df_ham[(df_ham["TARİH"].dt.date >= bas) & (df_ham["TARİH"].dt.date <= bit)]
kap = pen.pivot_table(index="TEZGAH", columns=pen["TARİH"].dt.strftime("%m-%d"), values="Sure_Saat", aggfunc="sum", fill_value=0.0).round(2)
st.plotly_chart(px.imshow(kap, text_auto=True, aspect="auto", color_continuous_scale="RdYlGn", labels=dict(color="Saat")), use_container_width=True)
uretim_kap = pen[pen["Durum"] == "Üretim"].pivot_table(index="TEZGAH", columns=pen["TARİH"].dt.strftime("%m-%d"), values="Sure_Saat", aggfunc="sum", fill_value=0.0).round(2)
with st.expander("Sadece ÜRETİM saati (Tezgah × Gün)"):
    st.dataframe(uretim_kap, use_container_width=True)

# ---------------------------------------------------------------- HAM DÖKÜM
st.subheader("📋 Penceredeki TÜM kayıtlar (saniyeye kadar)")
st.caption("Kaynak veri dakika çözünürlüğündedir (saniye alanı her zaman :00) — format saniyeyi gösterir ama gerçek saniye bilgisi kaynakta yoktur.")
z = compute_zaman_cizelgesi(pen)
if not z.empty:
    kol = [c for c in ["TEZGAH", "Operator_Kod", "Durum", "Baslangic_Zaman", "Bitis_Zaman", "Sure_Saat", "Sure_Tutarsizligi_Saat",
                       "KOD & OPE.", "PARÇA NO_ÜRETİM_GİRİLEN", "Durus_Kategorisi", "ÜRETİM_C/T", "Sapma_Yuzdesi"] if c in z.columns]
    goster = z[kol].copy()
    goster["Baslangic_Zaman"] = goster["Baslangic_Zaman"].dt.strftime("%Y-%m-%d %H:%M:%S")
    goster["Bitis_Zaman"] = goster["Bitis_Zaman"].dt.strftime("%Y-%m-%d %H:%M:%S")
    for c_, n_ in [("Sure_Saat", 2), ("ÜRETİM_C/T", 2), ("Sapma_Yuzdesi", 1), ("Sure_Tutarsizligi_Saat", 2)]:
        if c_ in goster.columns:
            goster[c_] = goster[c_].round(n_)
    st.dataframe(goster, use_container_width=True, hide_index=True, height=500)
    st.download_button("⬇️ CSV indir", goster.to_csv(index=False).encode("utf-8-sig"), "pencere_kayitlari.csv", "text/csv")
