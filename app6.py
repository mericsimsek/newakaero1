# -*- coding: utf-8 -*-
"""
app6.py — Günlük Üretim Akışı (Sade, Anlaşılır Yönetici Görünümü)
"""
from __future__ import annotations

import re

import numpy as np
import pandas as pd
import streamlit as st
# 1. ADIM: Kendi yazdığın stil dosyasını buraya çağır

from common import veri_kaynagi_secici
from uretim_akisi import (hazirla, saat_tablosu, nihai_biten_satirlar, gunluk_ai_baglami_olustur, _op_sirala, CALISMA,
                          donem_sec, durum_saatleri, tezgah_listesi, kart_satiri, parca_durumu_df,
                          parca_durumu_tablosu, olay_gunlugu, _GUN_KISA)
from stil import stil_uygula, sayfa_basligi, bolum_basligi, filtre_seridi, cipler, tablo_stili, kpi_satiri

# Streamlit sayfa ayarların (zaten kodunda vardır)
st.set_page_config(
    page_title="Üretim Dashboard", 
    layout="wide", 
    initial_sidebar_state="expanded"
    
)

# 2. ADIM: CSS SİHRİNİ DEVREYE SOK!

st.set_page_config(page_title="Günlük Üretim Akışı", layout="wide", page_icon="🏭")

stil_uygula()
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

# ===================== TEK SAYFA MİMARİSİ =====================
from datetime import timedelta

try:
    import plotly.graph_objects as go
except ImportError:
    go = None

RENK = {"Üretim": "#22c55e", "Plansız_Duruş": "#ef4444", "Set_Up": "#f59e0b", "Rework": "#a855f7"}
ETIKET = {"Üretim": "Net Üretim", "Plansız_Duruş": "Plansız Duruş", "Set_Up": "Set-up", "Rework": "Rework"}


def _grafik(p, baslik):
    if go is None:
        st.bar_chart(p.rename(columns=ETIKET))
        return
    fig = go.Figure()
    for d in RENK:
        fig.add_bar(name=ETIKET[d], x=p.index.tolist(), y=p[d].round(2).tolist(), marker_color=RENK[d])
    fig.update_layout(barmode="stack", title=baslik, height=380, margin=dict(l=10, r=10, t=50, b=10),
                      legend=dict(orientation="h", y=-0.25), yaxis_title="saat")
    st.plotly_chart(fig, use_container_width=True)


def _donut(d):
    st.markdown("**🛑 Plansız Duruş Sebepleri**")
    dd = d[d["Durum"] == "Plansız_Duruş"]
    r = (dd.assign(Sebep=dd["Sebep"].replace("", "Belirtilmemiş"))
           .groupby("Sebep")["Süre"].sum().sort_values(ascending=False))
    r = r[r > 0]
    if r.empty:
        st.success("Duruş yok ✅")
    elif len(r) == 1:
        st.metric(r.index[0], f"{r.iloc[0]:.2f} sa")
    else:
        if len(r) > 6:
            r = pd.concat([r.iloc[:6], pd.Series({"Diğer": r.iloc[6:].sum()})])
        if go is None:
            st.dataframe(r.round(2).rename("sa"), use_container_width=True)
        else:
            fig = go.Figure(go.Pie(labels=r.index.tolist(), values=r.round(2).tolist(), hole=0.55, sort=False))
            fig.update_layout(height=380, margin=dict(l=10, r=10, t=10, b=10), legend=dict(orientation="h", y=-0.2))
            st.plotly_chart(fig, use_container_width=True)


def _suzgec_temizle():
    for k in ("f_tez", "f_kisi", "f_durum", "f_vardiya", "f_parca", "f_op"):
        st.session_state[k] = []
    st.session_state["f_ara"] = ""


def _toplam_ekle(t, anahtar):
    s = t.drop(columns=anahtar).sum().round(2)
    return pd.concat([t, pd.DataFrame([{anahtar: "TOPLAM", **s.to_dict()}])], ignore_index=True)


# --- Sidebar iskeleti: dönem → veri kaynağı → güncelle → AI ---
# ---------- Panel yardımcıları ----------
PANELLER = ["📈 Grafikler", "🏭 Tezgah Durumu", "👷 Operatörler", "📦 Parça Durumu", "⚠️ Dikkat Edilecekler"]


def _panel_sec():
    if hasattr(st, "pills"):
        return st.pills("Görünüm", PANELLER, selection_mode="multi", default=["📈 Grafikler"],
                        key="ana_panel", label_visibility="collapsed") or []
    return st.multiselect("Görünüm", PANELLER, default=["📈 Grafikler"], key="ana_panel",
                          label_visibility="collapsed")


