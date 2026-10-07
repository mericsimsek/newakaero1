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

# ---------------------------------------------------------------- API AYARLARI (GROQ)
import os
import re
from dotenv import load_dotenv

try:
    from groq import Groq
except ImportError:
    st.error("`groq` kurulu değil. Kurulum: `pip install groq python-dotenv`")
    st.stop()

load_dotenv()


def _api_anahtari_oku() -> str | None:
    anahtar = os.getenv("GROQ_API_KEY")
    if anahtar:
        return anahtar.strip()
    try:  # Streamlit Cloud: .streamlit/secrets.toml
        return st.secrets["GROQ_API_KEY"]
    except Exception:
        return None


GROQ_API_KEY = _api_anahtari_oku()
if not GROQ_API_KEY:
    st.error("HATA: .env dosyasında GROQ_API_KEY bulunamadı!")
    st.stop()

client = Groq(api_key=GROQ_API_KEY)

# Varsayılan sırada öne çıkacak modeller (listede varsa)
TERCIH_EDILEN = [
    "llama-3.3-70b-versatile",
    "openai/gpt-oss-120b",
    "qwen/qwen3-32b",
    "openai/gpt-oss-20b",
    "llama-3.1-8b-instant",
]
DISLANAN = ("whisper", "guard", "tts", "orpheus", "playai", "safeguard", "embed")


@st.cache_data(show_spinner=False, ttl=3600)
def aktif_modelleri_getir():
    try:
        idler = [m.id for m in client.models.list().data]
        idler = [i for i in idler if not any(x in i.lower() for x in DISLANAN)]
        onde = [m for m in TERCIH_EDILEN if m in idler]
        geri = sorted(i for i in idler if i not in onde)
        return onde + geri or TERCIH_EDILEN
    except Exception as e:
        st.sidebar.warning(f"Model listesi çekilemedi, varsayılan liste kullanılıyor: {e}")
        return TERCIH_EDILEN


guncel_modeller = aktif_modelleri_getir()
model_adi = st.sidebar.selectbox("Groq Modeli", guncel_modeller, index=0)

# ---- Gemini (büyük bağlam / yedek için; GEMINI_API_KEY varsa devreye girer)
try:
    import google.generativeai as genai
except ImportError:
    genai = None

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
GEMINI_VAR = bool(genai and GEMINI_API_KEY)
if GEMINI_VAR:
    genai.configure(api_key=GEMINI_API_KEY)

GEMINI_TERCIH = ["gemini-2.5-flash-lite", "gemini-flash-lite-latest", "gemini-2.5-flash", "gemini-flash-latest"]


@st.cache_data(show_spinner=False, ttl=3600)
def gemini_modelleri_getir():
    try:
        idler = [m.name.replace("models/", "") for m in genai.list_models()
                 if "generateContent" in m.supported_generation_methods]
        onde = [m for m in GEMINI_TERCIH if m in idler]
        return onde + sorted(i for i in idler if i not in onde) or GEMINI_TERCIH
    except Exception:
        return GEMINI_TERCIH


gemini_model_adi = None
if GEMINI_VAR:
    saglayici = st.sidebar.radio(
        "AI sağlayıcı",
        ["Otomatik (büyük bağlamda Gemini, olmazsa Groq)", "Sadece Groq", "Sadece Gemini"],
    )
    gemini_model_adi = st.sidebar.selectbox("Gemini Modeli", gemini_modelleri_getir(), index=0)
else:
    saglayici = "Sadece Groq"
    st.sidebar.caption("Gemini yedeği kapalı (GEMINI_API_KEY veya google-generativeai yok).")

st.sidebar.markdown("**Bağlam boyutu** (ücretsiz plandaki dakikalık token sınırı için)")
TABLO7_MAX_SATIR = st.sidebar.number_input("TABLO 7 en fazla satır", 0, 2000, 100, step=50)
TABLO8_MAX_SATIR = st.sidebar.number_input("TABLO 8 en fazla satır", 0, 2000, 120, step=20)


