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
from uretim_akisi import hazirla, sn_sayilari, biten_operasyon, saat_tablosu, nihai_biten_satirlar, zaman_tanilama_tablolari, gunluk_ai_baglami_olustur, op_ad, _op_sirala, CALISMA, donem_sec, durum_saatleri, tezgah_listesi, kart_satiri,parca_durumu_goster, parca_durumu_df
import yonetici_ozeti
st.set_page_config(page_title="Günlük Üretim Akışı", layout="wide", page_icon="🏭")
st.markdown("""
<style>
    /* Büyük Rakamların (Değerlerin) Boyutu */
    [data-testid='stMetricValue'] {
        font-size: 2rem !important;
        font-weight: 700 !important;
    }
    /* Kartların Başlıklarının (Etiketlerin) Boyutu */
    [data-testid='stMetricLabel'] {
        font-size: 1.1rem !important;
        font-weight: 600 !important;
    }
    /* Kartların Başlıklarının altındaki açıklama ikonlarını (?) hizalama */
    [data-testid='stMetricLabel'] svg {
        width: 1.2rem;
        height: 1.2rem;
    }
        [data-testid='stMetric'] {
        background: rgba(128,128,128,0.08);
        border: 1px solid rgba(128,128,128,0.25);
        border-radius: 12px;
        padding: 12px 16px;
    }
</style>
""", unsafe_allow_html=True)

import os
import sys
import glob
import shutil
import subprocess

# --- SADECE BU 2 SATIRI KENDİ BİLGİLERİNE GÖRE DÜZENLE ---
KAYNAK_KLASOR = r"\\Akbeynas\paylasim\GÜNLÜK RAPORLAR\İMALAT TAKİP REV3.xlsx"  # Şirket klasörünün adres çubuğundaki yolu
ETL_SCRIPT_ADI = "etl.py"                          # Çalıştırdığın ETL dosyasının adı

HEDEF_DOSYA = r"C:\Users\Akaero\Documents\newakaeroproject1 - Kopya\puredata1.xlsx"

def veriyi_cek_ve_guncelle():
    try:
        # Klasördeki açık olmayan (~$ ile başlamayan) tüm Excel dosyalarını bul
        excel_dosyalari = [
            f for f in glob.glob(os.path.join(KAYNAK_KLASOR, "*.xlsx"))
            if not os.path.basename(f).startswith("~$")
        ]
        
        if not excel_dosyalari:
            return False, "Şirket klasöründe Excel dosyası bulunamadı veya klasör yolu yanlış!"

        # Klasördeki en son güncellenen Excel dosyasını otomatik seç
        kaynak_dosya = max(excel_dosyalari, key=os.path.getmtime)

        # Kendi klasörüne puredata1.xlsx adıyla kopyala
        shutil.copy2(kaynak_dosya, HEDEF_DOSYA)

        # Sanal ortamındaki (venv) Python ile ETL dosyasını çalıştır
        etl_sonuc = subprocess.run(
            [sys.executable, ETL_SCRIPT_ADI],
            cwd=r"C:\Users\Akaero\Documents\newakaeroproject1 - Kopya",
            capture_output=True,
            text=True
        )
        if etl_sonuc.returncode != 0:
            return False, f"ETL çalışırken hata oluştu:\n{etl_sonuc.stderr}"

        return True, f"Güncellendi! '{os.path.basename(kaynak_dosya)}' çekildi ve işlendi."

    except Exception as e:
        return False, f"Hata: {str(e)}"

# --- EKRANDAKİ BUTON ---
if st.button("🔄 Veriyi Çek ve Güncelle"):
    with st.spinner("Şirket klasöründen veri alınıyor ve ETL çalıştırılıyor..."):
        basarili, mesaj = veriyi_cek_ve_guncelle()
        if basarili:
            st.cache_data.clear()
            st.success(mesaj)
            st.rerun()
        else:
            st.error(mesaj)

st.title("🏭 Günlük Üretim Akışı")