def _tezgah_paneli(Gv, tum_tez):
    bolum_basligi("🏭 Tezgah Durumu", "net saat · duruş · set-up · rework")
    t = saat_tablosu(Gv, "Tezgah", sorted(set(tum_tez) | (set(Gv["Tezgah"]) - {"-"})))
    kartlar = []
    for _, r in t.iterrows():
        net, dur, su = r["⚙️ Net Üretim (sa)"], r["🛑 Plansız Duruş (sa)"], r["🔧 Set-up (sa)"]
        rw, top = r["♻️ Rework (sa)"], r["⏱️ Toplam (sa)"]
        if top <= 0:
            kartlar.append((r["Tezgah"], "—", "pasif", "gri"))
        else:
            ton = "kirmizi" if dur / top > 0.15 else "yesil"
            kartlar.append((r["Tezgah"], f"{net:,.2f} sa", f"🛑 {dur:.2f} · 🔧 {su:.2f} · ♻️ {rw:.2f}", ton))
    kpi_satiri(kartlar)
    st.caption(f"TOPLAM → Net {t['⚙️ Net Üretim (sa)'].sum():,.2f} · Plansız {t['🛑 Plansız Duruş (sa)'].sum():,.2f} · "
               f"Set-up {t['🔧 Set-up (sa)'].sum():,.2f} · Rework {t['♻️ Rework (sa)'].sum():,.2f} sa  |  "
               "🟢 normal · 🔴 plansız duruş %15'in üstünde · ⚪ pasif")


def _operator_paneli(Gv, tum_op):
    bolum_basligi("👷 Operatörler", "çalışma süreleri")
    op_t = (saat_tablosu(Gv[Gv["Operatör"] != "-"], "Operatör")
            .sort_values("⏱️ Toplam (sa)", ascending=False)
            .rename(columns={"⚙️ Net Üretim (sa)": "Net Üretim", "🛑 Plansız Duruş (sa)": "Duruş",
                             "🔧 Set-up (sa)": "Set-up", "♻️ Rework (sa)": "Rework", "⏱️ Toplam (sa)": "Toplam Saat"}))
    op_t = _toplam_ekle(op_t, "Operatör")
    st.dataframe(op_t, hide_index=True, use_container_width=True, height=min(60 + 35 * len(op_t), 420),
                 column_config={c: st.column_config.NumberColumn(format="%.2f")
                                for c in ["Net Üretim", "Duruş", "Set-up", "Rework", "Toplam Saat"]})
    pas_o = [o for o in tum_op if o not in set(Gv["Operatör"])]
    st.caption("💤 **Pasif operatör:** " + (", ".join(pas_o) or "yok"))


def _parca_paneli(kd, c_bitti):
    bolum_basligi("📦 Parça Durumu", "nihai · ara operasyon · devam")
    k = parca_durumu_df(kd) if c_bitti else None
    if k is None or k.empty:
        st.info("Bu seçimde parça kaydı yok.")
        return
    st.caption("Nihai = rotanın son (FİNİSH) operasyonu bitti · Ara op. = başka bir operasyonu bitti, "
               "sonraki operasyonu bekliyor · Devam = dönemde hiç operasyon bitmedi")
    st.dataframe(parca_durumu_tablosu(k), hide_index=True, use_container_width=True)


def _dikkat_paneli(Gv, tum_tez, d_bas, d_bit):
    bolum_basligi("⚠️ Dikkat Edilecekler", "duruş / set-up / rework olayları, pasif tezgahlar")
    log = olay_gunlugu(Gv, tum_tez, d_bas, d_bit)
    if log.empty:
        st.success("Planlanmayan olay yok ✅")
    else:
        st.dataframe(log.sort_values("Süre (sa)", ascending=False, na_position="last"), hide_index=True,
                     use_container_width=True, height=min(60 + 35 * len(log), 420),
                     column_config={"Süre (sa)": st.column_config.NumberColumn(format="%.2f")})


# --- Sidebar: veri kaynağı → güncelle → AI ---
df_ham, kaynak_notu = veri_kaynagi_secici("home")
sb_islem = st.sidebar.container()
sb_ai = st.sidebar.container()

with sb_islem:
    st.divider()
    if st.button("🔄 Veriyi Çek ve Güncelle", use_container_width=True):
        with st.spinner("Şirket klasöründen veri alınıyor ve ETL çalıştırılıyor..."):
            basarili, mesaj = veriyi_cek_ve_guncelle()
        if basarili:
            st.cache_data.clear()
            st.success(mesaj)
            st.rerun()
        else:
            st.error(mesaj)

