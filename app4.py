# -*- coding: utf-8 -*-
"""
app4.py — Üretim Zekası Dashboard'u (sunum odaklı, şeffaf, doğrulanmış sürüm)

Bu sürümde (gerçek `puredata1.xlsx` verisiyle uçtan uca test edilerek) düzeltilenler:
  • OEE grafiği: "Gerçek OEE" katalog C/T eski olduğu için %105-142 çıkıyordu (imkânsız değer).
    Artık varsayılan grafik OEE Yaklaşık (Kullanılabilirlik × Kalite); Gerçek OEE uyarıyla seçilebilir.
  • Kronolojik tablo: TARİH+BAŞLANGIÇ_SAAT metin sıralaması gece vardiyasında (00:00-07:00)
    sırayı bozuyordu; artık gerçek zaman damgasına (Baslangic_Zaman) göre sıralanır.
  • Operatör emsal sapması: emsal medyanı artık TÜM veriden hesaplanır (dar tarih filtresinde
    kararsız olmasın diye) — sunumla doğrulanmış metodoloji.
  • CT metinleri sabit yazı değil, veriden ÜRETİLEN dinamik cümleler; adil (medyan tabanlı) kıyas eklendi.
  • Gerçek operatör isimleri: sidebar filtresi dahil her yerde.
  • Gizli modüller (Isı Haritası, Vardiya Yorgunluk, MTBF) SİLİNMEDİ: yorum satırında.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

from common import (
    RENK, DURUM_SIRASI, METRIC_REGISTRY, metrik_aciklamasi, kolon_var,
    veri_yukle_ve_filtrele, compute_tezgah_oee, compute_operator_karne,
    compute_pareto, compute_vardiya_yorgunluk, compute_mtbf_mttr, compute_heatmap,  # (gizli modüller için)
    compute_ct_guvenilirlik, compute_onerilen_ct, compute_ct_cozum_etkisi, compute_zaman_cizelgesi,
    compute_operator_kapasite,
    EMSAL_MIN_TEKRAR, CT_DUSUK_GUVEN_ORANI, KARNE_MIN_SAAT, KARNE_MIN_SAAT_GUNLUK,
)

try:  # ETL kurallarını (Durum haritası vb.) canlı göstermek için; yoksa sessizce atlanır
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
GUNCELLEME_ESIGI_YUZDE = 15      # compute_onerilen_ct: |Fark| >= %15 ise katalog güncellenmeli
KAPSAM_ESIGI = 0.5               # tezgah-günü kaydı, tezgahın tipik gününün %50'sinden azsa "düşük kapsam"
OEE_REFERANS = 85                # yaygın "dünya standardı" OEE referansı (%)

T_TEZGAH = "📊 Tezgah Özeti"
T_PARETO = "⏱️ Duruş Pareto ve Analizi"
T_OPERATOR = "👷 Operatör Karnesi"
T_KAPASITE = "💰 Kapasite Kaybı (Ödenen vs Loglanmış)"
T_KRONO = "📅 Kronolojik Üretim Dökümü"
T_CT = "⚠️ CT Güvenilirlik"
T_FORMUL = "📐 Metrikler ve Formüller"

SEKMELER = [
    T_TEZGAH, T_PARETO, T_OPERATOR, T_KAPASITE, T_KRONO, T_CT, T_FORMUL,
    # --- GİZLİ MODÜLLER (FAZ-2): yeniden açmak için aşağıdaki satırı yorumdan çıkarın ---
    # "🌡️ Parça-Tezgah Isı Haritası",
    # "🌙 Vardiya Yorgunluk",
    # "🔧 MTBF & MTTR",
]

KRONO_KOLONLAR = [
    ("TARİH", "Tarih"), ("VARDİYA", "Vardiya"), ("BAŞLANGIÇ_SAAT", "Başlangıç Saati"), ("BİTİŞ_SAAT", "Bitiş Saati"),
    ("TEZGAH", "Tezgah"), ("Operator_Kod", "Operatör (Gerçek İsim)"), ("PARÇA NO_ÜRETİM_GİRİLEN", "Parça No"),
    ("OPERASYON_NO", "Operasyon No"), ("Durum", "Durum"), ("Durus_Kategorisi", "Duruş Kategorisi"),
    ("Durus_Sebebi_Ham", "Duruş Sebebi"), ("Sure_Saat", "Gerçekleşen Süre (Saat)"), ("ÜRETİM_C/T", "Hedef C/T (Saat)"),
    ("Sapma_Saat", "Sapma (Saat)"), ("Sapma_Yuzdesi", "Sapma (%)"),
]


# =============================================================================
# YARDIMCI FONKSİYONLAR (saf pandas — Streamlit'ten bağımsız, test edilebilir)
# =============================================================================
def op_ad(kod):
    """Op_xxx kodunu gerçek isme çevirir; sözlükte yoksa kodu olduğu gibi bırakır."""
    return OPERATOR_SOZLUGU.get(kod, kod)

def sa(x, n: int = 2) -> str:
    return "—" if x is None or (isinstance(x, float) and np.isnan(x)) else f"{x:,.{n}f}"

@st.cache_data(show_spinner=False)
def zaman_hazirla(veri: pd.DataFrame) -> pd.DataFrame:
    """Her satıra gerçek Baslangic_Zaman/Bitis_Zaman ekler (gece yarısını geçen vardiyalar dahil)."""
    return compute_zaman_cizelgesi(veri)

def emsal_medyan_tablosu(df_ref: pd.DataFrame) -> pd.DataFrame:
    """Aynı 'KOD & OPE.' işini yapan TÜM operatörlerin medyan süresi (n >= EMSAL_MIN_TEKRAR)."""
    if not kolon_var(df_ref, "Durum", "KOD & OPE.", "Sure_Saat"):
        return pd.DataFrame(columns=["Emsal_Medyan_Saat", "Emsal_Kayit"])
    u = df_ref[df_ref["Durum"] == "Üretim"].dropna(subset=["KOD & OPE.", "Sure_Saat"])
    sayim = u.groupby("KOD & OPE.").size()
    guvenilir = sayim[sayim >= EMSAL_MIN_TEKRAR].index
    return (u[u["KOD & OPE."].isin(guvenilir)].groupby("KOD & OPE.")["Sure_Saat"]
            .agg(Emsal_Medyan_Saat="median", Emsal_Kayit="size"))

def operator_emsal_kirilimi(df: pd.DataFrame, df_ref: pd.DataFrame, operator_adi: str):
    """Bir operatörün emsal sapmasının KOD bazında dökümü. Dönen 'genel', karnedeki
    Emsal Sapması ile birebir aynı formülle (tüm satırların ortalaması) hesaplanır."""
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
    """Bir tezgahın bir günde kayıtlı toplam saati, o tezgahın tipik gününün %esik'inden azsa
    (eksik vardiya/veri girişi şüphesi) listeler. Sadece filtrelenmiş kapsam için."""
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

def medyan_aciklamasi(degerler) -> str:
    """Medyanın nasıl bulunduğunu sayılarla anlatan cümle."""
    v = sorted(float(x) for x in degerler)
    n = len(v)
    if n == 0:
        return ""
    if n % 2 == 1:
        return f"{n} kayıt (tek sayı) → küçükten büyüğe sıralanınca ortadaki ({n // 2 + 1}.) değer = **{v[n // 2]:.2f} sa**"
    a, b = v[n // 2 - 1], v[n // 2]
    return f"{n} kayıt (çift sayı) → ortadaki iki değerin ({n // 2}. = {a:.2f} ve {n // 2 + 1}. = {b:.2f}) ortalaması = **{(a + b) / 2:.2f} sa**"

def ct_kanit_hesapla(df: pd.DataFrame) -> dict:
    """C/T analizi için sayısal kanıt paketi (hepsi filtrelenmiş veriden canlı hesaplanır)."""
    sonuc: dict = {"gecerli": False}
    if df.empty or not kolon_var(df, "Durum", "KOD & OPE.", "Sure_Saat", "ÜRETİM_C/T", "Operator_Kod", "TEZGAH"):
        return sonuc
    uk = df[df["Durum"] == "Üretim"].dropna(subset=["KOD & OPE.", "Sure_Saat"])
    u = uk[uk["ÜRETİM_C/T"].notna() & (uk["ÜRETİM_C/T"] > 0)]
    if u.empty:
        return sonuc
    sonuc.update(gecerli=True, satir_sayisi=int(len(u)), kod_sayisi=int(u["KOD & OPE."].nunique()),
                 katalog_farkli_deger=int(u["ÜRETİM_C/T"].round(2).nunique()))
    vc = u["ÜRETİM_C/T"].round(2).value_counts()
    sonuc["en_sik_deger"], sonuc["en_sik_pay"] = float(vc.index[0]), round(float(vc.iloc[0] / len(u) * 100), 1)

    onerilen = compute_onerilen_ct(df)
    tablo = pd.DataFrame()
    if not onerilen.empty:
        ist = uk.groupby("KOD & OPE.").agg(Ort_Sure=("Sure_Saat", "mean"), Min_Sure=("Sure_Saat", "min"), Max_Sure=("Sure_Saat", "max"),
                                            Usta_Sayisi=("Operator_Kod", "nunique"), Tezgah_Sayisi=("TEZGAH", "nunique"))
        tablo = onerilen.merge(ist, left_on="KOD & OPE.", right_index=True, how="left")
        tablo["Fark_Saat"] = (tablo["Onerilen_CT_Saat"] - tablo["Katalog_CT_Saat"]).round(2)
        tablo[["Ort_Sure", "Min_Sure", "Max_Sure"]] = tablo[["Ort_Sure", "Min_Sure", "Max_Sure"]].round(2)
        med = tablo.set_index("KOD & OPE.")["Onerilen_CT_Saat"]
        x = uk.assign(m=uk["KOD & OPE."].map(med)).dropna(subset=["m"])
        x = x[x["m"] > 0]
        if not x.empty:
            sonuc["kisa_kayit_pay"] = round(float((((x["Sure_Saat"] - x["m"]) / x["m"] * 100) < -50).mean() * 100), 1)
    sonuc["tablo"] = tablo
    fl = tablo[tablo["Guncelleme_Onerisi"]] if not tablo.empty else tablo
    sonuc["guncelle_sayisi"] = int(len(fl))
    sonuc["yavas_katalog"] = int((fl["Fark_Yuzde"] < 0).sum()) if len(fl) else 0   # katalog gerçekten yavaş (gevşek)
    sonuc["siki_katalog"] = int((fl["Fark_Yuzde"] > 0).sum()) if len(fl) else 0    # katalog gerçekten hızlı (sıkı)
    return sonuc

def rontgen_satirlari(r: pd.Series, emsal: pd.DataFrame):
    """Kronolojik tabloda seçilen bir kaydın 'röntgeni': alan/değer listesi + yorum cümleleri."""
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
    ct, sap, sapy = g("ÜRETİM_C/T"), g("Sapma_Saat"), g("Sapma_Yuzdesi")
    if g("Durum") == "Üretim" and ct and sure is not None:
        yon = "hızlı" if sure < ct else "yavaş"
        yorum.append(f"Katalog hedefi **{ct:.2f} sa**, gerçekleşen **{sure:.2f} sa** → hedeften **{abs(sure - ct):.2f} sa ({abs(sure - ct) / ct * 100:.1f}%) {yon}**. "
                     f"(Sapma = ({sure:.2f} − {ct:.2f}) / {ct:.2f} × 100 = {(sure - ct) / ct * 100:+.1f}%)")
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

# HATA ÇÖZÜMÜ: veri_yukle_ve_filtrele artık operator_isimleri parametresi beklemiyor.
df_ham, df, kaynak_notu = veri_yukle_ve_filtrele("home")
st.caption(kaynak_notu)

if df.empty:
    st.warning("Seçili filtrelerle eşleşen veri yok.")
    st.stop()

# GERÇEK İSİMLERİ VERİ YÜKLENDİKTEN SONRA UYGULAYALIM (Güvenli Yöntem)
if "Operator_Kod" in df_ham.columns:
    bilinmeyen = sorted(set(df_ham["Operator_Kod"].dropna()) - set(OPERATOR_SOZLUGU))
    if bilinmeyen:
        st.sidebar.warning(f"Sözlükte olmayan operatör kodu (kod olarak gösterilir): {', '.join(bilinmeyen)}")
    
    # Hem ekranda görünen (df) hem de arka planda emsal hesabında kullanılan (df_ham) tablolardaki isimleri dönüştür
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
planlanan = toplam_uretim + toplam_durus + toplam_rework
kullanilabilirlik = (toplam_uretim + toplam_rework) / planlanan * 100 if planlanan > 0 else 0

k1, k2, k3, k4, k5 = st.columns(5)
k1.metric("Toplam Üretim Saati", f"{toplam_uretim:,.1f} sa", help="Durum = 'Üretim' olan kayıtların Sure_Saat toplamı")
k2.metric("Toplam Duruş Saati", f"{toplam_durus:,.1f} sa", help="Durum = 'Plansız_Duruş' olan kayıtların Sure_Saat toplamı")
k3.metric("Setup Saati", f"{toplam_setup:,.1f} sa", help="Durum = 'Set_Up' olan kayıtların Sure_Saat toplamı")
k4.metric("Rework Saati", f"{toplam_rework:,.1f} sa", help="Durum = 'Rework' olan kayıtların Sure_Saat toplamı")
k5.metric("Kullanılabilirlik", f"%{kullanilabilirlik:,.1f}", help="(Üretim + Rework) / (Üretim + Plansız Duruş + Rework) × 100")

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
        for oran in ["Kullanilabilirlik", "Kalite_Orani", "OEE_Yaklasik", "Performans_Orani", "Gercek_OEE"]:
            if oran in g.columns:
                g[oran] = (g[oran] * 100).round(1)

        oee_secenek = {"OEE Yaklaşık = Kullanılabilirlik × Kalite  (önerilen)": "OEE_Yaklasik"}
        if "Gercek_OEE" in g.columns:
            oee_secenek["Gerçek OEE = Kullanılabilirlik × Performans × Kalite  ⚠️ katalog C/T'ye bağlı"] = "Gercek_OEE"
        oee_etiket = st.radio("Grafikte gösterilecek OEE türü", list(oee_secenek), horizontal=True, key="oee_tur")
        oee_kol = oee_secenek[oee_etiket]

        c1, c2 = st.columns([1, 2])
        with c1:
            durum_toplam = df.groupby("Durum")["Sure_Saat"].sum().reset_index()
            fig_pie = px.pie(durum_toplam, values="Sure_Saat", names="Durum", title="Fabrika Geneli Zaman Dağılımı", color="Durum",
                             color_discrete_map={k: RENK.get(k, "#BDBDBD") for k in durum_toplam["Durum"]}, hole=0.4, height=450)
            fig_pie.update_traces(textposition="inside", textinfo="percent+label", textfont_size=14)
            st.plotly_chart(fig_pie, use_container_width=True)
        with c2:
            sirali = g.sort_values(oee_kol, ascending=True)
            max_deger = float(sirali[oee_kol].max())
            fig_oee = px.bar(sirali, x=oee_kol, y="TEZGAH", orientation="h", text_auto=".1f", color=oee_kol,
                             color_continuous_scale="RdYlGn", range_color=[50, 100], height=480,
                             title=f"Tezgah OEE Skorları — {'OEE Yaklaşık' if oee_kol == 'OEE_Yaklasik' else 'Gerçek OEE'}")
            fig_oee.add_vline(x=OEE_REFERANS, line_dash="dash", line_color="red", annotation_text=f"Referans %{OEE_REFERANS}", annotation_font_size=12)
            if max_deger > 100:
                fig_oee.add_vline(x=100, line_dash="solid", line_color="black", annotation_text="Teorik üst sınır %100", annotation_position="bottom right")
            fig_oee.update_layout(xaxis_title="OEE (%)", yaxis_title="Tezgahlar", xaxis=dict(range=[0, max(105, max_deger + 8)]), font=dict(size=14))
            st.plotly_chart(fig_oee, use_container_width=True)
            if oee_kol == "Gercek_OEE":
                asan = int((g["Gercek_OEE"] > 100).sum())
                if asan:
                    st.warning(f"⚠️ {asan}/{len(g)} tezgahta 'Gerçek OEE' %100'ü aşıyor. Bu imkânsızdır: **Performans oranı katalog C/T'ye bağlıdır ve katalog gevşektir** "
                               f"(bkz. '{T_CT}' sekmesi). Mutlak değere değil, tezgahlar arası sıralamaya bakın.")
            else:
                st.caption("OEE Yaklaşık, hız (performans) bileşenini içermez; bu yüzden gerçek OEE'nin **üst sınırıdır**. Ayrıntı: "
                           f"'{T_FORMUL}' sekmesi.")

        st.markdown("##### Tezgah Başına Detaylı Tablo")
        gost = g.rename(columns={
            "Uretim_Saat": "Üretim (Sa)", "Plansiz_Durus_Saat": "Plansız Duruş (Sa)", "Set_Up_Saat": "Set-Up (Sa)", "Yemek_Saat": "Yemek Mola (Sa)",
            "Rework_Saat": "Rework (Sa)", "Kayit_Sayisi": "Kayıt Sayısı", "Planlanan_Sure": "Planlanan Süre (Sa)",
            "Kullanilabilirlik": "Kullanılabilirlik (%)", "Kalite_Orani": "Kalite Oranı (%)", "OEE_Yaklasik": "OEE Yaklaşık (%)",
            "Performans_Orani": "Performans Oranı (%)", "Gercek_OEE": "Gerçek OEE (%)", "En_Buyuk_Kayip_Kategorileri": "En Büyük Kayıp Kategorileri",
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
    karne = compute_operator_karne(df, emsal_referans_df=df_ham)
    if karne.empty:
        st.info("Yeterli veri yok (min. saat eşiğini geçen operatör bulunamadı).")
    else:
        esik, gun = karne.attrs.get("efektif_esik_saat"), karne.attrs.get("gun_sayisi")
        uretimde_olan = df.loc[df["Durum"] == "Üretim", "Operator_Kod"].nunique()
        st.caption(
            f"ℹ️ {len(karne)} operatör karnede (eşik: en az **{esik:g} sa** üretim — {gun} günlük aralık için) · "
            f"eşik altında kalan: {max(uretimde_olan - len(karne), 0)} · emsal referansı: **tüm veri**, en az {EMSAL_MIN_TEKRAR} tekrarlı kodlar."
        )
        kg = karne.rename(columns={
            "Operator_Kod": "Operatör", "Toplam_Saat": "Toplam Saat", "Islem_Sayisi": "İşlem Sayısı", "Islem_Saat_Hizi": "İşlem / Saat Hızı",
            "CT_Bazli_Sapma_Yuzde": "CT Sapması (%)", "CT_Dusuk_Guven_Satir_Sayisi": "Düşük Güven (Satır)",
            "CT_Dusuk_Guven_Yuzde": "Düşük Güven (%)", "Emsal_Bazli_Sapma_Yuzde": "Emsal Sapması (%)",
        })
        if "Emsal Sapması (%)" in kg.columns:
            ks = kg.sort_values("Emsal Sapması (%)", ascending=False)
            fig_op = px.bar(ks, x="Emsal Sapması (%)", y="Operatör", orientation="h", title="Operatör Hız Kıyaslaması (Emsal Medyanına Göre)",
                            text_auto=".1f", color="Emsal Sapması (%)", color_continuous_scale="RdYlGn_r", height=max(450, 28 * len(ks) + 120))
            fig_op.add_vline(x=0, line_dash="solid", line_color="black", annotation_text="Emsal Medyanı ", annotation_font_size=12)
            fig_op.update_layout(xaxis_title="Emsal Sapması (%) [Negatif = emsallerinden hızlı]", yaxis_title="Operatörler", font=dict(size=14),
                                 yaxis=dict(autorange="reversed"))
            st.plotly_chart(fig_op, use_container_width=True)
            
        st.dataframe(kg, use_container_width=True, hide_index=True, column_config={
            "CT Sapması (%)": st.column_config.NumberColumn(format="%.1f%%"), "Emsal Sapması (%)": st.column_config.NumberColumn(format="%.1f%%"),
            "Düşük Güven (%)": st.column_config.NumberColumn(format="%.1f%%"), "Toplam Saat": st.column_config.NumberColumn(format="%.2f"),
            "İşlem / Saat Hızı": st.column_config.NumberColumn(format="%.3f")})

        st.markdown("#### 🔍 Bir operatörün emsal sapması nereden geliyor?")
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
            st.caption(f"**{secilen_op}** — bu tablodaki tüm işlem satırlarının ortalama emsal sapması: **{genel:+.1f}%** · karnedeki değer: **{karnedeki:+.1f}%** → {uyum}. "
                       f"Her satır için: (gerçek süre − emsal medyan) / emsal medyan × 100.")

# =============================================================================
# SEKME 3b: KAPASİTE KAYBI (Ödenen vs Loglanmış Saat)
# =============================================================================
with sekme[T_KAPASITE]:
    st.subheader("💰 Operatöre Ödenen Saat, Sistemde Nereye Gidiyor?")
    st.caption(
        "Bir operatöre mesaisi boyunca (vardiya süresi kadar) ödeme yapılır. Ama sistemde o operatöre ait "
        "kayıtlı (Üretim + Duruş + Setup + Yemek + Rework) toplam süre, vardiya süresinden azsa, aradaki fark "
        "**hiçbir yere loglanmamış zamandır** — nerede olduğu bilinmiyor demektir."
    )
    kap = compute_operator_kapasite(df)
    if kap.empty:
        st.info("Bu filtrede kapasite analizi için yeterli veri yok (VARDİYA/Vardiya_Uzunluk_Saat gerekli).")
    else:
        toplam_odenen, toplam_kayip = float(kap["Odenen_Saat"].sum()), float(kap["Kayip_Saat"].sum())
        f1, f2, f3, f4 = st.columns(4)
        f1.metric("Toplam Ödenen (Mesai) Saat", f"{toplam_odenen:,.0f} sa")
        f2.metric("Toplam Kayıp (Loglanmamış) Saat", f"{toplam_kayip:,.0f} sa", delta=f"%{toplam_kayip / toplam_odenen * 100:.1f} pay" if toplam_odenen > 0 else None, delta_color="inverse")
        f3.metric("Ortalama Kayıp Oranı", f"%{kap['Kayip_Yuzde'].mean():.1f}")
        f4.metric("En Yüksek Kayıp", f"{kap.iloc[0]['Operatör'] if 'Operatör' in kap.columns else kap.iloc[0]['Operator_Kod']}", delta=f"{kap.iloc[0]['Kayip_Saat']:.1f} sa (%{kap.iloc[0]['Kayip_Yuzde']:.1f})", delta_color="off")

        en = kap.iloc[0]
        isim = en["Operatör"] if "Operatör" in kap.columns else en["Operator_Kod"]
        st.markdown(
            f"**Örnek okuma ({isim}):** {int(en['Vardiya_Sayisi'])} vardiyada toplam **{en['Odenen_Saat']:.1f} sa** mesai yapmış "
            f"(ödenen), sistemde **{en['Loglanmis_Saat']:.1f} sa** kayıt var → **{en['Kayip_Saat']:.1f} sa (%{en['Kayip_Yuzde']:.1f})** "
            f"hiçbir kayda düşmemiş. `Kayip_Saat = Odenen_Saat − Loglanmis_Saat = {en['Odenen_Saat']:.1f} − {en['Loglanmis_Saat']:.1f} = {en['Kayip_Saat']:.1f} sa`."
        )

        kg2 = kap.rename(columns={
            "Operator_Kod": "Operatör", "Vardiya_Sayisi": "Vardiya (n)", "Odenen_Saat": "Ödenen (Sa)", "Loglanmis_Saat": "Loglanmış (Sa)",
            "Uretim_Saat": "Üretim (Sa)", "Plansiz_Durus_Saat": "Plansız Duruş (Sa)", "Set_Up_Saat": "Set-Up (Sa)", "Yemek_Saat": "Yemek (Sa)",
            "Rework_Saat": "Rework (Sa)", "Kayip_Saat": "Kayıp (Sa)", "Kayip_Yuzde": "Kayıp (%)", "Uretkenlik_Yuzde": "Üretkenlik (%)",
            "Fazla_Loglanan_Saat": "Fazla Loglanan (Sa) ⚠️",
        }) if "Operatör" not in kap.columns else kap

        fig_kap = px.bar(kg2.sort_values("Kayıp (Sa)", ascending=True), x="Kayıp (Sa)", y="Operatör", orientation="h",
                         color="Kayıp (%)", color_continuous_scale="Reds", text_auto=".1f", height=max(420, 28 * len(kg2) + 120),
                         title="Operatör Bazında Kayıp (Loglanmamış) Saat")
        fig_kap.update_layout(xaxis_title="Kayıp Saat", yaxis_title="Operatörler", font=dict(size=14))
        st.plotly_chart(fig_kap, use_container_width=True)

        st.dataframe(kg2, use_container_width=True, hide_index=True, column_config={
            "Kayıp (%)": st.column_config.NumberColumn(format="%.1f%%"), "Üretkenlik (%)": st.column_config.NumberColumn(format="%.1f%%"),
            "Ödenen (Sa)": st.column_config.NumberColumn(format="%.2f"), "Loglanmış (Sa)": st.column_config.NumberColumn(format="%.2f"),
            "Kayıp (Sa)": st.column_config.NumberColumn(format="%.2f")})

        fazla = kg2[kg2["Fazla Loglanan (Sa) ⚠️"] > 0.01] if "Fazla Loglanan (Sa) ⚠️" in kg2.columns else pd.DataFrame()
        if not fazla.empty:
            st.warning(f"⚠️ {len(fazla)} operatörde loglanmış süre vardiya süresini AŞIYOR (örtüşen vardiya kaydı ya da veri girişi hatası olabilir) — 'Fazla Loglanan' kolonuna bakın.")

        with st.expander("📖 Bu tablo nasıl hesaplandı? / Günlük detaya in"):
            st.markdown(metrik_aciklamasi("operator_kapasite_kaybi"))
            st.caption("Bir operatörün GÜNLÜK/vardiya bazlı kırılımı için 'Operatör Karnesi' sekmesindeki isimle Kronolojik Döküm sekmesinde filtre uygulayın.")

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
    except TypeError:   # eski Streamlit sürümü: satır seçimi yok
        st.dataframe(tablo, use_container_width=True, hide_index=True, height=520, column_config=cfg)
        st.caption("ℹ️ Satır seçimi (röntgen) için Streamlit ≥ 1.35 gerekir.")
    st.download_button("⬇️ Tabloyu CSV indir", tablo.to_csv(index=False).encode("utf-8-sig"), "kronolojik_uretim_dokumu.csv", "text/csv")

    if len(tablo) == len(z):   # filtre yoksa KPI ile mutabakat
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
# SEKME 5: CT GÜVENİLİRLİK
# =============================================================================
with sekme[T_CT]:
    st.subheader("⚠️ Katalog C/T (Cycle Time) Analizi ve Kanıtlar")
    st.caption("Katalog C/T = her 'Kod & Operasyon' için tanımlı hedef süre (Excel'deki ÜRETİM C/T). Aşağıdaki her cümle ve sayı, seçili veriden canlı hesaplanır.")
    ct = compute_ct_guvenilirlik(df)
    kanit = ct_kanit_hesapla(df)
    etki = compute_ct_cozum_etkisi(df)
    if not ct.get("gecerli") or not kanit.get("gecerli"):
        st.info("Bu filtrede CT analizi için yeterli veri yok.")
    else:
        tablo_ct = kanit["tablo"]
        n_kod, n_fl, yavas, siki = (len(tablo_ct), kanit["guncelle_sayisi"], kanit["yavas_katalog"], kanit["siki_katalog"])
        kat_op, emsal_op = etki.get("operator_katalog_hizli_yuzde"), etki.get("operator_onerilen_hizli_yuzde")

        m1, m2, m3, m4 = st.columns(4)
        m1.metric("Sistemsel Kayma Puanı", f"{ct['sistemsel_kayma_puan']:+.1f}" if ct.get("sistemsel_kayma_puan") is not None else "—",
                  help="Ort. CT sapması − Ort. Emsal sapması (operatör bazında). Negatif ve büyükse katalog, ustaların gerçek hızından yavaş (gevşek).")
        m2.metric("CT'den Hızlı Görünen Operatör Oranı", f"%{ct['negatif_ct_sapma_orani']:.1f}" if ct.get("negatif_ct_sapma_orani") is not None else "—",
                  help="Ortalama CT sapması negatif olan operatörlerin oranı. Sağlıklı bir katalogda ~%50 beklenir. (Ortalama-tabanlı; adil kıyas yandaki metrikte.)")
        m3.metric("Adil Kıyas: Katalog ↔ Emsal Medyan", f"%{kat_op:.0f} ↔ %{emsal_op:.0f}" if kat_op is not None and emsal_op is not None else "—",
                  help="Operatörün MEDYAN sapması negatif olanların oranı: katalogla ↔ emsal medyanıyla. Medyan referansta ~%50 beklenir; katalogdaki fazlalık = sistematik gevşeklik.")
        m4.metric("Güncelleme Gereken Kod", f"{n_fl} / {n_kod}", help=f"Katalog ile gerçekleşen medyan arasında ≥%{GUNCELLEME_ESIGI_YUZDE} fark olan kodlar (n ≥ {EMSAL_MIN_TEKRAR} kayıt)")

        fark_puan = (kat_op - emsal_op) if kat_op is not None and emsal_op is not None else None
        if (fark_puan is not None and fark_puan >= 20) or (yavas >= 3 and siki == 0):
            st.error(f"🚨 **Katalog C/T güncel değil — sistematik olarak gevşek.** Katalogla operatörlerin %{kat_op:.0f}'i tipik olarak hedeften hızlıyken, aynı operatörler emsal medyana göre %{emsal_op:.0f} düzeyinde "
                     f"(medyan referansta ~%50 beklenir). Güncelleme gereken {n_fl} kodun **{yavas}'inde katalog gerçekte olandan yavaş, {siki}'inde hızlı** — sapma tek yönlü. "
                     "Bu, operatörlerin 'çok hızlı' olduğunu değil, kağıt üzerindeki hedeflerin gevşekliğini gösterir." if fark_puan is not None else
                     f"🚨 Güncelleme gereken {n_fl} kodun {yavas}'inde katalog gerçekte olandan yavaş, {siki}'inde hızlı — sapma tek yönlü; katalog gevşek görünüyor.")
        elif n_fl == 0:
            st.success("✅ Seçili kapsamda katalog ile gerçekleşen medyan arasında belirgin (≥%15) fark yok.")
        else:
            st.warning(f"⚠️ {n_fl} kodda katalog ile gerçekleşen medyan belirgin ayrışıyor ({yavas} gevşek, {siki} sıkı). Sistematik bir yön görünmüyor; kod bazında inceleyin.")

        st.markdown("#### 🧾 Kanıtlar (hepsi veriden hesaplanır)")
        kanitlar = []
        if n_fl:
            kanitlar.append(f"**1) Tek yönlü sapma:** Katalogla gerçekleşen medyan arasında ≥%{GUNCELLEME_ESIGI_YUZDE} fark olan **{n_fl}** kodun **{yavas}'inde katalog yavaş, {siki}'inde hızlı**. "
                            "Hatalar rastgele olsaydı iki yönde dağılırdı." + (" Tümünün aynı yönde çıkma olasılığı çok düşüktür (≈ 0.5^" + str(n_fl) + ")." if siki == 0 and yavas == n_fl else ""))
        if kat_op is not None:
            kanitlar.append(f"**2) Adil (medyan bazlı) kıyas:** {etki['operator_sayisi']} operatörün **%{kat_op:.0f}'i** katalogdan tipik olarak hızlı; aynı operatörlerin emsal medyana göre oranı **%{emsal_op:.0f}** "
                            f"(beklenen ≈%50). Fark: **{kat_op - emsal_op:+.0f} puan**.")
        if etki.get("satir_katalog_hizli_yuzde") is not None:
            kanitlar.append(f"**3) Kayıt düzeyi:** Kayıtların **%{etki['satir_katalog_hizli_yuzde']:.1f}'i** katalogdan hızlı; emsal medyanda bu oranı %{etki['satir_onerilen_hizli_yuzde']:.1f} "
                            f"(medyanda ≈%50 tanım gereğidir). Fark ≈ **{etki['satir_katalog_hizli_yuzde'] - etki['satir_onerilen_hizli_yuzde']:+.1f} puan**.")
        kanitlar.append(f"**4) Katalog yapısı:** {kanit['kod_sayisi']} farklı kod, yalnızca **{kanit['katalog_farkli_deger']}** farklı C/T değerini paylaşıyor; en sık değer **{kanit['en_sik_deger']:.2f} sa** "
                        f"(kayıtların %{kanit['en_sik_pay']:.1f}'i). Hedefler parça/tezgah bazında ayrıştırılmamış, şablon gibi görünüyor.")
        if n_fl:
            ilk = tablo_ct.iloc[0]
            kanitlar.append(f"**5) Uç örnek:** **{ilk['KOD & OPE.']}** — katalog **{ilk['Katalog_CT_Saat']:.2f} sa**, ustaların gerçekleşen medyanı **{ilk['Onerilen_CT_Saat']:.2f} sa** "
                            f"({int(ilk['Islem_Sayisi'])} kayıt, {int(ilk['Usta_Sayisi'])} usta) → fark **{ilk['Fark_Yuzde']:+.1f}%**.")
        for k in kanitlar:
            st.markdown(f"- {k}")

        with st.expander("ℹ️ Bu ölçüler neden birlikte okunmalı? (dürüst not)"):
            kisa = kanit.get("kisa_kayit_pay")
            st.markdown(
                "**Ortalama-tabanlı** ölçü (ilk metrik) tek başına yanıltıcı olabilir: kayıt süreleri sol çarpıktır"
                + (f" (kayıtların **%{kisa:.1f}'i** kod medyanının −%50 altında — vardiya bölünmesi/kısmi kayıtlar)" if kisa is not None else "")
                + ", bu yüzden ortalama yüzde sapma referans ne olursa olsun negatife kayar"
                + (f" (emsal medyan referansıyla bile ortalama-tabanlı oran %{etki.get('operator_onerilen_hizli_yuzde_ortalama', float('nan')):.0f})" if etki.get("operator_onerilen_hizli_yuzde_ortalama") is not None else "")
                + ". Bu nedenle **medyan-tabanlı adil kıyası** ve **kod bazlı tabloyu** esas alın. "
                "Ayrıca satır düzeyinde 'medyandan hızlı' oranı tanım gereği ≈%50'dir; yani önerilen C/T ile ≈%50 çıkması bir *kanıt* değil, referansın dengeli olduğunun göstergesidir."
            )

        st.markdown("---")
        st.subheader("✅ Veri Odaklı Çözüm: Önerilen C/T")
        st.info("Aşağıdaki değerler, hatalı katalog süreleri yerine ustalarımızın **fiili üretim medyanlarından** hesaplanmış gerçekçi hedeflerdir. "
                "Medyan, ortalamadan farklı olarak tekil aykırı kayıtlardan etkilenmez. Başlangıç noktasıdır — nihai standart süreç mühendisi onayıyla belirlenmelidir.")
        if tablo_ct.empty:
            st.info(f"Önerilen C/T için yeterli tekrarlı (n ≥ {EMSAL_MIN_TEKRAR}) kod yok.")
        else:
            top_n = st.slider("Kaç kodun ayrıntı kartı gösterilsin?", 3, 15, 6, key="ct_topn")
            kartlar = tablo_ct[tablo_ct["Guncelleme_Onerisi"]].head(top_n)
            for _, r in kartlar.iterrows():
                yorum = "**Katalog gerçekte olandan yavaş — güncellenmeli!**" if r["Fark_Yuzde"] < 0 else "Katalog gerçekte olandan hızlı (sıkı) — gerçekçi mi kontrol edin."
                with st.container(border=True):
                    st.markdown(
                        f"**{r['KOD & OPE.']}** — Katalog: **{r['Katalog_CT_Saat']:.2f} sa** · Gerçekleşen Ustaların Medyanı: **{r['Onerilen_CT_Saat']:.2f} sa** "
                        f"(ort. {r['Ort_Sure']:.2f}; en hızlı {r['Min_Sure']:.2f}, en yavaş {r['Max_Sure']:.2f}) · {int(r['Islem_Sayisi'])} kayıt, {int(r['Usta_Sayisi'])} usta, {int(r['Tezgah_Sayisi'])} tezgah  \n"
                        f"Fark: **{r['Fark_Yuzde']:+.1f}%** ({r['Fark_Saat']:+.2f} sa) → {yorum}"
                    )
            fl = tablo_ct[tablo_ct["Guncelleme_Onerisi"]].head(15)
            if not fl.empty:
                figc = go.Figure()
                figc.add_bar(y=fl["KOD & OPE."], x=fl["Katalog_CT_Saat"], name="Katalog C/T", orientation="h", marker_color="#C62828")
                figc.add_bar(y=fl["KOD & OPE."], x=fl["Onerilen_CT_Saat"], name="Önerilen C/T (Medyan)", orientation="h", marker_color="#2E7D32")
                figc.update_layout(barmode="group", title="Katalog vs Gerçekleşen Medyan (güncelleme önerilen kodlar)", xaxis_title="Saat", yaxis=dict(autorange="reversed"),
                                   height=max(420, 34 * len(fl) + 140), font=dict(size=14))
                st.plotly_chart(figc, use_container_width=True)

            st.markdown("##### Tüm kodlar — Katalog ↔ Önerilen C/T")
            tg = tablo_ct.copy()
            tg["Durum"] = np.where(tg["Guncelleme_Onerisi"], np.where(tg["Fark_Yuzde"] < 0, "🔴 Güncelle (katalog yavaş)", "🟠 Güncelle (katalog hızlı)"), "🟢 Uygun")
            tg = tg[["KOD & OPE.", "Katalog_CT_Saat", "Onerilen_CT_Saat", "Fark_Saat", "Fark_Yuzde", "Ort_Sure", "Min_Sure", "Max_Sure", "Islem_Sayisi", "Usta_Sayisi", "Tezgah_Sayisi", "Durum"]].rename(columns={
                "KOD & OPE.": "Kod & Operasyon", "Katalog_CT_Saat": "Katalog C/T (Sa)", "Onerilen_CT_Saat": "Önerilen C/T — Medyan (Sa)", "Fark_Saat": "Fark (Sa)",
                "Fark_Yuzde": "Fark (%)", "Ort_Sure": "Ort. Gerçekleşen (Sa)", "Min_Sure": "En Hızlı (Sa)", "Max_Sure": "En Yavaş (Sa)",
                "Islem_Sayisi": "Kayıt (n)", "Usta_Sayisi": "Usta Sayısı", "Tezgah_Sayisi": "Tezgah Sayısı"})
            st.dataframe(tg, use_container_width=True, hide_index=True, column_config={
                "Fark (%)": st.column_config.NumberColumn(format="%+.1f%%"), "Fark (Sa)": st.column_config.NumberColumn(format="%+.2f")})
            st.download_button("⬇️ Katalog güncelleme listesini CSV indir", tg.to_csv(index=False).encode("utf-8-sig"), "onerilen_ct_listesi.csv", "text/csv")

            st.markdown("##### 🔬 Bir kodu mercek altına al")
            kod_sec = st.selectbox("Kod & Operasyon", tablo_ct["KOD & OPE."].tolist(), key="ct_kod")
            satir = tablo_ct[tablo_ct["KOD & OPE."] == kod_sec].iloc[0]
            kayitlar = df[(df["Durum"] == "Üretim") & (df["KOD & OPE."] == kod_sec)].dropna(subset=["Sure_Saat"])
            figk = px.strip(kayitlar, x="Operator_Kod", y="Sure_Saat", color="TEZGAH", hover_data=[c for c in ["TARİH", "BAŞLANGIÇ_SAAT", "Sapma_Yuzdesi"] if c in kayitlar.columns],
                            title=f"{kod_sec} — her nokta bir üretim kaydı", height=480)
            figk.add_hline(y=float(satir["Katalog_CT_Saat"]), line_dash="dash", line_color="red", annotation_text=f"Katalog {satir['Katalog_CT_Saat']:.2f} sa")
            figk.add_hline(y=float(satir["Onerilen_CT_Saat"]), line_color="green", annotation_text=f"Medyan {satir['Onerilen_CT_Saat']:.2f} sa", annotation_position="bottom right")
            figk.update_layout(xaxis_title="Operatör", yaxis_title="Süre (saat)", font=dict(size=14))
            st.plotly_chart(figk, use_container_width=True)
            st.caption("Medyan nasıl bulundu? " + medyan_aciklamasi(kayitlar["Sure_Saat"]))

        with st.expander("⚖️ Bu analizin sınırları"):
            st.markdown(
                "- Veride **üretilen adet yoktur**; `Sure_Saat` bir üretim *kaydının* süresidir (bir kayıt birden fazla parça/parti içerebilir, vardiya sonunda bölünebilir). "
                "Katalog C/T ile aynı birimde kıyaslanır ama parça-başı normalizasyon yapılamaz.\n"
                f"- Önerilen C/T yalnızca en az **{EMSAL_MIN_TEKRAR} kayıtlı** kodlar için üretilir; az kayıtlı kodlarda öneri yoktur.\n"
                "- Medyan *tipik* performansı yansıtır; hedefin ne kadar zorlayıcı olacağı (ör. en hızlı %25) mühendislik kararıdır.\n"
                "- Katalog ile gerçek arasındaki fark bazen ürün karışımı/tezgah farkından da doğabilir; kod bazlı tabloda tezgah sayısı verilmiştir."
            )
        agn = ct.get("tezgah_agnostik_kodlar")
        if agn is not None and not agn.empty:
            with st.expander(f"🔍 Tezgah-agnostik {len(agn)} kod: C/T sabit ama tezgahlar arası süre ≥%15 farklı"):
                st.caption("Bu kodlarda katalog, hangi tezgahın daha hızlı/yavaş olduğunu hiç hesaba katmıyor: ölçülen şey operatör değil, atandığı tezgah olabilir.")
                st.dataframe(agn, use_container_width=True, hide_index=True)

# =============================================================================
# SEKME 6: METRİKLER VE FORMÜLLER (ŞEFFAFLIK)
# =============================================================================
with sekme[T_FORMUL]:
    st.subheader("📐 Hesaplama Mantığı, Formüller ve Şeffaflık")
    st.markdown("Ekrandaki hiçbir sayı tahmin değildir: hepsi aşağıdaki formüllerle, veritabanındaki belirtilen kolonlardan hesaplanır. "
                "Her bölümde **formül**, **Python karşılığı**, **kullanılan kolonlar** ve **şu an ekrandaki veriyle canlı örnek** vardır.")

    st.markdown("### 🗺️ Veri akışı")
    st.markdown("`Excel ('Giriş' sayfası)` → **ETL** (temizlik, Durum/kategori tespiti, C/T sapması) → `production.db` (tablo: `detay_uretim_loglari`) → **Bu dashboard** (filtre → hesaplama → gösterim).")
    with st.expander("Kolon sözlüğü — hangi kolon ne demek?"):
        st.dataframe(pd.DataFrame([
            ("TARİH", "Vardiyanın başladığı gün", "Excel 'TARİH'"),
            ("VARDİYA", "Vardiya aralığı (örn. 19:00 - 07:00)", "Excel 'VARDİYA'"),
            ("BAŞLANGIÇ_SAAT / BİTİŞ_SAAT", "Kaydın saat aralığı (dakika çözünürlüğünde)", "Excel"),
            ("TEZGAH", "Tezgah kodu", "Excel 'TEZGAH'"),
            ("Operator_Kod", "Operatör (kalıcı kod; ekranda sözlükle gerçek isme çevrilir)", "ETL: isim normalize + kalıcı Op_xxx kodu"),
            ("Durum", "Üretim / Plansız_Duruş / Set_Up / Yemek_Mola / Rework", "ETL: Excel'deki tek harf (Ü,D,S,Y,R) + 'KOD & OPE.' anahtar kelime düzeltmeleri"),
            ("Sure_Saat", "Kaydın süresi (saat)", "Excel 'SÜRE SAAT-1'"),
            ("KOD & OPE.", "Parça+operasyon kodu (emsal ve C/T bu koda göre gruplanır)", "Excel"),
            ("PARÇA NO_ÜRETİM_GİRİLEN / OPERASYON_NO", "Parça no / operasyon no (duruşlarda OPERASYON_NO sebep metnidir)", "Excel"),
            ("ÜRETİM_C/T", "Katalog hedef süre (saat); boşsa aynı Parça×Operasyon'un medyanıyla doldurulur", "Excel + ETL"),
            ("Sapma_Saat / Sapma_Yuzdesi", "Sure_Saat − C/T ve bunun C/T'ye oranı (yalnızca Durum='Üretim', C/T>0)", "ETL"),
            ("Durus_Sebebi_Ham / Durus_Kategorisi", "Duruş sebep metni ve anahtar kelimelerle atanan kategori", "ETL (config.CATEGORY_RULES)"),
        ], columns=["Kolon", "Anlamı", "Kaynağı"]), hide_index=True, use_container_width=True)
        if _cfg is not None and hasattr(_cfg, "DURUM_MAP"):
            st.caption(f"Durum harf kodları (config.DURUM_MAP): {dict(_cfg.DURUM_MAP)}")

    # --- canlı örnek verileri ---
    tz = compute_tezgah_oee(df)
    kn = compute_operator_karne(df, emsal_referans_df=df_ham)
    kt = ct_kanit_hesapla(df)
    pr = compute_pareto(df)

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

    st.markdown("### 📊 Ana OEE ve verimlilik formülleri")
    ex = tz.sort_values("Kullanilabilirlik").iloc[0] if not tz.empty else None
    if ex is not None:
        U, P, R = ex["Uretim_Saat"], ex["Plansiz_Durus_Saat"], ex["Rework_Saat"]
        can_kul = (f"**{ex['TEZGAH']}** tezgahı: Üretim = {U:.2f} sa, Plansız Duruş = {P:.2f} sa, Rework = {R:.2f} sa → "
                   f"({U:.2f} + {R:.2f}) / ({U:.2f} + {P:.2f} + {R:.2f}) × 100 = **%{(U + R) / (U + P + R) * 100:.1f}**" if (U + P + R) > 0 else None)
        can_kal = f"**{ex['TEZGAH']}**: {U:.2f} / ({U:.2f} + {R:.2f}) × 100 = **%{U / (U + R) * 100:.1f}**" if (U + R) > 0 else None
        uu = df[(df["Durum"] == "Üretim") & (df["TEZGAH"] == ex["TEZGAH"])]
        sct, sg = float(uu["ÜRETİM_C/T"].fillna(0).sum()), float(uu["Sure_Saat"].sum())
        can_perf = f"**{ex['TEZGAH']}**: Σ Hedef C/T = {sct:.2f} sa, Σ Gerçek süre = {sg:.2f} sa → {sct:.2f} / {sg:.2f} = **{sct / sg:.3f}** (%{sct / sg * 100:.1f})" if sg > 0 else None
        kul, kal, per = (U + R) / (U + P + R) if (U + P + R) > 0 else 0, U / (U + R) if (U + R) > 0 else 0, sct / sg if sg > 0 else 0
        can_oee = (f"**{ex['TEZGAH']}**: OEE Yaklaşık = {kul:.3f} × {kal:.3f} = **%{kul * kal * 100:.1f}** · Gerçek OEE = {kul:.3f} × {per:.3f} × {kal:.3f} = **%{kul * per * kal * 100:.1f}**"
                   + (" ⚠️ %100'ü aştı: katalog C/T gevşek" if kul * per * kal > 1 else ""))
    else:
        can_kul = can_kal = can_perf = can_oee = None

    bolum("1️⃣ Kullanılabilirlik (Availability)",
          "Makinenin, planlanan çalışma süresinin ne kadarını üreterek geçirdiği. **Set-up ve yemek molası planlı (kaçınılmaz) süre** sayıldığı için paydaya girmez.",
          r"\text{Kullanilabilirlik} = \frac{\text{Uretim\_Saat} + \text{Rework\_Saat}}{\text{Uretim\_Saat} + \text{Plansiz\_Durus\_Saat} + \text{Rework\_Saat}} \times 100",
          'planlanan = Uretim_Saat + Plansiz_Durus_Saat + Rework_Saat\nKullanilabilirlik = (Uretim_Saat + Rework_Saat) / planlanan   # planlanan > 0 ise\n'
          '# Uretim_Saat = df.loc[df["Durum"] == "Üretim", "Sure_Saat"].sum()  (tezgah bazında groupby("TEZGAH"))',
          ["Durum", "Sure_Saat", "TEZGAH"], can_kul)
    bolum("2️⃣ Kalite Oranı",
          "Üretilen işin ne kadarının tekrar işleme (rework) gerektirmediği. **Fire adedi verisi olmadığı için** Rework süresi kalite kaybının vekil (proxy) göstergesidir.",
          r"\text{Kalite\_Orani} = \frac{\text{Uretim\_Saat}}{\text{Uretim\_Saat} + \text{Rework\_Saat}} \times 100",
          'Kalite_Orani = Uretim_Saat / (Uretim_Saat + Rework_Saat)   # payda > 0 ise', ["Durum", "Sure_Saat"], can_kal)
    bolum("3️⃣ Performans Oranı ⚠️",
          "Klasik OEE'deki 'üretilen/hedeflenen adet'in **süre bazındaki** karşılığı. 1'den büyükse işler katalog hedefinden hızlı yapılmış demektir. "
          f"**Uyarı:** Katalog C/T gevşekse bu oran şişer ve Gerçek OEE %100'ü aşabilir (bkz. '{T_CT}'). Mutlak değere değil tezgahlar arası kıyasa güvenin.",
          r"\text{Performans\_Orani} = \frac{\sum \text{ÜRETİM\_C/T}}{\sum \text{Sure\_Saat}} \quad (\text{yalnızca Durum}=\text{Üretim})",
          'u = df[df["Durum"] == "Üretim"]\nTeorik = u["ÜRETİM_C/T"].fillna(0).sum()\nGercek = u["Sure_Saat"].sum()\nPerformans_Orani = Teorik / Gercek',
          ["ÜRETİM_C/T", "Sure_Saat", "Durum"], can_perf)
    bolum("4️⃣ OEE Yaklaşık ve Gerçek OEE",
          "**OEE Yaklaşık** hız bileşenini içermez (gerçek OEE'nin üst sınırı, güvenilir). **Gerçek OEE** hızı da katar ama katalog C/T'ye bağlıdır.",
          r"\text{OEE\_Yaklasik} = K \times Q \qquad \text{Gercek\_OEE} = K \times P \times Q",
          'OEE_Yaklasik = Kullanilabilirlik * Kalite_Orani\nGercek_OEE = Kullanilabilirlik * Performans_Orani * Kalite_Orani',
          ["Kullanılabilirlik (K)", "Performans (P)", "Kalite (Q)"], can_oee)

    st.markdown("### ⏱️ Duruş analizi")
    can_par = None
    if not pr.empty and len(pr) >= 2:
        can_par = (f"1. kategori **{pr.iloc[0]['Durus_Kategorisi']}** = {pr.iloc[0]['Toplam_Saat']:.2f} sa; 2. kategori **{pr.iloc[1]['Durus_Kategorisi']}** = {pr.iloc[1]['Toplam_Saat']:.2f} sa; "
                   f"toplam = {pr['Toplam_Saat'].sum():.2f} sa → ilk ikinin kümülatif payı = ({pr.iloc[0]['Toplam_Saat']:.2f} + {pr.iloc[1]['Toplam_Saat']:.2f}) / {pr['Toplam_Saat'].sum():.2f} × 100 = **%{pr.iloc[1]['Kumulatif_%']:.1f}**")
    bolum("5️⃣ Duruş Pareto ve Kümülatif %",
          "Plansız duruşlar kategoriye göre toplanır, büyükten küçüğe sıralanır; kümülatif pay hangi az sayıda sebebin kaybın çoğunu yarattığını gösterir. "
          "Kategori, duruş sebep metninden `config.CATEGORY_RULES` anahtar kelimeleriyle ETL'de atanır.",
          r"\text{Kumulatif\_\%}_k = \frac{\sum_{i \le k} \text{Toplam\_Saat}_i}{\sum \text{Toplam\_Saat}} \times 100",
          'd = df[df["Durum"] == "Plansız_Duruş"].dropna(subset=["Durus_Kategorisi"])\nozet = d.groupby("Durus_Kategorisi")["Sure_Saat"].agg(Toplam_Saat="sum", Adet="count")\n'
          'ozet = ozet.sort_values("Toplam_Saat", ascending=False)\nozet["Kumulatif_%"] = ozet["Toplam_Saat"].cumsum() / ozet["Toplam_Saat"].sum() * 100',
          ["Durum", "Durus_Kategorisi", "Sure_Saat"], can_par)

    st.markdown("### 👤 Operatör karnesi ve C/T formülleri")
    ornek_kayit = None
    if not kt.get("tablo", pd.DataFrame()).empty:
        kod0 = kt["tablo"].iloc[0]["KOD & OPE."]
        kk = df[(df["Durum"] == "Üretim") & (df["KOD & OPE."] == kod0) & (df["ÜRETİM_C/T"] > 0)].dropna(subset=["Sure_Saat"])
        if not kk.empty:
            med0 = kk["Sure_Saat"].median()
            ornek_kayit = kk.iloc[(kk["Sure_Saat"] - med0).abs().argsort().iloc[0]]
    can_ct = None
    if ornek_kayit is not None:
        s_, c_ = float(ornek_kayit["Sure_Saat"]), float(ornek_kayit["ÜRETİM_C/T"])
        can_ct = (f"**{ornek_kayit['KOD & OPE.']}**, {ornek_kayit['Operator_Kod']}: gerçek süre = {s_:.2f} sa, katalog C/T = {c_:.2f} sa → "
                  f"({s_:.2f} − {c_:.2f}) / {c_:.2f} × 100 = **{(s_ - c_) / c_ * 100:+.1f}%** (negatif = katalogdan hızlı)")
    bolum("6️⃣ CT Sapması (%)",
          "Bir işlemin katalog hedefine göre yüzde kaç saptığı. Eksi (−) hedeften hızlı, artı (+) yavaş demektir. Operatör ortalaması, o operatörün tüm üretim satırlarının **ortalamasıdır**.",
          r"\text{Sapma\_Yuzdesi} = \frac{\text{Sure\_Saat} - \text{ÜRETİM\_C/T}}{\text{ÜRETİM\_C/T}} \times 100 \qquad \text{(satır bazlı; sonra operatör ortalaması)}",
          'uretim_mask = (df["Durum"] == "Üretim") & (df["ÜRETİM_C/T"] > 0)\ndf["Sapma_Saat"] = np.where(uretim_mask, df["Sure_Saat"] - df["ÜRETİM_C/T"], np.nan)\n'
          'df["Sapma_Yuzdesi"] = np.where(uretim_mask, df["Sapma_Saat"] / df["ÜRETİM_C/T"] * 100, np.nan)\n# operatör: df[df.Sapma_Yuzdesi.notna()].groupby("Operator_Kod")["Sapma_Yuzdesi"].mean()',
          ["Sure_Saat", "ÜRETİM_C/T", "Durum", "Operator_Kod"], can_ct)

    can_em = None
    if not kn.empty:
        op0 = kn.iloc[0]["Operator_Kod"]
        kir0, gen0 = operator_emsal_kirilimi(df, df_ham, op0)
        if not kir0.empty:
            r0 = kir0.iloc[0]
            can_em = (f"**{op0}**, kod **{r0['KOD & OPE.']}**: ort. süre {r0['Ort_Sure']:.2f} sa, emsal medyanı {r0['Emsal_Medyan']:.2f} sa (n={int(r0['Emsal_Kayit'])}) → "
                      f"({r0['Ort_Sure']:.2f} − {r0['Emsal_Medyan']:.2f}) / {r0['Emsal_Medyan']:.2f} × 100 ≈ **{r0['Ort_Sapma']:+.1f}%** (yaklaşık; satır bazlı ortalama). Operatörün tüm satırlarının ortalaması: **{gen0:+.1f}%**")
    bolum("7️⃣ Emsal Sapması (%)",
          f"Katalog güvenilmezse, operatörü **aynı işi yapan diğer ustalara** kıyaslar. Emsal medyanı, o kodu işleyen **tüm** operatörlerin medyan süresidir (yalnızca en az {EMSAL_MIN_TEKRAR} kayıtlı kodlar). "
          "Referans TÜM veriden alınır; böylece dar tarih filtresinde kararsızlaşmaz.",
          r"\text{Emsal\_Sapma} = \frac{\text{Sure\_Saat} - \text{Emsal\_Medyan\_Saat}}{\text{Emsal\_Medyan\_Saat}} \times 100 \qquad \text{Emsal\_Medyan\_Saat} = \operatorname{Medyan}(\text{Sure\_Saat} \mid \text{KOD \& OPE.})",
          'u_ref = df_ham[df_ham["Durum"] == "Üretim"].dropna(subset=["KOD & OPE.", "Sure_Saat"])\nguvenilir = u_ref.groupby("KOD & OPE.").size().loc[lambda s: s >= EMSAL_MIN_TEKRAR].index\n'
          'emsal = u_ref[u_ref["KOD & OPE."].isin(guvenilir)].groupby("KOD & OPE.")["Sure_Saat"].median()\nu["Emsal_Sapma"] = (u["Sure_Saat"] - u["KOD & OPE."].map(emsal)) / u["KOD & OPE."].map(emsal) * 100',
          ["Sure_Saat", "KOD & OPE.", "Durum", "Operator_Kod"], can_em)

    dg_sayi = dg_pay = None
    uu2 = df[(df["Durum"] == "Üretim") & (df["ÜRETİM_C/T"] > 0)].dropna(subset=["Sure_Saat"])
    if not uu2.empty:
        dg = uu2["Sure_Saat"] < CT_DUSUK_GUVEN_ORANI * uu2["ÜRETİM_C/T"]
        dg_sayi, dg_pay = int(dg.sum()), float(dg.mean() * 100)
    bolum("8️⃣ Düşük Güven (Satır / %)",
          f"Bir işlem süresi, katalog C/T'nin **%{CT_DUSUK_GUVEN_ORANI * 100:.0f}'inden bile kısa** ise muhtemelen hatalı/eksik zaman girişidir. Bu şüpheli kayıtların oranı operatör karnesinde gösterilir. "
          "(Eşik `config.CT_DUSUK_GUVEN_ORANI`.)",
          r"\text{Dusuk\_Guven\_\%} = \frac{\#\{\text{Sure\_Saat} < \alpha \times \text{ÜRETİM\_C/T}\}}{\text{Islem\_Sayisi}} \times 100 \qquad \alpha = " + f"{CT_DUSUK_GUVEN_ORANI}",
          f'CT_DUSUK_GUVEN_ORANI = {CT_DUSUK_GUVEN_ORANI}\nu["dusuk_guven"] = u["Sure_Saat"] < CT_DUSUK_GUVEN_ORANI * u["ÜRETİM_C/T"].fillna(0)\n'
          'yuzde = u.groupby("Operator_Kod")["dusuk_guven"].sum() / u.groupby("Operator_Kod").size() * 100',
          ["Sure_Saat", "ÜRETİM_C/T", "Operator_Kod"],
          f"Şu an seçili veride {dg_sayi:,} / {len(uu2):,} üretim kaydı (**%{dg_pay:.1f}**) bu eşiğin altında." if dg_sayi is not None else None)

    esik_txt = f"{kn.attrs.get('efektif_esik_saat'):g}" if not kn.empty and kn.attrs.get("efektif_esik_saat") is not None else "—"
    bolum("9️⃣ Operatör karnesine giriş eşiği",
          f"Az veriyle istatistik güvenilmez olduğundan, toplam üretim saati eşiğin altındaki operatörler karneye girmez. Eşik seçili aralığa göre ölçeklenir: en fazla **{KARNE_MIN_SAAT} sa**, kısa aralıklarda gün başına **{KARNE_MIN_SAAT_GUNLUK} sa × gün sayısı**.",
          r"\text{Esik} = \min(\text{KARNE\_MIN\_SAAT},\ \text{KARNE\_MIN\_SAAT\_GUNLUK} \times \text{gun\_sayisi})",
          f'KARNE_MIN_SAAT = {KARNE_MIN_SAAT}; KARNE_MIN_SAAT_GUNLUK = {KARNE_MIN_SAAT_GUNLUK}\nefektif_esik = min(KARNE_MIN_SAAT, KARNE_MIN_SAAT_GUNLUK * gun_sayisi)',
          ["Sure_Saat", "TARİH", "Operator_Kod"], f"Şu an efektif eşik: **{esik_txt} sa**.")

    st.markdown("### 🎯 Önerilen C/T")
    can_med = None
    if not kt.get("tablo", pd.DataFrame()).empty:
        r0 = kt["tablo"].iloc[0]
        vals = df[(df["Durum"] == "Üretim") & (df["KOD & OPE."] == r0["KOD & OPE."])]["Sure_Saat"].dropna()
        can_med = (f"**{r0['KOD & OPE.']}**: {medyan_aciklamasi(vals)}. Katalog {r0['Katalog_CT_Saat']:.2f} sa → "
                   f"Fark = ({r0['Onerilen_CT_Saat']:.2f} − {r0['Katalog_CT_Saat']:.2f}) / {r0['Katalog_CT_Saat']:.2f} × 100 = **{r0['Fark_Yuzde']:+.1f}%**")
    bolum("🔟 Önerilen C/T ve güncelleme kararı",
          f"Katalog yerine, parçayı işleyen **tüm operatörlerin** gerçekleşen süre **medyanı** önerilir. **Neden medyan?** Ortalama, tek bir arıza/kısmi kaydtan bozulur; medyan ise sıralı değerlerin ortasıdır. "
          f"|Fark| ≥ %{GUNCELLEME_ESIGI_YUZDE} ve kayıt sayısı ≥ {EMSAL_MIN_TEKRAR} ise katalog 'güncellenmeli' işaretlenir.",
          r"\text{Onerilen\_CT} = \operatorname{Medyan}(\text{Sure\_Saat}) \qquad \text{Fark\%} = \frac{\text{Onerilen\_CT} - \text{Katalog\_CT}}{\text{Katalog\_CT}} \times 100",
          'u = df[df["Durum"] == "Üretim"].dropna(subset=["KOD & OPE.", "Sure_Saat"])\ng = u.groupby("KOD & OPE.").agg(Onerilen_CT_Saat=("Sure_Saat", "median"), Islem_Sayisi=("Sure_Saat", "size"), Katalog_CT_Saat=("ÜRETİM_C/T", "median"))\n'
          f'g = g[g["Islem_Sayisi"] >= EMSAL_MIN_TEKRAR]\ng["Fark_Yuzde"] = (g["Onerilen_CT_Saat"] - g["Katalog_CT_Saat"]) / g["Katalog_CT_Saat"] * 100\ng["Guncelleme_Onerisi"] = g["Fark_Yuzde"].abs() >= {GUNCELLEME_ESIGI_YUZDE}',
          ["KOD & OPE.", "Sure_Saat", "ÜRETİM_C/T", "Durum"], can_med)

    st.markdown("### 🧮 Genel notlar")
    st.markdown(
        "- **Yuvarlama:** Süreler dakika kesirlerinden (10 dk = 1/6 sa) oluştuğu için toplamlar `29.9777…` gibi çıkabilir; ekranda saatler 2, yüzdeler 1 basamağa yuvarlanır. Yuvarlama yalnızca gösterimdedir, hesaplar ham değerlerle yapılır.\n"
        "- **Tarih:** `TARİH` vardiyanın başladığı gündür; gece vardiyasının gece yarısından sonraki kayıtları da aynı `TARİH`'e yazılır. Gerçek zaman damgası `BAŞLANGIÇ_SAAT/BİTİŞ_SAAT` + `VARDİYA`'dan çözülür.\n"
        "- **Süre alanları:** `Sure_Saat` ile (BİTİŞ − BAŞLANGIÇ) farkı bazı satırlarda mola/kayıt dışı süre yüzünden uyuşmaz; oran 'Veri Kapsamı ve Kalite' kutusunda gösterilir."
    )
    with st.expander("Tüm metrik sözlüğü (METRIC_REGISTRY — gizli modüller dahil)"):
        for anahtar in METRIC_REGISTRY:
            st.markdown(metrik_aciklamasi(anahtar))
            st.markdown("---")

# ====================================================================================
# AŞAĞIDAKİ KODLAR GİZLENMİŞ MODÜLLERDİR (SİLİNMEMİŞTİR) — FAZ-2
# NOT: Streamlit, dosyada tek başına duran '''...''' metinlerini ekrana YAZDIRIR ("magic"),
# bu yüzden gizleme '#' ile yapılmıştır. Yeniden açmak için:
#   1) SEKMELER listesinde ilgili sekme adını yorumdan çıkarın,
#   2) aşağıdaki ilgili bloğu seçip Ctrl+/ ile yorumdan çıkarın.
# (Sekmeler artık sıraya değil ada göre seçildiği için indeks çakışması olmaz.)
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