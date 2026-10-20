# -*- coding: utf-8 -*-
"""stil.py — ortak görünüm (CSS + küçük HTML bileşenleri). Açık ve koyu temada çalışır."""
from __future__ import annotations

from html import escape as _e

import pandas as pd
import streamlit as st

_CSS = """
.block-container{padding-top:1.6rem;padding-bottom:3rem;max-width:1600px}
footer{visibility:hidden}
hr{margin:1.2rem 0;opacity:.3}

/* --- sayfa başlığı --- */
.ph{margin:0 0 .4rem 0}
.ph-t{font-size:2rem;font-weight:800;line-height:1.15;letter-spacing:-.01em}
.ph-s{opacity:.65;font-size:.95rem;margin-top:.2rem}

/* --- bölüm başlığı --- */
.sec{display:flex;align-items:baseline;gap:.7rem;margin:1.6rem 0 .7rem 0;padding-left:.7rem;
     border-left:4px solid #3b82f6;font-size:1.15rem;font-weight:700}
.sec span{font-size:.85rem;font-weight:500;opacity:.6}

/* --- KPI kartları --- */
.kpi-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(175px,1fr));gap:12px;margin:.2rem 0 .4rem 0}
.kpi{position:relative;padding:14px 16px 12px 20px;border-radius:14px;overflow:hidden;
     background:rgba(128,128,128,.07);border:1px solid rgba(128,128,128,.28)}
.kpi::before{content:"";position:absolute;left:0;top:0;bottom:0;width:5px;background:var(--ac,#94a3b8)}
.kpi-l{font-size:.82rem;font-weight:600;opacity:.72}
.kpi-v{font-size:2.05rem;font-weight:800;line-height:1.15;margin:.15rem 0 .1rem 0;font-variant-numeric:tabular-nums}
.kpi-s{font-size:.76rem;opacity:.55}
.kpi.mavi{--ac:#3b82f6}.kpi.yesil{--ac:#22c55e}.kpi.kirmizi{--ac:#ef4444}
.kpi.sari{--ac:#f59e0b}.kpi.mor{--ac:#a855f7}.kpi.gri{--ac:#94a3b8}
.kpi.sifir .kpi-v{opacity:.3}

/* --- çipler / filtre şeridi --- */
.chips{display:flex;flex-wrap:wrap;gap:6px;align-items:center}
.chip{display:inline-block;padding:3px 11px;border-radius:999px;font-size:.8rem;
      background:rgba(59,130,246,.13);border:1px solid rgba(59,130,246,.38)}
.chip b{font-weight:700;margin-right:.35rem}
.chip.yesil{background:rgba(34,197,94,.13);border-color:rgba(34,197,94,.42)}
.fbar{display:flex;justify-content:space-between;align-items:center;gap:1rem;flex-wrap:wrap;
      margin:.5rem 0 .3rem 0;padding:8px 12px;border-radius:12px;
      background:rgba(128,128,128,.06);border:1px dashed rgba(128,128,128,.35)}
.fbar .muted{opacity:.6;font-size:.85rem}
.fbar .sayac{font-size:.85rem;opacity:.8;white-space:nowrap}

/* --- Streamlit bileşenleri --- */
[data-testid="stExpander"]{border:1px solid rgba(128,128,128,.28);border-radius:12px;overflow:hidden}
[data-testid="stExpander"] summary{font-weight:600}
[data-testid="stMetric"]{background:rgba(128,128,128,.07);border:1px solid rgba(128,128,128,.28);
                         border-radius:14px;padding:12px 16px}
[data-testid="stMetricLabel"]{opacity:.7}
[data-testid="stMetricValue"]{font-weight:800}
[data-testid="stDataFrame"]{border:1px solid rgba(128,128,128,.28);border-radius:12px;overflow:hidden}
[data-testid="stAlert"]{border-radius:12px}
[data-testid="stChatMessage"]{border:1px solid rgba(128,128,128,.2);border-radius:14px;padding:.75rem 1rem}
.stButton>button{border-radius:10px;text-align:left}
"""

