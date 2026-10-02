# -*- coding: utf-8 -*-
"""
app5.py — Üretim Zekası Dashboard'u (Sadeleştirilmiş ve Temizlenmiş Son Sürüm)

Bu sürümde:
  • OEE formülü kesin olarak Üretim / (Üretim + Plansız Duruş + Rework) şekline getirildi.
  • Kapasite Kaybı ve CT Güvenilirlik modülleri geçici olarak yorum satırına alındı (if False).
  • Tezgah tablosundan Kalite, Performans ve eski OEE metrikleri tamamen silindi.
  • Operatör tablosundan İşlem Sayısı, İşlem/Saat Hızı ve Düşük Güven metrikleri silindi.
  • Emsal sapması analizinin detayları (Kayıt vs Emsal Kayıt) şeffafça açıklandı.
  • Çöp veri filtresi devrededir (0.05 saatin altı kayıtlar ortalamaları zehirleyemez).
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st
from sn_izleme import render_sn_sekmesi
from common import (
    RENK, DURUM_SIRASI, METRIC_REGISTRY, metrik_aciklamasi, kolon_var,
    veri_yukle_ve_filtrele, compute_tezgah_oee, compute_operator_karne,
    compute_pareto, compute_vardiya_yorgunluk, compute_mtbf_mttr, compute_heatmap,
    compute_ct_guvenilirlik, compute_onerilen_ct, compute_ct_cozum_etkisi, compute_zaman_cizelgesi,
    compute_operator_kapasite,
    EMSAL_MIN_TEKRAR, CT_DUSUK_GUVEN_ORANI, KARNE_MIN_SAAT, KARNE_MIN_SAAT_GUNLUK,
)

try:
    import config as _cfg
except ImportError:
    _cfg = None

# =============================================================================
# SABİTLER
# =============================================================================
OPERATOR_SOZLUGU = {
    "Op_001": "AŞKIN SAKALLI", "Op_002": "BERAT TUNÇ", "Op_003": "CİHAN SARIGÜL", "Op_004": "EMRE TOPRAK",
    "Op_005": "EREN TAMER YELEKÇİ", "Op_006": "ERTUĞRUL ÖZDEMİR", "Op_007": "HAMZA KARAASLAN", "Op_008": "HÜSEYİN YENİCE",
    "Op_009": "HÜSEYİN YILMAZ", "Op_010": "İLKER SEZDİ", "Op_011": "MEHMET ALİ ÇOLAK", "Op_012": "MEHMET EMRE KARAKAŞ",
    "Op_013": "MERT KUŞCU", "Op_014": "MESUT ARDIÇ", "Op_015": "MUHAMMED KORKMAZ", "Op_016": "NAZİF GÜÇLÜDAĞ",
    "Op_017": "NURULLAH ZENGİN", "Op_018": "ÖZCAN ÇELİK", "Op_019": "TANER GÜÇLÜDAĞ", "Op_020": "UMUT ÖZKAN", "Op_021": "YAŞAR ŞENOL",
}
GUNCELLEME_ESIGI_YUZDE = 15
KAPSAM_ESIGI = 0.5
OEE_REFERANS = 85

T_TEZGAH = "📊 Tezgah Özeti"
T_PARETO = "⏱ Duruş Pareto ve Analizi"
T_OPERATOR = "👷 Operatör Karnesi"
T_KRONO = "📅 Kronolojik Üretim Dökümü"
T_FORMUL = "📐 Metrikler ve Formüller"
T_SN = "📦 Parça S/N İzleme"
# T_KAPASITE ve T_CT sekmeleri listeden çıkarıldı (şu anlık devre dışı)
SEKMELER = [
    T_TEZGAH, T_PARETO, T_OPERATOR, T_KRONO, T_FORMUL, T_SN
]

KRONO_KOLONLAR = [
    ("TARİH", "Tarih"), ("VARDİYA", "Vardiya"), ("BAŞLANGIÇ_SAAT", "Başlangıç Saati"), ("BİTİŞ_SAAT", "Bitiş Saati"),
    ("TEZGAH", "Tezgah"), ("Operator_Kod", "Operatör (Gerçek İsim)"), ("PARÇA NO_ÜRETİM_GİRİLEN", "Parça No"),
    ("OPERASYON_NO", "Operasyon No"), ("Durum", "Durum"), ("Durus_Kategorisi", "Duruş Kategorisi"),
    ("Durus_Sebebi_Ham", "Duruş Sebebi"), ("Sure_Saat", "Gerçekleşen Süre (Saat)"), ("ÜRETİM_C/T", "Hedef C/T (Saat)"),
    ("Sapma_Saat", "Sapma (Saat)"), ("Sapma_Yuzdesi", "Sapma (%)"),
]


# =============================================================================
# YARDIMCI FONKSİYONLAR
# =============================================================================
def op_ad(kod):
    return OPERATOR_SOZLUGU.get(kod, kod)

def sa(x, n: int = 2) -> str:
    return "—" if x is None or (isinstance(x, float) and np.isnan(x)) else f"{x:,.{n}f}"

@st.cache_data(show_spinner=False)
def zaman_hazirla(veri: pd.DataFrame) -> pd.DataFrame:
    return compute_zaman_cizelgesi(veri)

def emsal_medyan_tablosu(df_ref: pd.DataFrame) -> pd.DataFrame:
    if not kolon_var(df_ref, "Durum", "KOD & OPE.", "Sure_Saat"):
        return pd.DataFrame(columns=["Emsal_Medyan_Saat", "Emsal_Kayit"])
    u = df_ref[df_ref["Durum"] == "Üretim"].dropna(subset=["KOD & OPE.", "Sure_Saat"])
    sayim = u.groupby("KOD & OPE.").size()
    guvenilir = sayim[sayim >= EMSAL_MIN_TEKRAR].index
    return (u[u["KOD & OPE."].isin(guvenilir)].groupby("KOD & OPE.")["Sure_Saat"]
            .agg(Emsal_Medyan_Saat="median", Emsal_Kayit="size"))

def operator_emsal_kirilimi(df: pd.DataFrame, df_ref: pd.DataFrame, operator_adi: str):
    emsal = emsal_medyan_tablosu(df_ref)
    if emsal.empty or not kolon_var(df, "Durum", "Operator_Kod", "KOD & OPE.", "Sure_Saat"):
        return pd.DataFrame(), None
    u = df[(df["Durum"] == "Üretim") & (df["Operator_Kod"] == operator_adi)].dropna(subset=["KOD & OPE.", "Sure_Saat"])
    u = u[u["KOD & OPE."].isin(emsal.index)].merge(emsal, left_on="KOD & OPE.", right_index=True)
    if u.empty:
        return pd.DataFrame(), None
    u["Sapma"] = np.where(u["Emsal_Medyan_Saat"] > 0, (u["Sure_Saat"] - u["Emsal_Medyan_Saat"]) / u["Emsal_Medyan_Saat"] * 100, 0.0)
    g = u.groupby("KOD & OPE.").agg(
        Kayit=("Sure_Saat", "size"), Ort_Sure=("Sure_Saat", "mean"),
        Emsal_Medyan=("Emsal_Medyan_Saat", "first"), Emsal_Kayit=("Emsal_Kayit", "first"), Ort_Sapma=("Sapma", "mean"),
    ).reset_index().sort_values("Kayit", ascending=False)
    g[["Ort_Sure", "Emsal_Medyan", "Ort_Sapma"]] = g[["Ort_Sure", "Emsal_Medyan", "Ort_Sapma"]].round(2)
    return g, float(u["Sapma"].mean())

def kapsam_anomalileri(df_ham: pd.DataFrame, df: pd.DataFrame, esik: float = KAPSAM_ESIGI) -> pd.DataFrame:
    if not kolon_var(df_ham, "TEZGAH", "TARİH", "Sure_Saat") or df.empty:
        return pd.DataFrame()
    p = df_ham.pivot_table(index="TEZGAH", columns=df_ham["TARİH"].dt.date, values="Sure_Saat", aggfunc="sum", fill_value=0.0)
    tipik = p.median(axis=1)
    gunler, tezgahlar = set(df["TARİH"].dt.date.unique()), set(df["TEZGAH"].dropna().unique())
    satirlar = []
    for t in p.index:
        if t not in tezgahlar:
            continue
        for g in p.columns:
            if g in gunler and p.loc[t, g] < esik * tipik[t]:
                satirlar.append({"Tezgah": t, "Tarih": g, "Kayıtlı Saat": round(float(p.loc[t, g]), 2),
                                 "Tezgahın Tipik Günü (Medyan, Sa)": round(float(tipik[t]), 2),
                                 "Oran (%)": round(float(p.loc[t, g] / tipik[t] * 100), 1) if tipik[t] > 0 else 0.0})
    return pd.DataFrame(satirlar).sort_values("Oran (%)") if satirlar else pd.DataFrame()

def rontgen_satirlari(r: pd.Series, emsal: pd.DataFrame):
    def g(k):
        v = r.get(k)
        return None if v is None or (not isinstance(v, str) and pd.isna(v)) else v
    sure = g("Sure_Saat")
    alanlar = [
        ("Vardiya günü (TARİH)", pd.to_datetime(g("TARİH")).strftime("%d.%m.%Y") if g("TARİH") is not None else "—"),
        ("Vardiya", g("VARDİYA")),
        ("Gerçek başlangıç", pd.to_datetime(g("Baslangic_Zaman")).strftime("%d.%m.%Y %H:%M:%S") if g("Baslangic_Zaman") is not None else "—"),
        ("Gerçek bitiş", pd.to_datetime(g("Bitis_Zaman")).strftime("%d.%m.%Y %H:%M:%S") if g("Bitis_Zaman") is not None else "—"),
        ("Süre", f"{sure:.2f} sa ({sure * 60:.0f} dk)" if sure is not None else "—"),
        ("Tezgah", g("TEZGAH")), ("Operatör", g("Operator_Kod")), ("Parça No", g("PARÇA NO_ÜRETİM_GİRİLEN")),
        ("Operasyon No", g("OPERASYON_NO")), ("Kod & Operasyon", g("KOD & OPE.")), ("Durum", g("Durum")),
        ("Duruş kategorisi", g("Durus_Kategorisi")), ("Duruş sebebi", g("Durus_Sebebi_Ham")),
    ]
    yorum = []
    ct = g("ÜRETİM_C/T")
    if g("Durum") == "Üretim" and ct and sure is not None:
        yon = "hızlı" if sure < ct else "yavaş"
        yorum.append(f"Katalog hedefi **{ct:.2f} sa**, gerçekleşen **{sure:.2f} sa** → hedeften **{abs(sure - ct):.2f} sa ({abs(sure - ct) / ct * 100:.1f}%) {yon}**.")
        kod = g("KOD & OPE.")
        if kod in emsal.index:
            em = float(emsal.loc[kod, "Emsal_Medyan_Saat"])
            yorum.append(f"Aynı kodu işleyen ustaların medyanı **{em:.2f} sa** (n={int(emsal.loc[kod, 'Emsal_Kayit'])}) → bu kayıt emsale göre **{(sure - em) / em * 100:+.1f}%**.")
    if g("Durum") == "Plansız_Duruş":
        yorum.append(f"Plansız duruş: **{g('Durus_Kategorisi') or 'kategori yok'}** — sebep metni: *{g('Durus_Sebebi_Ham') or '—'}*.")
    tut = g("Sure_Tutarsizligi_Saat")
    if tut is not None and abs(tut) > 0.01:
        yorum.append(f"⚠️ Başlangıç–bitiş farkı ile `Sure_Saat` arasında **{tut:+.2f} sa** fark var (mola/kayıt dışı süre olabilir).")
    return alanlar, yorum


# =============================================================================
# SAYFA BAŞLANGICI
# =============================================================================
st.set_page_config(page_title="Üretim Zekası Dashboard", layout="wide", page_icon="🏭")
st.title("🏭 Üretim Zekası ve Karar Destek Dashboard'u")

df_ham, df, kaynak_notu = veri_yukle_ve_filtrele("home")
st.caption(kaynak_notu)

if df.empty:
    st.warning("Seçili filtrelerle eşleşen veri yok.")
    st.stop()

if "Operator_Kod" in df_ham.columns:
    bilinmeyen = sorted(set(df_ham["Operator_Kod"].dropna()) - set(OPERATOR_SOZLUGU))
    if bilinmeyen:
        st.sidebar.warning(f"Sözlükte olmayan operatör kodu (kod olarak gösterilir): {', '.join(bilinmeyen)}")
    df["Operator_Kod"] = df["Operator_Kod"].apply(op_ad)
    df_ham["Operator_Kod"] = df_ham["Operator_Kod"].apply(op_ad)

if "TARİH" in df.columns and df["TARİH"].notna().any():
    st.caption(
        f"📌 Kapsam: **{df['TARİH'].min():%d.%m.%Y} – {df['TARİH'].max():%d.%m.%Y}** · {df['TARİH'].dt.date.nunique()} gün · "
        f"{len(df):,} kayıt · {df['TEZGAH'].nunique()} tezgah · {df['Operator_Kod'].nunique()} operatör"
    )

# ---------------------------------------------------------------- KPI
_dur = kolon_var(df, "Durum", "Sure_Saat")
toplam_uretim = df.loc[df["Durum"] == "Üretim", "Sure_Saat"].sum() if _dur else 0
toplam_durus = df.loc[df["Durum"] == "Plansız_Duruş", "Sure_Saat"].sum() if _dur else 0
toplam_setup = df.loc[df["Durum"] == "Set_Up", "Sure_Saat"].sum() if _dur else 0
toplam_rework = df.loc[df["Durum"] == "Rework", "Sure_Saat"].sum() if _dur else 0

# YENİ SAF OEE HESAPLAMASI
planlanan_yeni_oee_payda = toplam_uretim + toplam_durus + toplam_rework
saf_oee = (toplam_uretim / planlanan_yeni_oee_payda * 100) if planlanan_yeni_oee_payda > 0 else 0

k1, k2, k3, k4, k5 = st.columns(5)
k1.metric("Toplam Üretim Saati", f"{toplam_uretim:,.1f} sa", help="Durum = 'Üretim' olan kayıtların Sure_Saat toplamı")
k2.metric("Toplam Duruş Saati", f"{toplam_durus:,.1f} sa", help="Durum = 'Plansız_Duruş' olan kayıtların Sure_Saat toplamı")
k3.metric("Setup Saati", f"{toplam_setup:,.1f} sa", help="Durum = 'Set_Up' olan kayıtların Sure_Saat toplamı")
k4.metric("Rework Saati", f"{toplam_rework:,.1f} sa", help="Durum = 'Rework' olan kayıtların Sure_Saat toplamı")
k5.metric("Saf Verimlilik (OEE)", f"%{saf_oee:,.1f}", help="Üretim / (Üretim + Plansız Duruş + Rework) × 100")

# ---------------------------------------------------------------- VERİ KAPSAMI VE KALİTE
with st.expander("🔎 Veri Kapsamı ve Kalite Kontrolü — rakamlara ne kadar güvenebiliriz?"):
    z_all = zaman_hazirla(df)
    anomali = kapsam_anomalileri(df_ham, df)
    q1, q2, q3 = st.columns(3)
    q1.metric("Analize giren kayıt", f"{len(df):,}")
    if not z_all.empty and "Sure_Tutarsizligi_Saat" in z_all.columns:
        tut_pay = (z_all["Sure_Tutarsizligi_Saat"].abs() > 0.01).mean() * 100
        q2.metric("Başlangıç–bitiş farkı ≠ Sure_Saat", f"%{tut_pay:.1f}", help="Mola/kayıt dışı süre nedeniyle iki süre alanı uyuşmayan satırların oranı")
    q3.metric("Düşük kapsamlı tezgah-günü", f"{len(anomali)}", help=f"Kayıtlı saat, tezgahın tipik gününün %{KAPSAM_ESIGI * 100:.0f}'inden az")
    st.caption(
        "Bir tezgahın bir günde 3 vardiya boyunca ~18-22 saat kayıt beklenir. Aşağıdaki günlerde kayıt belirgin az: "
        "ilgili vardiyaların Excel'e girilmemiş olması ya da son günün henüz tamamlanmamış olması olası nedenlerdir. "
        "Bu günler üretim/duruş toplamlarını **düşük gösterebilir** — kesin hata değil, kontrol listesidir."
    )
    if anomali.empty:   
        st.success("Seçili kapsamda düşük kapsamlı tezgah-günü yok.")
    else:
        st.dataframe(anomali, use_container_width=True, hide_index=True, height=260)
st.markdown("---")

sekme = dict(zip(SEKMELER, st.tabs(SEKMELER)))

# =============================================================================
# SEKME 1: TEZGAH ÖZETİ
# =============================================================================
with sekme[T_TEZGAH]:
    tezgah_ozet = compute_tezgah_oee(df)
    if tezgah_ozet.empty:
        st.info("Veri yok.")
    else:
        g = tezgah_ozet.copy()
        
        # Tezgah bazında Saf OEE hesaplaması
        g["Saf_OEE"] = np.where(
            (g["Uretim_Saat"] + g["Plansiz_Durus_Saat"] + g["Rework_Saat"]) > 0,
            (g["Uretim_Saat"] / (g["Uretim_Saat"] + g["Plansiz_Durus_Saat"] + g["Rework_Saat"]) * 100).round(1),
            0
        )
        
        # Kullanılabilirlik, Kalite Oranı, Performans Oranı ve eski OEE'leri siliyoruz
        gizli_kolonlar = ["Kalite_Orani", "OEE_Yaklasik", "Performans_Orani", "Gercek_OEE", "Kullanilabilirlik", "Planlanan_Sure", "Kayit_Sayisi"]
        g = g.drop(columns=gizli_kolonlar, errors="ignore")

        oee_baslik = "OEE = Üretim / (Üretim + Plansız Duruş + Rework)"

        c1, c2 = st.columns([1, 2])
        with c1:
            durum_toplam = df.groupby("Durum")["Sure_Saat"].sum().reset_index()
            fig_pie = px.pie(durum_toplam, values="Sure_Saat", names="Durum", title="Fabrika Geneli Zaman Dağılımı", color="Durum",
                             color_discrete_map={k: RENK.get(k, "#BDBDBD") for k in durum_toplam["Durum"]}, hole=0.4, height=450)
            fig_pie.update_traces(textposition="inside", textinfo="percent+label", textfont_size=14)
            st.plotly_chart(fig_pie, use_container_width=True)
        with c2:
            sirali = g.sort_values("Saf_OEE", ascending=True)
            max_deger = float(sirali["Saf_OEE"].max())
            fig_oee = px.bar(sirali, x="Saf_OEE", y="TEZGAH", orientation="h", text_auto=".1f", color="Saf_OEE",
                             color_continuous_scale="RdYlGn", range_color=[50, 100], height=480, title=f"Tezgah Saf Verimlilik Skorları — {oee_baslik}")
            fig_oee.add_vline(x=OEE_REFERANS, line_dash="dash", line_color="red", annotation_text=f"Hedef %{OEE_REFERANS}", annotation_font_size=12)
            fig_oee.update_layout(xaxis_title="OEE (%)", yaxis_title="Tezgahlar", xaxis=dict(range=[0, max(105, max_deger + 8)]), font=dict(size=14))
            st.plotly_chart(fig_oee, use_container_width=True)

        st.markdown("##### Tezgah Başına Detaylı Tablo")
        gost = g.rename(columns={
            "Uretim_Saat": "Üretim (Sa)", "Plansiz_Durus_Saat": "Plansız Duruş (Sa)", "Set_Up_Saat": "Set-Up (Sa)", "Yemek_Saat": "Yemek Mola (Sa)",
            "Rework_Saat": "Rework (Sa)", "Saf_OEE": "OEE (%)", "En_Buyuk_Kayip_Kategorileri": "En Büyük Kayıp Kategorileri",
        })
        
        yuzde_cfg = {c: st.column_config.NumberColumn(format="%.1f%%") for c in gost.columns if c.endswith("(%)")}
        st.dataframe(gost, use_container_width=True, hide_index=True, column_config=yuzde_cfg)

        with st.expander("Tezgah bazında saat dağılımı (yığılmış çubuk)"):
            uzun = g.melt(id_vars="TEZGAH", value_vars=["Uretim_Saat", "Plansiz_Durus_Saat", "Set_Up_Saat", "Yemek_Saat", "Rework_Saat"], var_name="Kod", value_name="Saat")
            uzun["Durum"] = uzun["Kod"].map({"Uretim_Saat": "Üretim", "Plansiz_Durus_Saat": "Plansız_Duruş", "Set_Up_Saat": "Set_Up", "Yemek_Saat": "Yemek_Mola", "Rework_Saat": "Rework"})
            st.plotly_chart(px.bar(uzun, x="TEZGAH", y="Saat", color="Durum", color_discrete_map=RENK, barmode="stack", height=420), use_container_width=True)

# =============================================================================
# SEKME 2: DURUŞ PARETO
# =============================================================================
with sekme[T_PARETO]:
    pareto = compute_pareto(df)
    rework_adet = int((df["Durum"] == "Rework").sum()) if "Durum" in df.columns else 0
    rework_saat = float(df.loc[df["Durum"] == "Rework", "Sure_Saat"].sum()) if rework_adet else 0.0
    rw_ekle = st.checkbox("Rework (tekrar işleme) süresini de kayıp kategorisi olarak ekle", value=False, key="pareto_rw",
                          help="Sunumdaki kayıp Pareto'su Rework'ü ayrı bir kategori olarak sayar. Varsayılan: yalnızca plansız duruşlar.")
    if rw_ekle and rework_adet > 0:
        ek = pd.DataFrame([{"Durus_Kategorisi": "Tekrar / Kalite (Rework)", "Toplam_Saat": round(rework_saat, 2), "Adet": rework_adet}])
        pareto = pd.concat([pareto, ek], ignore_index=True) if not pareto.empty else ek
        pareto = pareto.sort_values("Toplam_Saat", ascending=False).reset_index(drop=True)
        pareto["Kumulatif_%"] = (pareto["Toplam_Saat"].cumsum() / pareto["Toplam_Saat"].sum() * 100).round(1)

    if pareto.empty:
        st.info("Plansız duruş kaydı yok.")
    else:
        toplam_kayip = float(pareto["Toplam_Saat"].sum())
        en_buyuk = pareto.iloc[0]
        p1, p2, p3, p4 = st.columns(4)
        p1.metric("Toplam Kayıp", f"{toplam_kayip:,.1f} sa")
        p2.metric("Kategori Sayısı", f"{len(pareto)}")
        p3.metric("En Büyük Kategori", f"{en_buyuk['Durus_Kategorisi']}", delta=f"%{en_buyuk['Toplam_Saat'] / toplam_kayip * 100:.1f} pay", delta_color="off")
        p4.metric("İlk 2 Kategori Payı", f"%{pareto['Kumulatif_%'].iloc[min(1, len(pareto) - 1)]:.1f}", help="Pareto (80/20) kuralı: az sayıda sebep, kaybın çoğunu oluşturur")

        fig = go.Figure()
        fig.add_bar(x=pareto["Durus_Kategorisi"], y=pareto["Toplam_Saat"], marker_color="#C62828", name="Toplam Saat",
                    text=pareto["Toplam_Saat"].round(1), textposition="outside")
        fig.add_trace(go.Scatter(x=pareto["Durus_Kategorisi"], y=pareto["Kumulatif_%"], yaxis="y2", mode="lines+markers", name="Kümülatif %", line=dict(color="#1565C0")))
        fig.update_layout(title="Duruş Sebepleri Pareto", yaxis_title="Saat", yaxis2=dict(overlaying="y", side="right", range=[0, 105], title="Kümülatif %"),
                          height=520, font=dict(size=14))
        st.plotly_chart(fig, use_container_width=True)

        c1, c2 = st.columns([1, 2])
        with c1:
            st.subheader("Duruş Özeti")
            st.dataframe(pareto.rename(columns={"Durus_Kategorisi": "Duruş Kategorisi", "Toplam_Saat": "Toplam Saat", "Kumulatif_%": "Kümülatif %"}),
                         use_container_width=True, hide_index=True)
        with c2:
            st.subheader("Olay Bazlı Detaylı Duruş/Rework Kayıtları")
            st.caption("Kim, hangi parçada, ne kadar süre durdu? (Süreye göre büyükten küçüğe)")
            d = df[df["Durum"].isin(["Plansız_Duruş", "Rework"])].copy()
            if d.empty:
                st.info("Detaylı duruş kaydı bulunamadı.")
            else:
                kolonlar = [("TARİH", "Tarih"), ("BAŞLANGIÇ_SAAT", "Başlangıç"), ("TEZGAH", "Tezgah"), ("Operator_Kod", "Operatör"),
                            ("PARÇA NO_ÜRETİM_GİRİLEN", "Parça No"), ("Durum", "Durum"), ("Durus_Kategorisi", "Kategori"),
                            ("Durus_Sebebi_Ham", "Açıklama (Sebep)"), ("Sure_Saat", "Süre (Saat)")]
                mevcut = [(k, v) for k, v in kolonlar if k in d.columns]
                dd = d.sort_values("Sure_Saat", ascending=False)[[k for k, _ in mevcut]].rename(columns=dict(mevcut))
                if "Tarih" in dd.columns:
                    dd["Tarih"] = pd.to_datetime(dd["Tarih"]).dt.date
                dd["Süre (Saat)"] = pd.to_numeric(dd["Süre (Saat)"], errors="coerce").round(2)
                for c in dd.columns:
                    if dd[c].dtype == object or str(dd[c].dtype).startswith("str"):
                        dd[c] = dd[c].fillna("-")
                st.dataframe(dd, use_container_width=True, hide_index=True,
                             column_config={"Süre (Saat)": st.column_config.NumberColumn(format="%.2f"), "Tarih": st.column_config.DateColumn(format="DD.MM.YYYY")})
                st.caption(f"{len(dd):,} kayıt · toplam {dd['Süre (Saat)'].sum():,.2f} sa")

# =============================================================================
# SEKME 3: OPERATÖR KARNESİ
# =============================================================================
with sekme[T_OPERATOR]:
    
    # ÇÖP VERİ FİLTRESİ: 0.05 saatten küçük veya hedefin %10'undan küçük çöp verileri Emsal/Ortalama hesaplarından dışla
    if "ÜRETİM_C/T" in df.columns:
        df_temiz_karne = df[~((df["Durum"] == "Üretim") & ((df["Sure_Saat"] < 0.05) | (df["Sure_Saat"] < (df["ÜRETİM_C/T"] * 0.10))))].copy()
    else:
        df_temiz_karne = df[~((df["Durum"] == "Üretim") & (df["Sure_Saat"] < 0.05))].copy()

    karne = compute_operator_karne(df_temiz_karne, emsal_referans_df=df_ham)
    
    if karne.empty:
        st.info("Yeterli veri yok (min. saat eşiğini geçen operatör bulunamadı).")
    else:
        esik, gun = karne.attrs.get("efektif_esik_saat"), karne.attrs.get("gun_sayisi")
        uretimde_olan = df.loc[df["Durum"] == "Üretim", "Operator_Kod"].nunique()
        st.caption(
            f"ℹ️ {len(karne)} operatör karnede (eşik: en az **{esik:g} sa** üretim — {gun} günlük aralık için) · "
            f"eşik altında kalan: {max(uretimde_olan - len(karne), 0)} · emsal referansı: **tüm veri**, en az {EMSAL_MIN_TEKRAR} tekrarlı kodlar."
        )
        
        # Gereksiz kolonları siliyoruz: İslem_Sayisi, Islem_Saat_Hizi, CT_Dusuk_Guven_Satir_Sayisi, CT_Dusuk_Guven_Yuzde
        kg = karne.rename(columns={
            "Operator_Kod": "Operatör", "Toplam_Saat": "Toplam Saat",
            "CT_Bazli_Sapma_Yuzde": "CT Sapması (%)", "Emsal_Bazli_Sapma_Yuzde": "Emsal Sapması (%)",
        })
        kg = kg[["Operatör", "Toplam Saat", "CT Sapması (%)", "Emsal Sapması (%)"]]

        if "Emsal Sapması (%)" in kg.columns:
            ks = kg.sort_values("Emsal Sapması (%)", ascending=False)
            fig_op = px.bar(ks, x="Emsal Sapması (%)", y="Operatör", orientation="h", title="Operatör Hız Kıyaslaması (Emsal Medyanına Göre)",
                            text_auto=".1f", color="Emsal Sapması (%)", color_continuous_scale="RdYlGn_r", height=max(450, 28 * len(ks) + 120))
            fig_op.add_vline(x=0, line_dash="solid", line_color="black", annotation_text="Emsal Medyanı ", annotation_font_size=12)
            fig_op.update_layout(xaxis_title="Emsal Sapması (%) [Negatif = emsallerinden hızlı]", yaxis_title="Operatörler", font=dict(size=14),
                                 yaxis=dict(autorange="reversed"))
            st.plotly_chart(fig_op, use_container_width=True)
            
        st.dataframe(kg, use_container_width=True, hide_index=True, column_config={
            "CT Sapması (%)": st.column_config.NumberColumn(format="%.1f%%"), 
            "Emsal Sapması (%)": st.column_config.NumberColumn(format="%.1f%%"),
            "Toplam Saat": st.column_config.NumberColumn(format="%.2f")})

        st.markdown("#### 🔍 Bir operatörün emsal sapması nereden geliyor?")
        st.markdown("""
        Bu tablo, operatörün hızının (emsal sapmasının) hangi parçalarda ne kadar olduğunu gösterir. Hesaplama tüm veritabanı geçmişi taranarak yapılır:
        - **Kayıt (n):** Seçili operatörün belirlediğiniz tarih aralığında bu parçayı kaç defa işlediğidir.
        - **Emsal Kayıt (n):** Fabrikadaki *tüm operatörlerin* geçmişten bugüne bu parçayı toplamda kaç kere işlediğidir. (Referans hedefi bu dev havuzdan hesaplanır, böylece tek bir kişinin hatası genel hedefi bozamaz).
        - **Hesaplama Formülü:** `(Operatörün Ort. Süresi - Emsal Medyan Süre) / Emsal Medyan Süre × 100`. Eksi değerler ustayı emsallerinden hızlı yapar.
        """)
        
        secilen_op = st.selectbox("Operatör seç", kg["Operatör"].tolist(), key="op_drill")
        kir, genel = operator_emsal_kirilimi(df, df_ham, secilen_op)
        if kir.empty:
            st.info("Bu operatör için emsal kıyaslanabilir kod bulunamadı.")
        else:
            st.dataframe(kir.rename(columns={"KOD & OPE.": "Kod & Operasyon", "Kayit": "Kayıt (n)", "Ort_Sure": "Ort. Süre (Sa)", "Emsal_Medyan": "Emsal Medyanı (Sa)",
                                             "Emsal_Kayit": "Emsal Kayıt (n)", "Ort_Sapma": "Ort. Sapma (%)"}),
                         use_container_width=True, hide_index=True, column_config={"Ort. Sapma (%)": st.column_config.NumberColumn(format="%+.1f%%")})
            karnedeki = float(kg.loc[kg["Operatör"] == secilen_op, "Emsal Sapması (%)"].iloc[0])
            uyum = "✅ birebir eşleşiyor" if abs(genel - karnedeki) < 0.06 else "⚠️ fark var"
            st.caption(f"**{secilen_op}** — bu tablodaki tüm işlem satırlarının ortalama emsal sapması: **{genel:+.1f}%** · karnedeki değer: **{karnedeki:+.1f}%** → {uyum}. ")

# =============================================================================
# SEKME 4: KRONOLOJİK ÜRETİM DÖKÜMÜ
# =============================================================================
with sekme[T_KRONO]:
    st.subheader("📅 Detaylı Kronolojik Üretim Dökümü")
    st.caption("Fabrikadaki her kayıt, gerçek zaman sırasıyla. Tabloda bir satıra tıklayarak o kaydın **röntgenini** görebilirsiniz.")

    if "TARİH" in df.columns and "Sure_Saat" in df.columns:
        gunluk = df.groupby(["TARİH", "Durum"])["Sure_Saat"].sum().reset_index()
        fig_zaman = px.bar(gunluk, x="TARİH", y="Sure_Saat", color="Durum", title="Günlük Fabrika Çalışma ve Duruş Trendi", barmode="stack", height=500,
                           color_discrete_map={k: RENK.get(k, "#BDBDBD") for k in gunluk["Durum"].unique()})
        fig_zaman.update_layout(xaxis_title="Tarih (vardiya günü)", yaxis_title="Toplam Süre (Saat)", font=dict(size=14))
        st.plotly_chart(fig_zaman, use_container_width=True)

    z = zaman_hazirla(df)
    if z.empty:
        st.warning("Gerçek zaman damgaları hesaplanamadı (BAŞLANGIÇ_SAAT/VARDİYA eksik) — TARİH ve saat metnine göre sıralanıyor; gece vardiyası sırası hatalı olabilir.")
        z = df.sort_values([c for c in ["TARİH", "BAŞLANGIÇ_SAAT"] if c in df.columns]).reset_index(drop=True)
    else:
        z = z.sort_values(["Baslangic_Zaman", "TEZGAH"]).reset_index(drop=True)
    for k, _ in KRONO_KOLONLAR:
        if k not in z.columns:
            z[k] = None

    f1, f2, f3, f4 = st.columns(4)
    durumlar = sorted(z["Durum"].dropna().unique(), key=lambda d: DURUM_SIRASI.index(d) if d in DURUM_SIRASI else 99)
    sec_durum = f1.multiselect("Durum", durumlar, default=durumlar, key="krono_durum")
    tezgahlar = sorted(z["TEZGAH"].dropna().unique())
    sec_tezgah = f2.multiselect("Tezgah", tezgahlar, default=tezgahlar, key="krono_tezgah")
    operatorler = sorted(z["Operator_Kod"].dropna().unique())
    sec_op = f3.multiselect("Operatör", operatorler, default=operatorler, key="krono_op")
    arama = f4.text_input("Parça / kod ara", key="krono_ara", placeholder="örn. M14P04")

    maske = z["Durum"].isin(sec_durum) & z["TEZGAH"].isin(sec_tezgah) & z["Operator_Kod"].isin(sec_op)
    if arama:
        a = arama.strip().lower()
        maske &= (z["KOD & OPE."].astype(str).str.lower().str.contains(a, regex=False)
                  | z["PARÇA NO_ÜRETİM_GİRİLEN"].astype(str).str.lower().str.contains(a, regex=False))
    gorunum = z[maske].reset_index(drop=True)

    tablo = gorunum[[k for k, _ in KRONO_KOLONLAR]].rename(columns=dict(KRONO_KOLONLAR))
    tablo["Tarih"] = pd.to_datetime(tablo["Tarih"]).dt.date
    for c in ["Gerçekleşen Süre (Saat)", "Hedef C/T (Saat)", "Sapma (Saat)", "Sapma (%)"]:
        tablo[c] = pd.to_numeric(tablo[c], errors="coerce")
    for c in ["Vardiya", "Başlangıç Saati", "Bitiş Saati", "Tezgah", "Operatör (Gerçek İsim)", "Parça No", "Operasyon No", "Durum", "Duruş Kategorisi", "Duruş Sebebi"]:
        tablo[c] = tablo[c].astype("object").where(tablo[c].notna(), "-").astype(str)
    cfg = {"Tarih": st.column_config.DateColumn(format="DD.MM.YYYY"),
           "Gerçekleşen Süre (Saat)": st.column_config.NumberColumn(format="%.2f"), "Hedef C/T (Saat)": st.column_config.NumberColumn(format="%.2f"),
           "Sapma (Saat)": st.column_config.NumberColumn(format="%+.2f"), "Sapma (%)": st.column_config.NumberColumn(format="%+.1f%%")}

    st.caption(f"**{len(tablo):,}** kayıt · toplam **{tablo['Gerçekleşen Süre (Saat)'].sum():,.2f} sa**. "
               "*Tarih = vardiyanın başladığı gün; gece vardiyasında gece yarısından sonraki kayıtlar da aynı vardiya gününe yazılır (gerçek saat röntgende).*")
    secili = []
    try:
        olay = st.dataframe(tablo, use_container_width=True, hide_index=True, height=520, column_config=cfg, on_select="rerun", selection_mode="single-row", key="krono_tablo")
        secili = list(olay.selection.rows)
    except TypeError:   
        st.dataframe(tablo, use_container_width=True, hide_index=True, height=520, column_config=cfg)
        st.caption("ℹ️ Satır seçimi (röntgen) için Streamlit ≥ 1.35 gerekir.")
    st.download_button("⬇️ Tabloyu CSV indir", tablo.to_csv(index=False).encode("utf-8-sig"), "kronolojik_uretim_dokumu.csv", "text/csv")

    if len(tablo) == len(z):   
        fark = abs(tablo["Gerçekleşen Süre (Saat)"].sum() - df["Sure_Saat"].sum())
        st.caption("✅ Mutabakat: tablodaki toplam süre, KPI/analizlerdeki toplam süreyle aynı." if fark < 0.5 else f"⚠️ Mutabakat farkı: {fark:.2f} sa (yuvarlama sınırını aşıyor).")

    if secili:
        satir = gorunum.iloc[secili[0]]
        alanlar, yorumlar = rontgen_satirlari(satir, emsal_medyan_tablosu(df_ham))
        st.markdown("#### 🩻 Kayıt Röntgeni")
        r1, r2 = st.columns([1, 1])
        with r1:
            st.dataframe(pd.DataFrame([(a, "-" if v is None else str(v)) for a, v in alanlar], columns=["Alan", "Değer"]), hide_index=True, use_container_width=True)
        with r2:
            for y in yorumlar:
                st.markdown(f"- {y}")
            if not yorumlar:
                st.caption("Bu kayıt türü için ek yorum yok.")


# =============================================================================
# SEKME 5: METRİKLER VE FORMÜLLER (ŞEFFAFLIK)
# =============================================================================
with sekme[T_FORMUL]:
    st.subheader("📐 Hesaplama Mantığı, Formüller ve Şeffaflık")
    st.markdown("Ekrandaki hiçbir sayı tahmin değildir: hepsi aşağıdaki formüllerle, veritabanındaki belirtilen kolonlardan hesaplanır. "
                "İptal edilen kafa karıştırıcı metrikler bu ekrandan kaldırılmıştır.")

    def bolum(baslik, aciklama, latex, kod, kolonlar, canli=None):
        with st.expander(baslik):
            st.markdown(aciklama)
            st.latex(latex)
            st.markdown("**Python karşılığı:**")
            st.code(kod, language="python")
            st.markdown("**Kullanılan kolonlar:** " + ", ".join(f"`{k}`" for k in kolonlar))
            if canli:
                st.markdown("**🔢 Canlı örnek (şu an ekrandaki veri):**")
                st.markdown(canli)

    st.markdown("### 📊 Ana Verimlilik Formülü (OEE)")
    ex = tezgah_ozet.sort_values("Uretim_Saat").iloc[-1] if not tezgah_ozet.empty else None
    can_oee = None
    if ex is not None:
        U, P, R = ex["Uretim_Saat"], ex["Plansiz_Durus_Saat"], ex["Rework_Saat"]
        can_oee = (f"**{ex['TEZGAH']}** tezgahı: Üretim = {U:.2f} sa, Plansız Duruş = {P:.2f} sa, Rework = {R:.2f} sa → "
                   f"{U:.2f} / ({U:.2f} + {P:.2f} + {R:.2f}) × 100 = **%{(U) / (U + P + R) * 100:.1f}**" if (U + P + R) > 0 else None)

    bolum("1️⃣ Saf Verimlilik (OEE)",
          "Performans (Hız) ve Kalite çarpanlarını içermeyen, makinenin planlanan çalışma süresinin ne kadarını **sadece ürün üreterek** geçirdiğini ölçen net formüldür.",
          r"\text{OEE} = \frac{\text{Üretim\_Saat}}{\text{Üretim\_Saat} + \text{Plansiz\_Durus\_Saat} + \text{Rework\_Saat}} \times 100",
          'planlanan = Uretim_Saat + Plansiz_Durus_Saat + Rework_Saat\nOEE = (Uretim_Saat / planlanan) * 100   # planlanan > 0 ise',
          ["Durum", "Sure_Saat", "TEZGAH"], can_oee)

    st.markdown("### 👤 Operatör Karnesi Formülleri")
    bolum("2️⃣ CT Sapması (%)",
          "Bir işlemin katalog hedefine göre yüzde kaç saptığı. Eksi (−) hedeften hızlı, artı (+) yavaş demektir.",
          r"\text{Sapma\_Yuzdesi} = \frac{\text{Sure\_Saat} - \text{ÜRETİM\_C/T}}{\text{ÜRETİM\_C/T}} \times 100",
          'df["Sapma_Yuzdesi"] = (df["Sure_Saat"] - df["ÜRETİM_C/T"]) / df["ÜRETİM_C/T"] * 100',
          ["Sure_Saat", "ÜRETİM_C/T", "Durum", "Operator_Kod"], None)

    bolum("3️⃣ Emsal Sapması (%)",
          f"Katalog güvenilmezse, operatörü **aynı işi yapan diğer ustalara** kıyaslar. Emsal medyanı, o kodu işleyen **tüm** operatörlerin medyan süresidir.",
          r"\text{Emsal\_Sapma} = \frac{\text{Sure\_Saat} - \text{Emsal\_Medyan\_Saat}}{\text{Emsal\_Medyan\_Saat}} \times 100",
          'emsal_medyan = df.groupby("KOD & OPE.")["Sure_Saat"].median()\nEmsal_Sapma = (Sure_Saat - emsal_medyan) / emsal_medyan * 100',
          ["Sure_Saat", "KOD & OPE.", "Durum", "Operator_Kod"], None)

with sekme[T_SN]:
    render_sn_sekmesi(df)
# ====================================================================================
# GEÇİCİ DEVRE DIŞI BIRAKILAN MODÜLLER (Patron talebi üzerine ekrandan kaldırıldı)
# İleride açmak için 'if False:' kısımlarını 'if True:' yapıp 'SEKMELER' listesine ekleyin.
# ====================================================================================

if False: # with sekme[T_KAPASITE]:
    st.subheader("💰 Operatöre Ödenen Saat, Sistemde Nereye Gidiyor?")
    kap = compute_operator_kapasite(df)
    if not kap.empty:
        st.dataframe(kap, use_container_width=True, hide_index=True)

if False: # with sekme[T_CT]:
    st.subheader("⚠️ Katalog C/T (Cycle Time) Analizi ve Kanıtlar")
    ct = compute_ct_guvenilirlik(df)
    kanit = ct_kanit_hesapla(df)
    if ct.get("gecerli") and kanit.get("gecerli"):
        st.dataframe(kanit["tablo"], use_container_width=True, hide_index=True)

# ====================================================================================
# AŞAĞIDAKİ KODLAR GİZLENMİŞ MODÜLLERDİR (SİLİNMEMİŞTİR)
# ====================================================================================
#
# --- GİZLENEN SEKME: PARÇA-TEZGAH ISI HARİTASI ---
# with sekme["🌡️ Parça-Tezgah Isı Haritası"]:
#     heat = compute_heatmap(df)
#     if heat.empty:
#         st.info("Yeterli veri yok.")
#     else:
#         fig = px.imshow(heat, aspect="auto", color_continuous_scale="Reds", labels=dict(x="Tezgah", y="Parça No", color="Saat"))
#         st.plotly_chart(fig, use_container_width=True)
#
# --- GİZLENEN SEKME: VARDİYA YORGUNLUK ---
# with sekme["🌙 Vardiya Yorgunluk"]:
#     vy = compute_vardiya_yorgunluk(df)
#     if vy.empty:
#         st.info("Vardiya_Ici_Pozisyon_Yuzde hesaplanamadı (BAŞLANGIÇ_SAAT/VARDİYA eksik olabilir).")
#     else:
#         fig = px.bar(vy, x="Dilim", y="Hata_Orani_%", color="Hata_Orani_%", color_continuous_scale="Reds", title="Vardiya İçi Çeyreğe Göre Hata Oranı")
#         st.plotly_chart(fig, use_container_width=True)
#         st.dataframe(vy, use_container_width=True, hide_index=True)
#
# --- GİZLENEN SEKME: MTBF & MTTR ---
# with sekme["🔧 MTBF & MTTR"]:
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