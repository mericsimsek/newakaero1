# -*- coding: utf-8 -*-
"""
4_AI_Asistan.py — AI Fabrika Asistanı.

Özellik: Sadece sayı vermekle kalmaz, "bu sayıyı nasıl buldun?" diye
sorulduğunda hangi METRİĞİ, hangi FORMÜLÜ, hangi KOLONLARI kullandığını
söyler. Bunu sağlamak için METRIC_REGISTRY (common.py) sistem promptuna
tam olarak enjekte edilir ve modelden HER sayısal iddiada kullandığı
metriğin adını parantez içinde belirtmesi istenir.
"""
from __future__ import annotations

import pandas as pd
import streamlit as st

from common import (
    METRIC_REGISTRY, metrik_aciklamasi, veri_yukle_ve_filtrele,
    compute_tezgah_oee, compute_operator_karne, compute_pareto,
    compute_mtbf_mttr, compute_vardiya_yorgunluk, compute_setup_parca_kirilimi,
    compute_zaman_cizelgesi, compute_aylik_ozet,
)

st.set_page_config(page_title="AI Fabrika Asistanı", layout="wide", page_icon="🤖")
st.title("🤖 AI Fabrika Asistanı")
st.caption(
    "Sorunu doğal dilde sor. Asistan, verdiği her sayının HANGİ metrikten ve "
    "HANGİ formülden geldiğini açıklayabilir — 'bunu nasıl hesapladın?' diye sorabilirsin."
)

df_ham, df, kaynak_notu = veri_yukle_ve_filtrele("asistan")
st.caption(kaynak_notu)

# ---------------------------------------------------------------- METRİK SÖZLÜĞÜ (her zaman görünür)
with st.expander("📖 Kullanılan Tüm Metrik ve Formüller Sözlüğü", expanded=False):
    for anahtar in METRIC_REGISTRY:
        st.markdown(metrik_aciklamasi(anahtar))
        st.markdown("---")

if df.empty:
    st.warning("Seçili filtrelerle eşleşen veri yok.")
    st.stop()

st.markdown("---")

# ---------------------------------------------------------------- API AYARLARI
# ---------------------------------------------------------------- API AYARLARI
import os
from dotenv import load_dotenv
import google.generativeai as genai

load_dotenv()
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

if not GEMINI_API_KEY:
    st.error("HATA: .env dosyasında GEMINI_API_KEY bulunamadı!")
    st.stop()

# Google kütüphanesini API anahtarınla başlatıyoruz
genai.configure(api_key=GEMINI_API_KEY)

# Google sunucularından anlık aktif modelleri çeken dinamik fonksiyon
@st.cache_data(show_spinner=False)
def aktif_modelleri_getir():
    modeller = []
    try:
        for m in genai.list_models():
            # Sadece metin/içerik üretebilen modelleri filtrele
            if 'generateContent' in m.supported_generation_methods:
                # "models/gemini-xxx" formatındaki baştaki "models/" kısmını temizle
                modeller.append(m.name.replace("models/", ""))
    except Exception as e:
        st.sidebar.error(f"Modeller çekilemedi: {e}")
    return modeller

guncel_modeller = aktif_modelleri_getir()

# Yan menüyü Google'ın anlık olarak izin verdiği modellerle doldur
model_adi = st.sidebar.selectbox(
    "Google Gemini Modeli", 
    guncel_modeller, 
    index=0 if guncel_modeller else None
)

try:
    import google.generativeai as genai
except ImportError:
    st.error("`google-generativeai` kurulu değil. Kurulum: `pip install google-generativeai`")
    st.stop()

# Google kütüphanesini API anahtarınla başlatıyoruz
genai.configure(api_key=GEMINI_API_KEY)
# =============================================================================
# BAĞLAM (CONTEXT) OLUŞTURMA — filtrelenmiş veriden özet tablolar + kullanılan
# metriklerin formülleri bir arada modele gönderilir.
# =============================================================================
def _baglam_ve_kullanilan_metrikler(df: pd.DataFrame) -> tuple[str, list[str]]:
    parcalar = []
    kullanilan_metrikler: list[str] = []

    tezgah_ozet = compute_tezgah_oee(df)
    if not tezgah_ozet.empty:
        parcalar.append("### TABLO 1: TEZGAH ÖZETİ (tezgah_oee)\n" + tezgah_ozet.to_csv(index=False))
        kullanilan_metrikler += ["kullanilabilirlik", "kalite_orani", "performans_orani", "gercek_oee"]

    operator_karne = compute_operator_karne(df)
    if not operator_karne.empty:
        parcalar.append("### TABLO 2: OPERATÖR KARNESİ\n" + operator_karne.to_csv(index=False))
        kullanilan_metrikler += ["calisma_orani_operator", "ct_bazli_sapma", "emsal_bazli_sapma", "ct_dusuk_guven_yuzde"]

    pareto = compute_pareto(df)
    if not pareto.empty:
        parcalar.append("### TABLO 3: DURUŞ PARETO (kategori bazında toplam saat + kümülatif %)\n" + pareto.to_csv(index=False))
        kullanilan_metrikler.append("pareto_kumulatif")

    mtbf = compute_mtbf_mttr(df)
    if not mtbf.empty:
        parcalar.append("### TABLO 4: MTBF/MTTR\n" + mtbf.to_csv(index=False))
        kullanilan_metrikler += ["mtbf", "mttr"]

    vy = compute_vardiya_yorgunluk(df)
    if not vy.empty:
        parcalar.append("### TABLO 5: VARDİYA YORGUNLUK (çeyrek bazında hata oranı)\n" + vy.to_csv(index=False))
        kullanilan_metrikler.append("vardiya_hata_orani")

    setup_kirilim = compute_setup_parca_kirilimi(df)
    if not setup_kirilim.empty:
        parcalar.append("### TABLO 6: SETUP OPERATÖR×PARÇA KIRILIMI (ilk 100 satır)\n" + setup_kirilim.head(100).to_csv(index=False))
        kullanilan_metrikler.append("setup_ortalama")

    if "TARİH" in df.columns:
        gunluk_ozet = df.groupby([df["TARİH"].dt.date, "TEZGAH", "Durum"])["Sure_Saat"].sum().reset_index()
        parcalar.append("### TABLO 7: GÜNLÜK TEZGAH-DURUM SÜRE ÖZETİ (ilk 300 satır)\n" + gunluk_ozet.head(300).to_csv(index=False))

    return "\n\n".join(parcalar)[:15000], sorted(set(kullanilan_metrikler))


