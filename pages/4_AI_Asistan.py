# -*- coding: utf-8 -*-
"""
4_AI_Asistan.py — AI Fabrika Asistanı.
"""
from __future__ import annotations
from uretim_akisi import hazirla
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

def _haftalik_baglam(df_ham: pd.DataFrame, df: pd.DataFrame, hafta_sayisi: int = 3) -> str:
    """app6 ile aynı hazirla() çıktısından, Perşembe–Çarşamba haftalarına göre özet tablolar."""
    T, _ = hazirla(df_ham)
    t = T.dropna(subset=["Tarih"]).copy()
    if "TARİH" in df.columns and df["TARİH"].notna().any():            # AI sayfasındaki tarih filtresine uy
        t = t[(t["Tarih"] >= df["TARİH"].min().normalize()) & (t["Tarih"] <= df["TARİH"].max().normalize())]
    if t.empty:
        return ""

    iso = (t["Tarih"] - pd.Timedelta(days=3)).dt.isocalendar()           # Perşembe başlangıçlı hafta
    t["Yıl"], t["Hafta"] = iso.year.astype(int), iso.week.astype(int)
    t["Hafta_Başı_Perş"] = (t["Tarih"] - pd.to_timedelta((t["Tarih"].dt.weekday - 3) % 7, unit="D")).dt.date
    son = t[["Yıl", "Hafta"]].drop_duplicates().sort_values(["Yıl", "Hafta"]).tail(hafta_sayisi)
    t = t.merge(son, on=["Yıl", "Hafta"])
    h = ["Yıl", "Hafta", "Hafta_Başı_Perş"]

    # HAFTA-1: Tezgah bazlı: tüm durum saatleri + biten adet
    p = t.pivot_table(index=h + ["Tezgah"], columns="Durum", values="Süre", aggfunc="sum", fill_value=0).round(2)
    p["Net_Adet"] = t.groupby(h + ["Tezgah"])["_adet"].sum()
    t10 = p.reset_index()
    t10.columns.name = None

    # HAFTA-DETAY: Operatör × Tezgah × Parça × Op (her şey tek satırda, AI birleştirme yapmasın)
    t["Parça"] = t["Parça"].fillna("-")
    t["Op"] = t["Op"].fillna("-")
    ad = {"Üretim": "Uretim_sa", "Plansız_Duruş": "Durus_sa", "Set_Up": "SetUp_sa", "Rework": "Rework_sa"}
    keys = h + ["Operatör", "Tezgah", "Parça", "Op"]
    dd = t.assign(_k=t["Durum"].map(ad)).dropna(subset=["_k"])
    pv = dd.pivot_table(index=keys, columns="_k", values="Süre", aggfunc="sum", fill_value=0)
    for c in ad.values():
        if c not in pv.columns:
            pv[c] = 0
    pv["Biten_Adet"] = t.groupby(keys)["_adet"].sum()
    pv["Islem_Goren_SN"] = t[t["S/N"].notna()].groupby(keys)["S/N"].nunique()
    t14 = pv[list(ad.values()) + ["Biten_Adet", "Islem_Goren_SN"]].fillna(0).round(2).reset_index()
    t14.columns.name = None

    # HAFTA-4: Duruş / Set-up / Rework sebepleri
    d = t[t["Durum"].isin(["Plansız_Duruş", "Set_Up", "Rework"]) & (t["Sebep"] != "")]
    t13 = (d.groupby(h + ["Tezgah", "Durum", "Sebep"])["Süre"].sum().round(2).reset_index()
             .rename(columns={"Süre": "Saat"}))

    bas = ("Kullanıcı 'N. hafta' derse YALNIZCA Hafta=N satırlarını kullan; tarih aralığı hesaplama. "
           "Hafta Perşembe–Çarşamba. Adet = BİTTİ alan S/N, parçanın bittiği haftada sayılır.")
    return (
        "### HAFTA-1: TEZGAH TOPLAMLARI (saatler + Net_Adet). Toplam sorularında BUNU kullan.\n"
        + bas + "\n" + t10.to_csv(index=False) +
        "\n### HAFTA-DETAY: Operatör × Tezgah × Parça × Op (her satır tek bir kombinasyondur; "
        "satırı olmayan kombinasyon YOKTUR)\n" + t14.to_csv(index=False) +
        "\n### HAFTA-4: DURUŞ/SET-UP/REWORK SEBEPLERİ (saat)\n" + t13.to_csv(index=False)
    )


