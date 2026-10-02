# -*- coding: utf-8 -*-
"""
4_AI_Asistan.py — AI Fabrika Asistanı.

Özellik: Sadece sayı vermekle kalmaz, "bu sayıyı nasıl buldun?" diye
sorulduğunda hangi METRİĞİ, hangi FORMÜLÜ, hangi KOLONLARI kullandığını
söyler. Bunu sağlamak için METRIC_REGISTRY (common.py) sistem promptuna
tam olarak enjekte edilir ve modelden HER sayısal iddiada kullandığı
metriğin adını parantez içinde belirtmesi istenir.
S/N (Seri No) bazlı üretim takibi (adet, hafta, setup) prompta entegredir.
"""
from __future__ import annotations

import os
import pandas as pd
import streamlit as st
from dotenv import load_dotenv

from sn_izleme import sn_ai_baglami, SN_AI_KURALI
from common import (
    METRIC_REGISTRY, metrik_aciklamasi, veri_yukle_ve_filtrele,
    compute_tezgah_oee, compute_operator_karne, compute_pareto,
    compute_mtbf_mttr, compute_vardiya_yorgunluk, compute_setup_parca_kirilimi,
    compute_onerilen_ct,
)

# ---------------------------------------------------------------- GERÇEK İSİM SÖZLÜĞÜ
OPERATOR_SOZLUGU = {
  "Op_001": "AŞKIN SAKALLI", "Op_002": "BERAT TUNÇ", "Op_003": "CİHAN SARIGÜL", "Op_004": "EMRE TOPRAK", 
  "Op_005": "EREN TAMER YELEKÇİ", "Op_006": "ERTUĞRUL ÖZDEMİR", "Op_007": "HAMZA KARAASLAN", "Op_008": "HÜSEYİN YENİCE", 
  "Op_009": "HÜSEYİN YILMAZ", "Op_010": "İLKER SEZDİ", "Op_011": "MEHMET ALİ ÇOLAK", "Op_012": "MEHMET EMRE KARAKAŞ", 
  "Op_013": "MERT KUŞCU", "Op_014": "MESUT ARDIÇ", "Op_015": "MUHAMMED KORKMAZ", "Op_016": "NAZİF GÜÇLÜDAĞ", 
  "Op_017": "NURULLAH ZENGİN", "Op_018": "ÖZCAN ÇELİK", "Op_019": "TANER GÜÇLÜDAĞ", "Op_020": "UMUT ÖZKAN", "Op_021": "YAŞAR ŞENOL"
}

st.set_page_config(page_title="AI Fabrika Asistanı", layout="wide", page_icon="🤖")
st.title("🤖 AI Fabrika Asistanı")
st.caption(
    "Sorunu doğal dilde sor. Asistan, verdiği her sayının HANGİ metrikten ve "
    "HANGİ formülden geldiğini açıklayabilir — 'bunu nasıl hesapladın?' diye sorabilirsin."
)

df_ham, df, kaynak_notu = veri_yukle_ve_filtrele("asistan")
st.caption(kaynak_notu)

if df.empty:
    st.warning("Seçili filtrelerle eşleşen veri yok.")
    st.stop()

# GERÇEK İSİMLERİ VERİYE UYGULA (AI'ın okuyup isimle cevap verebilmesi için)
if "Operator_Kod" in df.columns:
    df["Operator_Kod"] = df["Operator_Kod"].replace(OPERATOR_SOZLUGU)

# ---------------------------------------------------------------- METRİK SÖZLÜĞÜ (her zaman görünür)
with st.expander("📖 Kullanılan Tüm Metrik ve Formüller Sözlüğü", expanded=False):
    for anahtar in METRIC_REGISTRY:
        st.markdown(metrik_aciklamasi(anahtar))
        st.markdown("---")

st.markdown("---")

# ---------------------------------------------------------------- API AYARLARI
try:
    import google.generativeai as genai
except ImportError:
    st.error("`google-generativeai` kurulu değil. Kurulum: `pip install google-generativeai`")
    st.stop()

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
if not guncel_modeller:
    st.error("Google'dan aktif model listesi alınamadı (API anahtarı geçersiz olabilir ya da ağ engelleniyor).")
    st.stop()