baglam, kullanilan_metrikler = _baglam_ve_kullanilan_metrikler(df)
formul_bloklari = "\n\n".join(metrik_aciklamasi(k) for k in kullanilan_metrikler)

SISTEM_PROMPTU = f"""Sen bir fabrikanın üretim verisi asistanısın. Sana verilen özet CSV
tablolarına ve METRİK SÖZLÜĞÜ'ne dayanarak Türkçe, net ve sayısal cevaplar ver.

KURALLAR:
1. Her sayısal iddianı, hangi TABLO'dan ve hangi METRİK'ten geldiğini belirterek destekle.
   Örnek: "AB-07 tezgahının kullanılabilirliği %99.1 (Kullanılabilirlik metriği, TABLO 1)."
2. Kullanıcı "bunu nasıl hesapladın?" ya da "hangi formülle buldun?" diye sorarsa,
   İLGİLİ METRİĞİN TAM FORMÜLÜNÜ aşağıdaki METRİK SÖZLÜĞÜ'nden birebir alıntılayarak açıkla,
   ve varsa tablo içindeki gerçek sayılarla formülü doldurarak göster (örn. "(102.00+0.0)/(102.00+6.25+0.0)×100=%94.2").
3. Veride olmayan bir şeyi UYDURMA. Emin değilsen açıkça "bu veriyle hesaplanamıyor" de.
4. Kısa ve öz cevap ver, ama sayısal doğruluktan ödün verme.

--- METRİK SÖZLÜĞÜ (bu sohbette kullanılan tablolarla ilgili formüller) ---
{formul_bloklari}

--- VERİ TABLOLARI (CSV) ---
{baglam}
"""

if "sohbet_gecmisi" not in st.session_state:
    st.session_state.sohbet_gecmisi = []

for mesaj in st.session_state.sohbet_gecmisi:
    with st.chat_message(mesaj["role"]):
        st.markdown(mesaj["content"])

ornek_sorular = [
    "Bu hafta en çok hangi tezgah durdu ve niye?",
    "AB-07'nin kullanılabilirliğini nasıl hesapladın?",
    "Hangi operatör emsallerine göre en yavaş?",
    "MTBF'i hangi formülle buldun?",
]
st.caption("Örnek sorular: " + " · ".join(f"*{s}*" for s in ornek_sorular))

soru = st.chat_input("Sorunu yaz...")
if soru:
    st.session_state.sohbet_gecmisi.append({"role": "user", "content": soru})
    with st.chat_message("user"):
        st.markdown(soru)

    try:
        with st.chat_message("assistant"):
            with st.spinner("Veriler ve formüller analiz ediliyor..."):
                # Gemini modelini ve sistem promptunu (tabloları) yüklüyoruz
                model = genai.GenerativeModel(
                    model_name=model_adi,
                    system_instruction=SISTEM_PROMPTU
                )
                
                # Streamlit geçmişini Gemini'nin anlayacağı formata çeviriyoruz
                gemini_gecmis = []
                for mesaj in st.session_state.sohbet_gecmisi[:-1]: 
                    role = "user" if mesaj["role"] == "user" else "model"
                    gemini_gecmis.append({"role": role, "parts": [mesaj["content"]]})
                
                # Sohbeti başlat ve soruyu gönder
                chat = model.start_chat(history=gemini_gecmis)
                response = chat.send_message(soru)
                cevap = response.text
                
                st.markdown(cevap)
        st.session_state.sohbet_gecmisi.append({"role": "assistant", "content": cevap})
    except Exception as e:
        st.error(f"AI çağrısı başarısız oldu: {e}")