def _gunluk_baglam(df_ham: pd.DataFrame, df: pd.DataFrame, gun_sayisi: int = 14) -> str:
    """app6 ile aynı hazirla() çıktısından, TÜM parçaları kapsayan günlük özet tabloları."""
    T, _ = hazirla(df_ham)
    t = T.dropna(subset=["Tarih"]).copy()
    if "TARİH" in df.columns and df["TARİH"].notna().any():
        t = t[(t["Tarih"] >= df["TARİH"].min().normalize()) & (t["Tarih"] <= df["TARİH"].max().normalize())]
    if t.empty:
        return ""
    t["Gün"] = t["Tarih"].dt.date
    gunler = sorted(t["Gün"].unique())[-gun_sayisi:]
    t = t[t["Gün"].isin(gunler)]
    ur = t["Durum"] == "Üretim"

    g1 = pd.DataFrame({
        "Uretim_sa": t[ur].groupby("Gün")["Süre"].sum(),
        "Plansiz_Durus_sa": t[t["Durum"] == "Plansız_Duruş"].groupby("Gün")["Süre"].sum(),
        "SetUp_Rework_sa": t[t["Durum"].isin(["Set_Up", "Rework"])].groupby("Gün")["Süre"].sum(),
        "Islem_Goren_SN": t.groupby("Gün")["_a"].nunique(),
        "Biten_Adet": t.groupby("Gün")["_adet"].sum(),
    }).fillna(0).round(2).reset_index()

    p = t.pivot_table(index=["Gün", "Tezgah"], columns="Durum", values="Süre", aggfunc="sum", fill_value=0).round(2)
    p["Biten_Adet"] = t.groupby(["Gün", "Tezgah"])["_adet"].sum()
    g2 = p.reset_index()
    g2.columns.name = None

    b = t[(t["_adet"] == 1) & t["Gün"].isin(gunler[-7:])]
    g3 = (b[["Gün", "Tezgah", "Parça", "Op", "S/N", "Operatör"]]
          .rename(columns={"Operatör": "Bitiren_Operatör"}).sort_values(["Gün", "Tezgah"]))
        # GÜN-4: Gün × Operatör × Tezgah: üretim saati + biten adet + biten S/N listesi
    key4 = ["Gün", "Operatör", "Tezgah"]
    g4 = t[ur].groupby(key4).agg(Uretim_sa=("Süre", "sum")).round(2)
    g4["Biten_Adet"] = t.groupby(key4)["_adet"].sum()
    g4["Biten_SN"] = t[t["_adet"] == 1].groupby(key4)["S/N"].apply(lambda s: ";".join(s.astype(str)))
    g4 = g4.fillna({"Biten_Adet": 0, "Biten_SN": ""}).astype({"Biten_Adet": int}).reset_index()

    kural = ("ADET KURALI: 'çıkan parça / biten adet / kaç parça' sorularında SADECE GÜN-1, GÜN-2, GÜN-3 "
             "ve HAFTA tablolarını kullan; bunlar TÜM parçaları kapsar. S/N izleme tabloları (TABLO 10, 14 vb.) "
             "yalnızca 2547M01G03 Op 60 içindir, fabrika toplamı için KULLANMA. "
             "Bir S/N ancak GÜN-3 listesinde varsa tamamlanmıştır. ")
    return (
        "### GÜN-1: GÜNLÜK FABRİKA TOPLAMI (tüm parçalar, saat + Biten_Adet)\n" + kural + "\n" + g1.to_csv(index=False) +
        "\n### GÜN-2: GÜNLÜK TEZGAH ÖZETİ (saat + Biten_Adet)\n" + g2.to_csv(index=False) +
        "\n### GÜN-3: SON 7 GÜNDE BİTEN S/N LİSTESİ (hangi parça, kim bitirdi)\n" + g3.to_csv(index=False)        + "\n### GÜN-4: GÜNLÜK OPERATÖR×TEZGAH (üretim saati, Biten_Adet, Biten_SN listesi). "
          "Bir operatörün gün/aralık bazlı biten parça sorusunda BUNU kullan; o operatörün satırlarındaki Biten_Adet'i topla.\n"
        + g4.to_csv(index=False)
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
        # 1. MEVCUT KODUN: Günlük mikro analizler için (Tablo 7)
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
    try:
        for _fn in (_gunluk_baglam, _haftalik_baglam):
            _b = _fn(df_ham, df)
            if _b:
                parcalar.append(_b)
    except Exception as e:
        st.sidebar.warning(f"Günlük/haftalık tablolar hazırlanamadı: {e}")
    return "\n\n".join(parcalar), sorted(set(kullanilan_metrikler))

baglam, kullanilan_metrikler = _baglam_ve_kullanilan_metrikler(df)
_sn_baglam = sn_ai_baglami(df)
if _sn_baglam:
    baglam = baglam + "\n\n" + _sn_baglam

_gunluk = st.session_state.get("gunluk_ai_baglami")
if _gunluk:
    baglam += (
        "\n\n### GÜNCEL TARİH ARALIĞI ÖZETİ (Günlük Üretim Akışı sayfasından)\n"
        "Kullanıcı bir tarih, gün veya hafta sorarsa BU bölümü kullan. "
        "TABLO 1 tüm dönemin toplamıdır, tarih aralığına göre hesaplanmamıştır.\n" + _gunluk
    )

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
6. Farklı tabloları kendi kafandan ÇAPRAZLAMA (ör. operatör tablosu ile parça tablosunu birleştirip tezgah çıkarma). Operatör+tezgah+parça sorularında SADECE HAFTA-DETAY'ı kullan; orada satırı olmayan kombinasyon veride YOKTUR. Toplam sorularında HAFTA-1 / GÜN-1 satırlarını kullan; kendin topladıysan hangi satırları topladığını yaz.
7. Bir operatörün gün/tarih aralığında kaç parça bitirdiği sorulursa SADECE GÜN-4'ü kullan: o operatörün Biten_Adet'lerini topla, Biten_SN'leri tek tek say ve listele. TABLO 3 duruş paretosudur, S/N kaynağı DEĞİLDİR; S/N için asla TABLO 3 gösterme.
{SN_AI_KURALI}

--- METRİK SÖZLÜĞÜ (bu sohbette kullanılan tablolarla ilgili formüller) ---
{formul_bloklari}

--- VERİ TABLOLARI (CSV) ---
{baglam}
"""
with st.sidebar.expander("🔬 AI'a giden bağlam (debug)"):
    st.write(re.findall(r"^### .*", SISTEM_PROMPTU, flags=re.M))
    st.download_button("Bağlamı indir (.txt)", SISTEM_PROMPTU, "baglam.txt")

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
        st.markdown(mesaj.get("gorunen", mesaj["content"]))
        if mesaj.get("hesap"):
            st.info(mesaj["hesap"])
        if mesaj["role"] == "assistant":
            _teyit_alani(_i)

# =============================================================================
# 3. CHAT GİRDİSİ VE AI ÇAĞRISI (EN ALTTA)
# =============================================================================
soru = st.chat_input("Sorunu manuel yaz veya yukarıdan bir analiz seç...")
if secilen_soru:
    soru = secilen_soru
_TRM = str.maketrans("İıŞşĞğÜüÖöÇç", "IiSsGgUuOoCc")
_AYLAR = {"ocak": 1, "subat": 2, "mart": 3, "nisan": 4, "mayis": 5, "haziran": 6, "temmuz": 7,
          "agustos": 8, "eylul": 9, "ekim": 10, "kasim": 11, "aralik": 12}

def _norm(s: str) -> str:
    return str(s).translate(_TRM).lower()

def _soru_hesabi(soru: str, T: pd.DataFrame) -> str:
    """Soruda operatör + tarih/hafta varsa toplamları Python hesaplar (AI toplamasın)."""
    t = T.dropna(subset=["Tarih"]).copy()
    if t.empty:
        return ""
    q = _norm(soru)
    kisiler = [k for k in t["Operatör"].dropna().unique() if k != "-" and _norm(k) in q]
    if not kisiler:
        if re.search(r"neden|sebep|nicin|niye", q) or not re.search(
                r"toplam|kac|ne kadar|ozet|fabrika|tezgah|adet|parca|sure|saat|durus|uretim", q):
            return ""
    yil = int(t["Tarih"].max().year)
    ay = "(" + "|".join(_AYLAR) + ")"
    yilp = r"(?:\s*(20\d\d))?"
    d1 = d2 = None
    etiket = ""
    tok = r"(?<!\d)\d{1,2}(?!\d)"
    pat = r"((?:" + tok + r"\s*(?:-|–|,|ile|ila|ve)?\s*)+)" + ay + yilp
    gunler = []
    for m in re.finditer(pat, q):
        y = int(m[3]) if m[3] else yil
        gunler += [pd.Timestamp(y, _AYLAR[m[2]], int(n)) for n in re.findall(tok, m[1])]
    d1 = d2 = None
    if gunler:
        d1, d2 = min(gunler), max(gunler)
    if d1 is None:
        mh = re.search(r"(?<!\d)(\d{1,2})(?!\d)\s*\.?\s*hafta", q)
        if not mh:
            return ""
        hno = int(mh[1])
        iso = (t["Tarih"] - pd.Timedelta(days=3)).dt.isocalendar()
        t = t[iso.week.astype(int) == hno]
        if t.empty:
            return ""
        d1, d2 = t["Tarih"].min(), t["Tarih"].max()
        etiket = f"{hno}. hafta (Perşembe–Çarşamba)"
    t = t[(t["Tarih"] >= d1) & (t["Tarih"] <= d2)]
    etiket = etiket or f"{d1:%d.%m.%Y} – {d2:%d.%m.%Y}"
    
    if not kisiler:
        tz_l = sorted({f"AB-{int(n):02d}" for n in re.findall(r"\bab\s*-?\s*(\d{1,2})\b", q)})
        tz_l = [z for z in tz_l if z in set(t["Tezgah"])]
        f = t[t["Tezgah"].isin(tz_l)] if tz_l else t
        if f.empty:
            return f"HESAPLANMIŞ SONUÇ — {etiket}: kayıt yok."

        def _ozet(d):
            sm = lambda x: d.loc[d["Durum"] == x, "Süre"].sum()
            return (f"Üretim {sm('Üretim'):.2f} sa | Plansız duruş {sm('Plansız_Duruş'):.2f} sa | "
                    f"Set-up {sm('Set_Up'):.2f} sa | Rework {sm('Rework'):.2f} sa | Biten parça: {int(d['_adet'].sum())} adet")

        satirlar = [f"HESAPLANMIŞ SONUÇ (Python, kesin; AI bu rakamları aynen kullanmalı, kendi toplamasın) — {etiket}",
                    ("SEÇİLİ TEZGAHLAR TOPLAMI: " if tz_l else "FABRİKA TOPLAMI: ") + _ozet(f), "TEZGAH BAZLI:"]
        for tz, d in f.groupby("Tezgah"):
            satirlar.append(f"  {tz}: " + _ozet(d))
        return "\n".join(satirlar)

    satirlar = [f"HESAPLANMIŞ SONUÇ (Python, kesin; AI bu rakamları aynen kullanmalı, kendi toplamasın) — {etiket}"]
    for kisi in kisiler:
        k = t[t["Operatör"] == kisi]
        if k.empty:
            satirlar.append(f"{kisi}: bu aralıkta kayıt yok.")
            continue
        s = lambda d: k.loc[k["Durum"] == d, "Süre"].sum()
        bit = k[k["_adet"] == 1]
        satirlar.append(
            f"{kisi}: Üretim {s('Üretim'):.2f} sa | Plansız duruş {s('Plansız_Duruş'):.2f} sa | "
            f"Set-up {s('Set_Up'):.2f} sa | Rework {s('Rework'):.2f} sa | Toplam {k['Süre'].sum():.2f} sa | "
            f"Biten parça: {len(bit)} adet")
        for gun, g in bit.groupby(bit["Tarih"].dt.date):
            satirlar.append(f"  {gun:%d.%m.%Y}: {len(g)} adet — " + ", ".join(
                f"{sn} ({tz}, {p} Op {o})" for sn, tz, p, o in zip(g["S/N"], g["Tezgah"], g["Parça"], g["Op"])))
    return "\n".join(satirlar)
def _groq_cagri() -> str:
    mesajlar = [{"role": "system", "content": SISTEM_PROMPTU}]
    mesajlar += st.session_state.sohbet_gecmisi[-7:]  # son birkaç mesaj yeter
    yanit = client.chat.completions.create(
        model=model_adi, messages=mesajlar, temperature=0.2, max_tokens=1500,
    )
    return yanit.choices[0].message.content or ""

SISTEM_KISA = (
    "Sen fabrika üretim asistanısın. Kullanıcı mesajındaki 'HESAPLANMIŞ SONUÇ' Python ile hesaplanmış "
    "kesin sonuçtur. Cevabını SADECE bu sonuca dayandır: rakamları aynen kullan, toplama/çıkarma yapma, "
    "başka tablo veya kaynak (TABLO 2, TABLO 21, GÜN-4, HAFTA vb.) ekleme, sonuçta olmayan bilgiyi uydurma. "
    "Türkçe, kısa ve düzenli özetle. Sonuçta olmayan bir şey sorulursa 'bu hesapta yok' de."
)

def _hesapli_mi() -> bool:
    g = st.session_state.sohbet_gecmisi
    return bool(g and g[-1].get("hesap"))

def _gemini_cagri(soru_metni: str) -> str:
    hesapli = _hesapli_mi()
    model = genai.GenerativeModel(
        model_name=gemini_model_adi,
        system_instruction=SISTEM_KISA if hesapli else SISTEM_PROMPTU,
    )
    gecmis = [] if hesapli else [
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
    try:
        hesap = _soru_hesabi(soru, hazirla(df_ham)[0])
    except Exception:
        hesap = ""
    st.session_state.sohbet_gecmisi.append(
        {"role": "user", "content": soru + ("\n\n" + hesap if hesap else ""), "gorunen": soru, "hesap": hesap})
    with st.chat_message("user"):
        st.markdown(soru)
        if hesap:
            st.info(hesap)
        

    try:    
        with st.chat_message("assistant"):
            with st.spinner("Veriler ve formüller analiz ediliyor..."):
                cevap, kullanilan = _cevap_uret(st.session_state.sohbet_gecmisi[-1]["content"])
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