# Yan menüyü Google'ın anlık olarak izin verdiği modellerle doldur
model_adi = st.sidebar.selectbox("Google Gemini Modeli", guncel_modeller, index=0)

# =============================================================================
# BAĞLAM (CONTEXT) OLUŞTURMA
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
        parcalar.append("### TABLO 6: SETUP OPERATÖR×PARÇA KIRILIMI\n" + setup_kirilim.to_csv(index=False))
        kullanilan_metrikler.append("setup_ortalama")

    if "TARİH" in df.columns:
        gunluk_ozet = df.groupby([df["TARİH"].dt.date, "TEZGAH", "Durum"])["Sure_Saat"].sum().reset_index()
        parcalar.append("### TABLO 7: GÜNLÜK TEZGAH-DURUM SÜRE ÖZETİ\n" + gunluk_ozet.to_csv(index=False))

    onerilen_ct = compute_onerilen_ct(df)
    if not onerilen_ct.empty:
        parcalar.append(
            "### TABLO 9: ÖNERİLEN C/T (katalog C/T'nin verinin kendisinden türetilmiş, "
            "güncellenmesi önerilen alternatifi — Guncelleme_Onerisi=True olan kodlarda katalog "
            "C/T şüpheli/eski kabul edilmeli)\n" + onerilen_ct.to_csv(index=False)
        )
        kullanilan_metrikler.append("onerilen_ct")

    # --- SATIR BAZLI ETL ÇIKTISI (KRONOLOJİK VERİ) ---
    secilecek_kolonlar = [
        "TARİH", "BAŞLANGIÇ_SAAT", "BİTİŞ_SAAT", "TEZGAH", 
        "KOD & OPE.", "PARÇA NO_ÜRETİM_GİRİLEN", "Durum", 
        "Sure_Saat", "Durus_Kategorisi", "Durus_Sebebi_Ham", "Operator_Kod"
    ]
    mevcut_kolonlar = [col for col in secilecek_kolonlar if col in df.columns]
    
    if mevcut_kolonlar:
        kronolojik_veri = df[mevcut_kolonlar].copy()
        if "Sure_Saat" in kronolojik_veri.columns:
            kronolojik_veri["Sure_Dakika"] = (kronolojik_veri["Sure_Saat"] * 60).round(0)
        parcalar.append("### TABLO 8: KRONOLOJİK DETAYLI ETL ÇIKTISI (Satır Bazlı Döküm)\n" + kronolojik_veri.to_csv(index=False))

    # --- S/N VE WEEK BAZLI İZLEME TABLOLARI (TABLO 10-13) ---
    sn_baglami = sn_ai_baglami(df)
    if sn_baglami:
        parcalar.append(sn_baglami)

    return "\n\n".join(parcalar), sorted(set(kullanilan_metrikler))


baglam, kullanilan_metrikler = _baglam_ve_kullanilan_metrikler(df)
formul_bloklari = "\n\n".join(metrik_aciklamasi(k) for k in kullanilan_metrikler)

