# -*- coding: utf-8 -*-
"""
4_AI_Asistan.py — AI Fabrika Asistanı.
"""
from __future__ import annotations

import pandas as pd
import streamlit as st

from sn_izleme import sn_ai_baglami, SN_AI_KURALI   
from teyit import tablolari_topla, render_teyit      
from common import (
    METRIC_REGISTRY, metrik_aciklamasi, veri_yukle_ve_filtrele,
    compute_tezgah_oee, compute_operator_karne, compute_pareto,
    compute_mtbf_mttr, compute_vardiya_yorgunluk, compute_setup_parca_kirilimi,
    compute_zaman_cizelgesi, compute_aylik_ozet, compute_onerilen_ct,
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

if "Operator_Kod" in df.columns:
    df["Operator_Kod"] = df["Operator_Kod"].replace(OPERATOR_SOZLUGU)

with st.expander("📖 Kullanılan Tüm Metrik ve Formüller Sözlüğü", expanded=False):
    for anahtar in METRIC_REGISTRY:
        st.markdown(metrik_aciklamasi(anahtar))
        st.markdown("---")

st.markdown("---")

# ---------------------------------------------------------------- API AYARLARI
import os
from dotenv import load_dotenv

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

genai.configure(api_key=GEMINI_API_KEY)

@st.cache_data(show_spinner=False)
def aktif_modelleri_getir():
    modeller = []
    try:
        for m in genai.list_models():
            if 'generateContent' in m.supported_generation_methods:
                modeller.append(m.name.replace("models/", ""))
    except Exception as e:
        st.sidebar.error(f"Modeller çekilemedi: {e}")
    return modeller

guncel_modeller = aktif_modelleri_getir()
if not guncel_modeller:
    st.error("Google'dan aktif model listesi alınamadı (API anahtarı geçersiz olabilir ya da ağ engelleniyor).")
    st.stop()

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
            "güncellenmesi önerilen alternatifi)\n" + onerilen_ct.to_csv(index=False)
        )
        kullanilan_metrikler.append("onerilen_ct")

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

    return "\n\n".join(parcalar), sorted(set(kullanilan_metrikler))

baglam, kullanilan_metrikler = _baglam_ve_kullanilan_metrikler(df)
_sn_baglam = sn_ai_baglami(df)
if _sn_baglam:
    baglam = baglam + "\n\n" + _sn_baglam

tablolar = tablolari_topla(df)
formul_bloklari = "\n\n".join(metrik_aciklamasi(k) for k in kullanilan_metrikler)

SISTEM_PROMPTU = f"""Sen bir fabrikanın üretim verisi asistanısın. Sana verilen özet CSV
tablolarına, KRONOLOJİK DETAY tablosuna (TABLO 8) ve METRİK SÖZLÜĞÜ'ne dayanarak Türkçe, net ve sayısal cevaplar ver.

KURALLAR:
1. Her sayısal iddianı, hangi TABLO'dan ve hangi METRİK'ten geldiğini belirterek destekle.
2. Kullanıcı duruş özeti, dakika dakika, parça bazlı veya kronolojik olay sorarsa KESİNLİKLE 'TABLO 8: KRONOLOJİK DETAYLI ETL ÇIKTISI' tablosunu satır satır tara. Operatör kodları yerine gerçek isimleri kullanarak detaylı listele.
3. Bir duruşu raporlarken Operatör İsmi, Parça, Dakika (Sure_Dakika) ve Sebebini kesinlikle belirt.
4. Kullanıcı "bunu nasıl hesapladın?", "neden türettin?" veya "hangi formülle buldun?" diye sorarsa, İLGİLİ METRİĞİN TAM FORMÜLÜNÜ VE MANTIĞINI METRİK SÖZLÜĞÜ'nden birebir alıntıla.
5. Veride olmayan bir şeyi UYDURMA. Emin değilsen açıkça "bu veriyle hesaplanamıyor" de.

{SN_AI_KURALI}

--- METRİK SÖZLÜĞÜ (bu sohbette kullanılan tablolarla ilgili formüller) ---
{formul_bloklari}

--- VERİ TABLOLARI (CSV) ---
{baglam}
"""

if "sohbet_gecmisi" not in st.session_state:
    st.session_state.sohbet_gecmisi = []

def _teyit_ac_kapat(anahtar: str):
    st.session_state[anahtar] = not st.session_state.get(anahtar, False)

def _teyit_alani(idx: int):
    gecmis = st.session_state.sohbet_gecmisi
    soru_metni = gecmis[idx - 1]["content"] if idx > 0 and gecmis[idx - 1]["role"] == "user" else ""
    anahtar = f"teyit_acik_{idx}"
    acik = st.session_state.get(anahtar, False)
    st.button("✖ Teyidi kapat" if acik else "🔍 Bu cevabı gerçek veriyle teyit et", key=f"teyit_btn_{idx}",
              on_click=_teyit_ac_kapat, args=(anahtar,))
    if acik:
        render_teyit(st, soru_metni, gecmis[idx]["content"], df, tablolar, kaynak_notu)