df_ham, _df, kaynak_notu = veri_yukle_ve_filtrele("home")
T, c_bitti = hazirla(df_ham)
def _toplam_ekle(t, anahtar):
    s = t.drop(columns=anahtar).sum().round(2)
    return pd.concat([t, pd.DataFrame([{anahtar: "TOPLAM", **s.to_dict()}])], ignore_index=True)

def render_muhendislik(df_ham, T, c_bitti):
    # DİKKAT: sekme içinde st.stop() kullanma, diğer sekmeleri de durdurur -> return
    if T.empty or T["Tarih"].notna().sum() == 0:
        st.warning("Gösterilecek tarih/veri bulunamadı.")
        return

    mn, mx = T["Tarih"].min().date(), T["Tarih"].max().date()

    secim = donem_sec(T, "muh", aralik_izni=True)
    if secim is None:
        return
    d_bas, d_bit, _ = secim

    G = T[(T["Tarih"] >= pd.Timestamp(d_bas)) & (T["Tarih"] <= pd.Timestamp(d_bit))]
    if G.empty:
        st.warning("Seçilen tarihte kayıt yok.")
        return

    kart_kabi = st.container()   # kartlar süzgeçler okunduktan sonra burada doldurulur

    ozet_kabi = st.container()   # özet kutusu aşağıda, filtreler okunduktan sonra doldurulacak

    # --- Süzgeçler (kapalı kutu) ---
    tez_hepsi = sorted(T["Tezgah"].unique())
    with st.expander("🔍 Detaylı Süzgeçler", expanded=False):
        c1, c2, c3, c4 = st.columns(4)
        s_tez = c1.multiselect("🏭 Tezgah", tez_hepsi, placeholder="Tümü", key="f_tez")
        s_kisi = c2.multiselect("👷 Operatör", sorted(T["Operatör"].unique()), placeholder="Tümü", key="f_kisi")
        s_durum = c3.multiselect("Tür (Üretim/Duruş)", sorted(T["Durum"].unique()), placeholder="Tümü",
                                 format_func=lambda d: d.replace("_", " "), key="f_durum")
        s_vardiya = c4.multiselect("🕒 Vardiya", sorted(T["Vardiya"].unique()), placeholder="Tümü", key="f_vardiya")
        d1, d2, d3, d4 = st.columns([2, 1, 2, 3])
        s_parca = d1.multiselect("🔩 Parça Kodu", sorted(T["Parça"].dropna().unique()), placeholder="Tümü", key="f_parca")
        s_op = d2.multiselect("⚙️ Op", sorted(T["Op"].dropna().unique(), key=_op_sirala), placeholder="Tümü", key="f_op")
        s_ara = d3.text_input("🔎 Arama (S/N)", placeholder="Örn: 2H50M", key="f_ara")
        s_sira = d4.radio("Sırala", ["⏱ Tezgah ve Zaman", "👤 Operatör ve Zaman", "🔗 S/N'ye Göre (Grupla)"],
                          horizontal=True, key="f_sira")
    if set(s_tez) == set(tez_hepsi):   # hepsi seçiliyse filtre yok say
        s_tez = []

    Gv = G[G["Vardiya"].isin(s_vardiya)] if s_vardiya else G
    tum_tez = tezgah_listesi(T)
    tez_tablo = sorted(set(tum_tez) | (set(Gv["Tezgah"]) - {"-"}))
    tum_op = sorted(o for o in T["Operatör"].unique() if o != "-")
    _sayi = {c: st.column_config.NumberColumn(format="%.2f") for c in
             ["⚙️ Net Üretim (sa)", "🛑 Plansız Duruş (sa)", "🔧 Set-up (sa)", "♻️ Rework (sa)", "⏱️ Toplam (sa)"]}

    with ozet_kabi:
        with st.expander("📊 Tezgah & Operatör Özeti (Çalışma Süreleri ve Aktif/Pasif Durumu)", expanded=False):
            sol, sag = st.columns([3, 2])
            with sol:
                st.markdown("**🏭 Tezgahlar**")
                tz_t = _toplam_ekle(saat_tablosu(Gv, "Tezgah", tez_tablo), "Tezgah")
                st.dataframe(tz_t, hide_index=True, use_container_width=True,
                             height=min(60 + 35 * len(tz_t), 480), column_config=_sayi)
            with sag:
                st.markdown("**👷 Çalışan Operatörler**")
                op_t = (saat_tablosu(Gv[Gv["Operatör"] != "-"], "Operatör")
                        .sort_values("⏱️ Toplam (sa)", ascending=False)
                        .rename(columns={"⚙️ Net Üretim (sa)": "Net Üretim", "🛑 Plansız Duruş (sa)": "Duruş",
                                         "🔧 Set-up (sa)": "Set-up", "♻️ Rework (sa)": "Rework", "⏱️ Toplam (sa)": "Toplam Saat"}))
                op_t = _toplam_ekle(op_t, "Operatör")
                st.dataframe(op_t, hide_index=True, use_container_width=True,
                             height=min(60 + 35 * len(op_t), 480),
                             column_config={c: st.column_config.NumberColumn(format="%.2f")
                                            for c in ["Net Üretim", "Duruş", "Set-up", "Rework", "Toplam Saat"]})
                pas_t = [z for z in tum_tez if z not in set(Gv["Tezgah"])]
                pas_o = [o for o in tum_op if o not in set(Gv["Operatör"])]
                st.caption("💤 **Pasif tezgah:** " + (", ".join(pas_t) or "yok"))
                st.caption("💤 **Pasif operatör:** " + (", ".join(pas_o) or "yok"))

    # --- Filtre uygula ---
    x = G
    for kol, secim in (("Tezgah", s_tez), ("Operatör", s_kisi), ("Durum", s_durum),
                       ("Vardiya", s_vardiya), ("Parça", s_parca), ("Op", s_op)):
        if secim:
            x = x[x[kol].isin(secim)]
    if s_ara:
        x = x[x["S/N"].fillna("").str.lower().str.contains(s_ara.strip().lower(), regex=False)]

    if "Tezgah" in s_sira:
        x = x.sort_values(["Tezgah", "_sira", "_bit_dk"], na_position="last")
    elif "Operatör" in s_sira:
        x = x.sort_values(["Operatör", "_sira", "_bit_dk"], na_position="last")
    else:
        anahtar = x["_a"].fillna(pd.Series("~" + x.index.astype(str), index=x.index))
        x = (x.assign(_k=anahtar, _ilk=x.groupby(anahtar)["_pz"].transform("min"))
             .sort_values(["_ilk", "_k", "_pz"], na_position="last"))
    filtre_var = any([s_tez, s_kisi, s_durum, s_vardiya, s_parca, s_op, s_ara])
    kd = x if filtre_var else G
    with kart_kabi:
        kart_satiri(kd, c_bitti)
        calisma = G[G["Durum"].isin(CALISMA)]
        st.caption(f"👷 {calisma['Operatör'].nunique()} çalışan · 🏭 {calisma['Tezgah'].nunique()} aktif tezgah · "
                   f"⏱️ {G['Süre'].sum():,.1f} sa kayıtlı süre")
        parca_durumu_goster(kd, c_bitti)
        if filtre_var:
            t = durum_saatleri(G)
            st.caption(f"🎯 Kartlar süzgece göre. Dönem toplamı → Net {t['uretim']:,.2f} · Plansız {t['durus']:,.2f} · "
                       f"Set-up {t['setup']:,.2f} · Rework {t['rework']:,.2f} sa")
                       
    st.session_state["gunluk_ai_baglami"] = gunluk_ai_baglami_olustur(
        G, (d_bas, d_bit), tum_tezgah=tum_tez, tum_operator=tum_op)

    if x.empty:
        st.info("Seçili dönem ve filtrelerle eşleşen kayıt yok. Tarihi ya da filtreleri değiştirebilirsin.")
        return

    # --- Filtreye göre KPI (üstteki fabrika KPI'ından ayrı) ---


    sadece_biten = st.toggle("✅ Sadece biten parçaları göster (nihai mamul)", key="goster_biten", disabled=not c_bitti)
    xg = x[x.index.isin(nihai_biten_satirlar(x))] if sadece_biten else x
    if sadece_biten:
        if xg.empty:
            st.info("Seçili dönem ve filtrelerle nihai mamul olarak biten parça yok.")
        else:
            kk = parca_durumu_df(x)
            nih = kk[kk["Durum"] == "nihai"]["Parça"].value_counts()
            ara = kk[kk["Durum"] == "ara"].groupby(["Parça", "Op"]).size().sort_values(ascending=False)
            st.markdown(f"**✅ {nih.sum()} parça FİNİSH tamamlandı:** " + "  ·  ".join(f"{p}: **{n}**" for p, n in nih.items()))
            if len(ara):
                st.markdown(f"**🔧 {ara.sum()} parça ara operasyonu bitirdi:** "
                            + "  ·  ".join(f"{p} Op {o}: **{n}**" for (p, o), n in ara.items()))
            st.caption("Tabloda her parçanın son (FİNİSH) operasyon satırı görünüyor.")

    gos = xg[["Tarih", "Saat", "Tezgah", "Operatör", "Parça – Op", "Uzun Ref", "S/N",
              "İşlem Adımı", "Süre", "Adet", "Durum / Sebep"]].copy()
    gos["Tarih"] = gos["Tarih"].dt.date
    gos["S/N"] = gos["S/N"].fillna("")
    gos = gos.rename(columns={"Tarih": "Üretim Günü", "Süre": "Süre (sa)"})
    if not (gos["İşlem Adımı"] != "").any():      # tamamen boşsa gizle
        gos = gos.drop(columns="İşlem Adımı")

    st.dataframe(gos, use_container_width=True, hide_index=True, height=650,
                 key=f"tablo_{s_sira}_{sadece_biten}_{len(xg)}",
                 column_config={"Üretim Günü": st.column_config.DateColumn(format="DD.MM.YYYY"),
                                "Süre (sa)": st.column_config.NumberColumn(format="%.2f")})

    with st.expander("🤖 AI'a giden günlük özet (önizleme)"):
        st.code(st.session_state["gunluk_ai_baglami"], language=None)

    with st.expander("🧪 Zaman katmanı kontrolü (07→07 ile Excel 23→23 karşılaştırma)", expanded=False):
        gun_k = st.date_input("Kontrol günü", value=mx, min_value=mn, max_value=mx, format="DD.MM.YYYY", key="zk_gun")
        A_, _ = hazirla(df_ham, "07_07")
        B_, _ = hazirla(df_ham, "23_23")
        st.caption(f"Tüm veri toplam süre → 07-07: {A_['Süre'].sum():.2f} sa | 23-23: {B_['Süre'].sum():.2f} sa (aynı olmalı)")
        ilk, son, gt, ht = zaman_tanilama_tablolari(A_, B_, gun_k)
        st.markdown("**1) Excel günü sınırı** — beklenen: ilk başlangıç 23:00, son bitiş 23:00")
        k1_, k2_ = st.columns(2)
        k1_.dataframe(ilk, hide_index=True)
        k2_.dataframe(son, hide_index=True)
        st.markdown(f"**2) {gun_k:%d.%m.%Y} — iki mod yan yana**")
        st.dataframe(gt, hide_index=True)
        st.markdown("**3) Aynı haftanın G1–G7 günleri**")
        st.dataframe(ht, hide_index=True)


sek_yon, sek_muh = st.tabs(["📊 Yönetici Özeti", "📋 Mühendislik Analizi"])
with sek_yon:
    yonetici_ozeti.render(T, c_bitti)
with sek_muh:
    render_muhendislik(df_ham, T, c_bitti)
with sek_ai:
    st.info("AI Asistan — şimdilik ayrı sayfada (pages/4_AI_Asistan.py). Faz 4'te buraya taşınacak.")