with sb_ai:
    st.divider()
    with st.expander("🤖 AI Asistan", expanded=False):
        st.caption("Sohbet burada olacak (henüz taşınmadı).")

T, c_bitti = hazirla(df_ham)
sayfa_basligi("🏭 Günlük Üretim Akışı", "Üretim günü 07:00 → ertesi gün 07:00 · hafta Pazartesi 07:00'de başlar")
if T.empty or T["Tarih"].notna().sum() == 0:
    st.warning("Gösterilecek tarih/veri bulunamadı.")
    st.stop()

mx = T["Tarih"].max().date()

# --- ZAMAN ÇUBUĞU (ana ekranın en üstü) ---
with st.container(border=True):
    secim = donem_sec(T, "ana", aralik_izni=True)
if secim is None:
    st.stop()
d_bas, d_bit, n_kayma = secim

G = T[(T["Tarih"] >= pd.Timestamp(d_bas)) & (T["Tarih"] <= pd.Timestamp(d_bit))]
if G.empty:
    st.warning("Seçilen dönemde kayıt yok.")
    st.stop()

tum_tez = tezgah_listesi(T)
tum_op = sorted(o for o in T["Operatör"].unique() if o != "-")
tez_hepsi = sorted(T["Tezgah"].unique())

ust = st.container()   # çipler + kartlar + paneller: süzgeçler okunduktan sonra dolacak

# --- Süzgeç çekmecesi (kapalı) ---
with st.expander("🔍 Detaylı Süzgeçler ve Arama", expanded=False):
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
x = G
for kol, sec_ in (("Tezgah", s_tez), ("Operatör", s_kisi), ("Durum", s_durum), ("Vardiya", s_vardiya),
                  ("Parça", s_parca), ("Op", s_op)):
    if sec_:
        x = x[x[kol].isin(sec_)]
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

st.session_state["gunluk_ai_baglami"] = gunluk_ai_baglami_olustur(
    G, (d_bas, d_bit), tum_tezgah=tum_tez, tum_operator=tum_op)

aktif = [f"{ad}: {', '.join(map(str, v))}" for ad, v in (
    ("Tezgah", s_tez), ("Operatör", s_kisi), ("Tür", [d.replace("_", " ") for d in s_durum]),
    ("Vardiya", s_vardiya), ("Parça", s_parca), ("Op", s_op)) if v]
if s_ara:
    aktif.append(f"S/N: {s_ara}")
filtre_var = bool(aktif)
kd = x if filtre_var else G

# önceki dönem kıyası (yalnızca süzgeç yokken)
uz = max((min(d_bit, mx) - d_bas).days + 1, 1)
p_bas = d_bas - timedelta(days=n_kayma)
P = T[(T["Tarih"] >= pd.Timestamp(p_bas)) & (T["Tarih"] <= pd.Timestamp(p_bas + timedelta(days=uz - 1)))]
onceki = durum_saatleri(P) if (not filtre_var and not P.empty) else None
onceki_adi = "önceki gün" if n_kayma == 1 else "önceki hafta" if n_kayma == 7 else "önceki dönem"