_SIFIRLAR = ("0", "0.0", "0.00", "—")


def stil_uygula() -> None:
    """Sayfanın en başında (st.set_page_config'ten sonra) bir kez çağır."""
    st.markdown(f"<style>{_CSS}</style>", unsafe_allow_html=True)


def baslik_html(baslik: str, alt: str = "") -> str:
    return f'<div class="ph"><div class="ph-t">{_e(baslik)}</div><div class="ph-s">{_e(alt)}</div></div>'


def bolum_html(baslik: str, aciklama: str = "") -> str:
    return f'<div class="sec">{_e(baslik)}<span>{_e(aciklama)}</span></div>'


def kpi_html(kartlar) -> str:
    """kartlar: [(etiket, değer, alt_yazı, ton)]; ton: mavi | yesil | kirmizi | sari | mor | gri"""
    parca = []
    for etiket, deger, alt, ton in kartlar:
        sifir = " sifir" if str(deger).strip().split(" ")[0] in _SIFIRLAR else ""
        parca.append(f'<div class="kpi {ton}{sifir}"><div class="kpi-l">{_e(str(etiket))}</div>'
                     f'<div class="kpi-v">{_e(str(deger))}</div><div class="kpi-s">{_e(str(alt))}</div></div>')
    return '<div class="kpi-grid">' + "".join(parca) + "</div>"


def filtre_html(etiketler, gosterilen: int, toplam: int) -> str:
    cip = "".join(f'<span class="chip">{_e(str(x))}</span>' for x in etiketler) \
        or '<span class="muted">Filtre yok — tüm kayıtlar gösteriliyor</span>'
    return (f'<div class="fbar"><div class="chips">{cip}</div>'
            f'<div class="sayac"><b>{gosterilen}</b> / {toplam} satır</div></div>')


def cip_html(ogeler) -> str:
    """ogeler: [(ad, sayı)] -> yeşil çipler (ör. parça: adet)"""
    return '<div class="chips">' + "".join(
        f'<span class="chip yesil"><b>{_e(str(a))}</b>{_e(str(n))}</span>' for a, n in ogeler) + "</div>"


# --- Streamlit sarmalayıcıları ---
def sayfa_basligi(baslik: str, alt: str = "") -> None:
    st.markdown(baslik_html(baslik, alt), unsafe_allow_html=True)


def bolum_basligi(baslik: str, aciklama: str = "") -> None:
    st.markdown(bolum_html(baslik, aciklama), unsafe_allow_html=True)


def kpi_satiri(kartlar) -> None:
    st.markdown(kpi_html(kartlar), unsafe_allow_html=True)


def filtre_seridi(etiketler, gosterilen: int, toplam: int) -> None:
    st.markdown(filtre_html(etiketler, gosterilen, toplam), unsafe_allow_html=True)


def cipler(ogeler) -> None:
    st.markdown(cip_html(ogeler), unsafe_allow_html=True)


# --- tablo ---
_TON = (("Plansız", "rgba(239,68,68,.20)"), ("Set Up", "rgba(245,158,11,.22)"), ("Rework", "rgba(168,85,247,.20)"))


def tablo_stili(gos):
    """Durum / Sebep sütununu hafifçe renklendirir (yalnızca o sütun), Tarih ve Süre biçimini ayarlar."""
    def _renk(v):
        for k, c in _TON:
            if str(v).startswith(k):
                return f"background-color:{c}"
        return ""
    s = gos.style
    s = (s.map if hasattr(s, "map") else s.applymap)(_renk, subset=["Durum / Sebep"])
    _gun = lambda d: d.strftime("%d.%m.%Y") if pd.notna(d) else ""
    return s.format({"Süre (sa)": "{:.2f}", "Tarih": _gun, "Üretim Günü": _gun})
