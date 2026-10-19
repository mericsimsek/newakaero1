# -*- coding: utf-8 -*-
from datetime import timedelta

import pandas as pd
import streamlit as st

from uretim_akisi import (sn_sayilari, durum_saatleri, donem_sec, tezgah_listesi,
                          olay_gunlugu,parca_durumu_goster, kart_satiri, _GUN_KISA)

try:
    import plotly.graph_objects as go
except ImportError:
    go = None

RENK = {"Üretim": "#2E7D32", "Plansız_Duruş": "#C62828", "Set_Up": "#F9A825", "Rework": "#EF6C00"}
ETIKET = {"Üretim": "Net Üretim", "Plansız_Duruş": "Plansız Duruş", "Set_Up": "Set-up", "Rework": "Rework"}


def _grafik(p, baslik):
    if go is None:
        st.bar_chart(p.rename(columns=ETIKET))
        return
    fig = go.Figure()
    for d in RENK:
        fig.add_bar(name=ETIKET[d], x=p.index.tolist(), y=p[d].round(2).tolist(), marker_color=RENK[d])
    fig.update_layout(barmode="stack", title=baslik, height=400, margin=dict(l=10, r=10, t=50, b=10),
                      legend=dict(orientation="h", y=-0.25), yaxis_title="saat")
    st.plotly_chart(fig, use_container_width=True)

def render(T, c_bitti):
    if T.empty or T["Tarih"].notna().sum() == 0:
        st.warning("Gösterilecek veri yok.")
        return
    mx = T["Tarih"].max().date()
    secim = donem_sec(T, "yon")
    if secim is None:
        return
    d_bas, d_bit, n = secim
    vard = st.radio("🕒 Vardiya", ["Tümü"] + sorted(v for v in T["Vardiya"].unique() if v != "-"),
                    horizontal=True, key="yon_vardiya")
    tum_tez = tezgah_listesi(T)

    def dilim(a, b):
        x = T[(T["Tarih"] >= pd.Timestamp(a)) & (T["Tarih"] <= pd.Timestamp(b))]
        return x if vard == "Tümü" else x[x["Vardiya"] == vard]

    G = dilim(d_bas, d_bit)
    if G.empty:
        st.info("Bu dönemde kayıt yok.")
        return

    # önceki dönem: aynı gün sayısı kadar
    uz = max((min(d_bit, mx) - d_bas).days + 1, 1)
    p_bas = d_bas - timedelta(days=n)
    P = dilim(p_bas, p_bas + timedelta(days=uz - 1))
    onceki = durum_saatleri(P) if not P.empty else None
    onceki_biten = sn_sayilari(P)[1] if (c_bitti and not P.empty) else None

    kart_satiri(G, c_bitti, onceki, onceki_biten)
    st.caption("Fark oku: " + ("önceki gün" if n == 1 else "önceki haftanın aynı günleri" if n == 7 else "önceki dönem")
               + " ile kıyas.")
    parca_durumu_goster(G, c_bitti)

    # --- Tek grafik ---
    gorunum = "🏭 Tezgah Bazlı"
    if d_bas != d_bit:
        gorunum = st.radio("Grafik", ["🏭 Tezgah Bazlı", "📅 Gün Bazlı"], horizontal=True,
                           key="yon_grafik", label_visibility="collapsed")
    if gorunum.startswith("📅"):
        p_ = (G.pivot_table(index="Tarih", columns="Durum", values="Süre", aggfunc="sum", fill_value=0)
                .reindex(columns=list(RENK), fill_value=0)
                .reindex(pd.date_range(d_bas, d_bit), fill_value=0))
        p_.index = [f"G{d.weekday() + 1} {_GUN_KISA[d.weekday()]} {d:%d.%m}" for d in p_.index]
        _grafik(p_, "Gün Bazlı Çalışma ve Kayıplar (saat)")
    else:
        p_ = (G[G["Tezgah"] != "-"].pivot_table(index="Tezgah", columns="Durum", values="Süre",
                                                aggfunc="sum", fill_value=0)
              .reindex(columns=list(RENK), fill_value=0))
        p_ = p_.reindex(sorted(set(tum_tez) | set(p_.index)), fill_value=0)
        _grafik(p_, "Tezgah Bazlı Çalışma ve Kayıplar (saat)")

    # --- Tek tablo: dikkat edilecekler (en uzun olay üstte, pasif tezgah altta) ---
    st.markdown("### ⚠️ Dikkat Edilecekler")
    log = olay_gunlugu(G, tum_tez, d_bas, d_bit)
    if log.empty:
        st.success("Planlanmayan olay yok ✅")
    else:
        log = log.sort_values("Süre (sa)", ascending=False, na_position="last")
        st.caption("Duruş, set-up ve rework olayları — en uzun olan üstte. Pasif tezgahlar en altta.")
        st.dataframe(log, hide_index=True, use_container_width=True, height=min(60 + 35 * len(log), 420),
                     column_config={"Süre (sa)": st.column_config.NumberColumn(format="%.2f")})