# =============================================================================
# 1. YÖNETİCİ KISAYOLLARI (SADECE BİR KERE YAZILDI VE UYARILAR TEMİZLENDİ)
# =============================================================================
st.markdown("### ⚡ Yönetici Analiz Kısayolları")
st.caption("Aşağıdaki butonlara tıklayarak temel üretim verilerini saniyeler içinde çekebilirsiniz.")

sekme_parca, sekme_durus, sekme_op = st.tabs(["⚙️ Parçalar (S/N)", "🛑 Duruş ve Set-up", "👷 Operatör ve Tezgah"])

secilen_soru = None

with sekme_parca:
    s_parca = [
        "33. haftada toplam kaç adet parça (S/N) üretildi?",
        "Geçen hafta üretilen parçaların ortalama süresi kaç saatti?",
        "En hızlı üretilen 5 parça hangisi ve kimler yaptı?",
        "Bu hafta hangi tezgahta kaç adet parça işlendi?",
        "Vardiyalara göre üretilen parça (S/N) sayılarının dağılımı nasıldır?",
        "Hangi parçalarda birden fazla usta çalışarak (iş bölüşerek) üretimi tamamladı?",
        "Günlük bazda bakarsak, en çok parça hangi gün çıktı?",
        "Hedeflenen 2.5 saatlik süreye tam uyan veya en yakın olan parçalar hangileri?",
        "33. haftada işlenen parçaların operatörlere göre dağılımı nasıldır?",
        "Op 60'ta üretilen parçaların genel ortalama ve medyan süreleri nedir?"
    ]
    c1, c2 = st.columns(2)
    for i, s in enumerate(s_parca):
        col = c1 if i % 2 == 0 else c2
        if col.button(s, key=f"p_{i}"):
            secilen_soru = s

with sekme_durus:
    s_durus = [
        "Geçen hafta Op 60'ta toplam kaç saat duruş yaşadık?",
        "En çok zaman kaybettiğimiz ana duruş sebebi neydi?",
        "Hangi tezgah arızalar veya beklemeler yüzünden en çok durdu?",
        "Sadece 'ITEM TEKRARI' yüzünden kaç saat kaybettik ve kimler yaşadı?",
        "Set-up işlemleri toplam kaç saat tuttu ve ortalama ne kadar sürüyor?",
        "Operatörlerin sadece sayı girerek yaptığı duruş kayıtları kaç saat?",
        "Gece vardiyasında (23:00 - 07:00) yaşanan plansız duruşların toplamı kaç saat?",
        "Tezgah ısınma ve temizlik işlemleri için toplam ne kadar zaman harcandı?",
        "Geçen hafta duruş süresi en az olan (en sorunsuz çalışan) tezgah hangisiydi?",
        "Geçen hafta günlere göre duruş süreleri nasıl dağıldı?"
    ]
    c1, c2 = st.columns(2)
    for i, s in enumerate(s_durus):
        col = c1 if i % 2 == 0 else c2
        if col.button(s, key=f"d_{i}"):
            secilen_soru = s

with sekme_op:
    s_op = [
        "Bu hafta en çok parça üreten (net üretim adedi en yüksek) usta kim?",
        "Genel ortalamaya (medyana) göre en hızlı çalışan 3 operatör kim?",
        "Bu hafta işlem süreleri hedefin (medyan) üstünde kalan operatörlerimiz kimler?",
        "Yaşar Şenol bu hafta hangi parçaları işledi ve ortalama hızı neydi?",
        "Gece vardiyası mı daha hızlı çalışmış yoksa gündüz vardiyası mı?",
        "AB-10 tezgahında toplam kaç parça işlendi ve ortalama işlem hızı nedir?",
        "AB-06 tezgahının performansı ve Gerçek OEE oranı % kaçtır?",
        "Özcan Çelik'in tek başına bitirdiği işlerdeki net hızı nedir?",
        "Hangi operatörler işin yarısını yapıp diğer vardiyaya/ustaya devretti?",
        "Şu an fabrikada 1 parçayı bitirme hızımızın genel ortalaması (medyan) kaç saat?"
    ]
    c1, c2 = st.columns(2)
    for i, s in enumerate(s_op):
        col = c1 if i % 2 == 0 else c2
        if col.button(s, key=f"o_{i}"):
            secilen_soru = s

st.markdown("---")

# =============================================================================
# 2. SOHBET GEÇMİŞİ (SADECE BİR KERE YAZILDI)
# =============================================================================
for _i, mesaj in enumerate(st.session_state.sohbet_gecmisi):
    with st.chat_message(mesaj["role"]):
        st.markdown(mesaj["content"])
        if mesaj["role"] == "assistant":
            _teyit_alani(_i)

# =============================================================================
# 3. CHAT GİRDİSİ VE AI ÇAĞRISI (EN ALTTA)
# =============================================================================
soru = st.chat_input("Sorunu manuel yaz veya yukarıdan bir analiz seç...")
if secilen_soru:
    soru = secilen_soru

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
    else:
        st.rerun()