SISTEM_PROMPTU = f"""Sen bir fabrikanın üretim verisi asistanısın. Sana verilen özet CSV
tablolarına, KRONOLOJİK DETAY tablosuna (TABLO 8), S/N İZLEME TABLOLARINA (TABLO 10-13) ve METRİK SÖZLÜĞÜ'ne dayanarak Türkçe, net ve sayısal cevaplar ver.

KURALLAR:
1. Her sayısal iddianı, hangi TABLO'dan ve hangi METRİK'ten geldiğini belirterek destekle.
   Örnek: "AB-07 tezgahının kullanılabilirliği %99.1 (Kullanılabilirlik metriği, TABLO 1)."
2. Kullanıcı duruş özeti, dakika dakika, parça bazlı veya kronolojik olay sorarsa KESİNLİKLE 'TABLO 8: KRONOLOJİK DETAYLI ETL ÇIKTISI' tablosunu satır satır tara. Operatör kodları yerine gerçek isimleri ("Operator_Kod" sütunu) kullanarak detaylı listele.
3. Bir duruşu raporlarken Operatör İsmi, Parça, Dakika (Sure_Dakika) ve Sebebini kesinlikle belirt.
4. Kullanıcı "bunu nasıl hesapladın?", "neden türettin?" veya "hangi formülle buldun?" diye sorarsa, İLGİLİ METRİĞİN TAM FORMÜLÜNÜ VE MANTIĞINI METRİK SÖZLÜĞÜ'nden birebir alıntıla.
5. Veride olmayan bir şeyi UYDURMA. Emin değilsen açıkça "bu veriyle hesaplanamıyor" de.
6. Kullanıcıya liste veya döküm verirken "0 dakika" olan normal boşlukları veya sorunsuz standart kayıtları ASLA tek tek yazma. Sadece dikkat çekici anormallikleri (15 dakika, 30 dakikalık boşluklar, kırmızı alarmlar, sapmalar) listele. Cevaplarını her zaman kısa, net ve yöneticiye sunulacak özet formatında tut.
{SN_AI_KURALI}

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
    "2547M01G03 parçasından 32. hafta kaç adet ürettik?",
    "M01G03 parçasının S/N bazlı medyan süresi nedir?"
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
                model = genai.GenerativeModel(
                    model_name=model_adi,
                    system_instruction=SISTEM_PROMPTU
                )
                
                gemini_gecmis = []
                for mesaj in st.session_state.sohbet_gecmisi[:-1]: 
                    role = "user" if mesaj["role"] == "user" else "model"
                    gemini_gecmis.append({"role": role, "parts": [mesaj["content"]]})
                
                chat = model.start_chat(history=gemini_gecmis)
                response = chat.send_message(soru)
                cevap = response.text
                
                st.markdown(cevap)
        st.session_state.sohbet_gecmisi.append({"role": "assistant", "content": cevap})
    except Exception as e:
        st.error(f"AI çağrısı başarısız oldu: {e}")

# =============================================================================
# PATRONUN İSTEDİĞİ: DİNAMİK TEYİT BUTONU VE HAM VERİ EKRANI
# =============================================================================
# =============================================================================
# YANITI HAM VERİDEN DOĞRULAMA EKRANI (DİNAMİK FİLTRELİ)
# =============================================================================
st.markdown("---")
with st.expander("🔍 Yanıtı Ham Veriden Doğrula"):
    st.markdown("Yapay zekanın yanıtında geçen tezgahı, operatörü veya S/N numarasını seçerek ilgili satırları anında kontrol edebilirsiniz.")
    
    # "Sapma", "Yüzde", "C/T" gibi kafa karıştırıcı kolonları tablodan temizle
    # S/N kolonunu gösterime dahil et
    goster_df = df.copy()
    silinecek_kolonlar = [c for c in goster_df.columns if "Sapma" in c or "Yuzde" in c or "C/T" in c]
    goster_df = goster_df.drop(columns=silinecek_kolonlar, errors="ignore")
    
    # Kullanıcının AI'ın cevabına göre anında filtreleyebilmesi için dinamik kutular
    c1, c2, c3 = st.columns(3)
    with c1:
        sec_tezgah = st.multiselect("Tezgah Seç:", sorted(goster_df["TEZGAH"].dropna().unique()))
    with c2:
        sec_op = st.multiselect("Operatör Seç:", sorted(goster_df["Operator_Kod"].dropna().unique()))
    with c3:
        hizli_ara = st.text_input("Parça veya S/N Ara:")
        
    # Seçimlere göre anlık filtreleme yap
    if sec_tezgah:
        goster_df = goster_df[goster_df["TEZGAH"].isin(sec_tezgah)]
    if sec_op:
        goster_df = goster_df[goster_df["Operator_Kod"].isin(sec_op)]
    if hizli_ara:
        arama_kucuk = hizli_ara.lower()
        maske = goster_df.astype(str).apply(lambda x: x.str.lower().str.contains(arama_kucuk)).any(axis=1)
        goster_df = goster_df[maske]
        
    st.dataframe(goster_df, use_container_width=True, hide_index=True)
    st.caption(f"Filtrelenen satır sayısı: {len(goster_df)}")