def _csv_kirp(d: pd.DataFrame, n: int) -> str:
    """Tabloyu CSV'ye çevirir; çok uzunsa SON n satırı verir ve modele bunu söyler."""
    n = int(n)
    if n <= 0:
        return "(bu tablo kenar çubuğu ayarıyla kapatıldı)\n"
    if len(d) <= n:
        return d.to_csv(index=False)
    return (
        f"(NOT: toplam {len(d)} satırdan yalnızca SON {n} satır gösteriliyor; "
        "daha eski kayıtlar bu bağlamda YOK, bunu cevapta belirt.)\n"
        + d.tail(n).to_csv(index=False)
    )


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
        parcalar.append("### TABLO 7: GÜNLÜK TEZGAH-DURUM SÜRE ÖZETİ\n" + _csv_kirp(gunluk_ozet, TABLO7_MAX_SATIR))

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
        parcalar.append("### TABLO 8: KRONOLOJİK DETAYLI ETL ÇIKTISI (Satır Bazlı Döküm)\n" + _csv_kirp(kronolojik_veri, TABLO8_MAX_SATIR))

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

_tahmini_token = len(SISTEM_PROMPTU) // 3  # Türkçe CSV için kaba tahmin
st.sidebar.caption(f"Tahmini bağlam: ~{_tahmini_token:,} token")
if _tahmini_token > 8000:
    st.sidebar.warning(
        "Bağlam büyük. Ücretsiz planda 'çok büyük istek / dakikalık token' hatası "
        "alabilirsin. Satır sayılarını düşür veya yukarıdaki tarih filtresini daralt."
    )

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

def _groq_cagri() -> str:
    mesajlar = [{"role": "system", "content": SISTEM_PROMPTU}]
    mesajlar += st.session_state.sohbet_gecmisi[-7:]  # son birkaç mesaj yeter
    yanit = client.chat.completions.create(
        model=model_adi, messages=mesajlar, temperature=0.2, max_tokens=1500,
    )
    return yanit.choices[0].message.content or ""


def _gemini_cagri(soru_metni: str) -> str:
    model = genai.GenerativeModel(model_name=gemini_model_adi, system_instruction=SISTEM_PROMPTU)
    gecmis = [
        {"role": "user" if m["role"] == "user" else "model", "parts": [m["content"]]}
        for m in st.session_state.sohbet_gecmisi[-7:-1]
    ]
    while gecmis and gecmis[0]["role"] != "user":  # Gemini geçmişi 'user' ile başlamalı
        gecmis.pop(0)
    return model.start_chat(history=gecmis).send_message(soru_metni).text


def _cevap_uret(soru_metni: str) -> tuple[str, str]:
    if saglayici == "Sadece Gemini":
        sira = ["gemini"]
    elif saglayici == "Sadece Groq":
        sira = ["groq"]
    else:  # Otomatik
        sira = ["gemini", "groq"] if _tahmini_token > 8000 else ["groq", "gemini"]

    son_hata = None
    for s_ in sira:
        try:
            if s_ == "groq":
                return _groq_cagri(), f"Groq · {model_adi}"
            return _gemini_cagri(soru_metni), f"Gemini · {gemini_model_adi}"
        except Exception as e:
            son_hata = e
    raise son_hata


if soru:
    st.session_state.sohbet_gecmisi.append({"role": "user", "content": soru})
    with st.chat_message("user"):
        st.markdown(soru)

    try:
        with st.chat_message("assistant"):
            with st.spinner("Veriler ve formüller analiz ediliyor..."):
                cevap, kullanilan = _cevap_uret(soru)
                # Bazı modeller (ör. qwen3) <think>...</think> bloğu döndürebilir
                cevap = re.sub(r"<think>.*?</think>", "", cevap, flags=re.DOTALL).strip()
                st.markdown(cevap)
                st.caption(f"🤖 {kullanilan}")
        st.session_state.sohbet_gecmisi.append({"role": "assistant", "content": cevap})
    except Exception as e:
        st.session_state.sohbet_gecmisi.pop()  # cevapsız kalan soruyu geçmişten çıkar
        hata = str(e)
        if "413" in hata or "too large" in hata.lower() or "tokens per minute" in hata.lower():
            st.error(
                "İstek seçili modelin ücretsiz limiti için çok büyük. Sağlayıcıyı Gemini yap, "
                "TABLO 7/8 satırlarını azalt veya tarih filtresini daralt."
            )
        elif "429" in hata or "rate" in hata.lower() or "quota" in hata.lower():
            st.error("Limit doldu. Biraz bekle ya da başka bir model/sağlayıcı seç.")
        else:
            st.error(f"AI çağrısı başarısız oldu: {e}")
    else:
        st.rerun()