# --- ÜST: çipler + kartlar + görünüm butonları + paneller ---
with ust:
    if filtre_var:
        cf, cb = st.columns([8, 1.4])
        with cf:
            filtre_seridi(aktif, len(x), len(G))
        cb.button("✖ Temizle", on_click=_suzgec_temizle, key="f_temizle")
    kart_satiri(kd, c_bitti, onceki, onceki_adi)
    calisma = G[G["Durum"].isin(CALISMA)]
    st.caption(f"👷 {calisma['Operatör'].nunique()} çalışan · 🏭 {calisma['Tezgah'].nunique()} aktif tezgah · "
               f"⏱️ {G['Süre'].sum():,.1f} sa kayıtlı süre"
               + (" · 🎯 kartlar ve grafikler süzgece göre" if filtre_var else ""))

    paneller = _panel_sec()

    if "📈 Grafikler" in paneller:
        if kd.empty:
            st.info("Süzgeçlerle eşleşen kayıt yok.")
        else:
            sol, sag = st.columns(2)
            with sol:
                gorunum = "🏭 Tezgah Bazlı"
                if d_bas != d_bit:
                    gorunum = st.radio("Grafik", ["🏭 Tezgah Bazlı", "📅 Gün Bazlı"], horizontal=True,
                                       key="ana_grafik", label_visibility="collapsed")
                if gorunum.startswith("📅"):
                    p_ = (kd.pivot_table(index="Tarih", columns="Durum", values="Süre", aggfunc="sum", fill_value=0)
                            .reindex(columns=list(RENK), fill_value=0)
                            .reindex(pd.date_range(d_bas, d_bit), fill_value=0))
                    p_.index = [f"G{d.weekday() + 1} {_GUN_KISA[d.weekday()]} {d:%d.%m}" for d in p_.index]
                    _grafik(p_, "Gün Bazlı Çalışma ve Kayıplar (saat)")
                else:
                    p_ = (kd[kd["Tezgah"] != "-"].pivot_table(index="Tezgah", columns="Durum", values="Süre",
                                                              aggfunc="sum", fill_value=0)
                          .reindex(columns=list(RENK), fill_value=0))
                    p_ = p_.reindex(sorted(set(tum_tez) | set(p_.index)), fill_value=0)
                    _grafik(p_, "Tezgah Bazlı Çalışma ve Kayıplar (saat)")
            with sag:
                _donut(kd)

    # yan yana kartlı paneller (2'li satırlar)
    yan = [p for p in ("🏭 Tezgah Durumu", "👷 Operatörler", "📦 Parça Durumu") if p in paneller]
    for i in range(0, len(yan), 2):
        parca = yan[i:i + 2]
        kolonlar = st.columns(2) if len(parca) == 2 else [st.container()]
        for kol, p in zip(kolonlar, parca):
            with kol:
                with st.container(border=True):
                    if p.startswith("🏭"):
                        _tezgah_paneli(Gv, tum_tez)
                    elif p.startswith("👷"):
                        _operator_paneli(Gv, tum_op)
                    else:
                        _parca_paneli(kd, c_bitti)
    if "⚠️ Dikkat Edilecekler" in paneller:
        with st.container(border=True):
            _dikkat_paneli(Gv, tum_tez, d_bas, d_bit)

# --- ANA TABLO ---
bolum_basligi("📋 Üretim Kayıtları", "tezgah · operatör · parça · süre")
if x.empty:
    filtre_seridi(aktif, 0, len(G))
    st.info("Seçili dönem ve süzgeçlerle eşleşen kayıt yok.")
else:
    sadece_biten = st.toggle("✅ Sadece biten parçaları göster (nihai mamul)", key="goster_biten", disabled=not c_bitti)
    xg = x[x.index.isin(nihai_biten_satirlar(x))] if sadece_biten else x
    filtre_seridi(aktif, len(xg), len(G))
    if sadece_biten:
        if xg.empty:
            st.info("Seçili dönem ve süzgeçlerle nihai mamul olarak biten parça yok.")
        else:
            kk = parca_durumu_df(x)
            nih = kk[kk["Durum"] == "nihai"]["Parça"].value_counts()
            ara = kk[kk["Durum"] == "ara"].groupby(["Parça", "Op"]).size().sort_values(ascending=False)
            st.markdown(f"**✅ {nih.sum()} parça FİNİSH tamamlandı**")
            cipler(list(nih.items()))
            if len(ara):
                st.markdown(f"**🔧 {ara.sum()} parça ara operasyonu bitirdi**")
                cipler([(f"{p} Op {o}", n) for (p, o), n in ara.items()])
            st.caption("Tabloda her parçanın son (FİNİSH) operasyon satırı görünüyor.")

    gos = xg[["Tarih", "Vardiya", "Saat", "Tezgah", "Operatör", "Parça – Op", "Uzun Ref", "S/N",
              "İşlem Adımı", "Süre", "Adet", "Durum / Sebep"]].copy()
    gos["Tarih"] = gos["Tarih"].dt.date
    gos["S/N"] = gos["S/N"].fillna("")
    gos = gos.rename(columns={"Tarih": "Üretim Günü", "Süre": "Süre (sa)"})
    if not (gos["İşlem Adımı"] != "").any():
        gos = gos.drop(columns="İşlem Adımı")
    stilli = len(gos) <= 3000
    st.dataframe(tablo_stili(gos) if stilli else gos, use_container_width=True, hide_index=True, height=650,
                 key=f"tablo_{s_sira}_{sadece_biten}_{len(xg)}",
                 column_config=None if stilli else {
                     "Üretim Günü": st.column_config.DateColumn(format="DD.MM.YYYY"),
                     "Süre (sa)": st.column_config.NumberColumn(format="%.2f")})