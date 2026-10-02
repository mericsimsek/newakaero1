# -*- coding: utf-8 -*-
"""
sn_izleme.py — Seri numarası (S/N) bazlı parça izleme, adet ve hafta özeti.

KURAL (kullanıcı): parça adedi S/N'ye göre sayılır. Aynı S/N'nin bütün kayıtları
(vardiya/operatör değişse de, biri diğerini devam ettirse de) birleşir ve 1 ADET olur.
Pilot: 2547M01G03 (kısa kod M01G03), operasyon 60.

HAFTA: Perşembe başlar, Çarşamba biter. Hafta no = (tarih − 3 gün)'ün ISO hafta numarası
(örn. Per 6 Ağu 2026 → hafta 32). Bir S/N, TAMAMLANDIĞI (son kayıt) haftaya yazılır.

SET-UP: Veride set-up satırları "60-SETUP" gibi operasyon metniyle ve çoğunlukla
S/N'siz girilir. Bu yüzden set-up, S/N'ye değil PARÇA+OPERASYON'a bağlanır; adet yanında
ayrı gösterilir ("setuplarla birlikte kaç adet"). Sadece "SETUP" yazanlar operasyonu
belirsiz sayılır, ayrıca raporlanır.

Beklenen kolonlar (ETL): TARİH, TEZGAH, VARDİYA, Operator_Kod, Durum (Üretim/Set_Up/…),
Sure_Saat, "S/N", "PARÇA NO_ÜRETİM_GİRİLEN", "OPERASYON_NO", opsiyonel "ÜRETİM_C/T",
BAŞLANGIÇ_SAAT, BİTİŞ_SAAT. (Ham Excel'deki boşluklu isimler de kabul edilir.)
"""
from __future__ import annotations

import numpy as np
import pandas as pd

UZUN_KOD_ONEKI = {"M01G03": "2547"}   # kısa kod -> uzun koddaki ön ek
PILOT = [("M01G03", "60")]            # (kısa kod, operasyon) — yenisini buraya ekleyin
ALARM_TOLERANS = 0.20                 # katalog C/T'yi %20'den fazla aşarsa "yavaş"


def _kol(df, *adaylar):
    for a in adaylar:
        if a in df.columns:
            return a
    return None


def uzun_kod(kisa: str) -> str:
    return UZUN_KOD_ONEKI.get(kisa, "") + kisa


def hafta_no(tarih: pd.Series) -> pd.Series:
    """Perşembe-başlangıçlı hafta numarası."""
    return (tarih - pd.Timedelta(days=3)).dt.isocalendar().week.astype(int)


def hafta_baslangic(tarih: pd.Series) -> pd.Series:
    return (tarih - pd.to_timedelta((tarih.dt.weekday - 3) % 7, unit="D")).dt.normalize()


def sn_izleme(df: pd.DataFrame, parca_kisa: str = "M01G03", operasyon: str = "60", op_adi=lambda k: k):
    """Döner: (ozet, detay, setup, kapsam)
    ozet  : S/N başına 1 satır = 1 ADET (toplam saat, operatörler, vardiyalar, bölünmüş mü, alarm)
    detay : S/N'nin her kaydı (kim, vardiya, saat aralığı, süre)
    setup : bu parça+operasyonun set-up kayıtları (S/N'siz olabilir)
    kapsam: sayılar ve veri kalitesi uyarıları (kapsam['mesaj'] doluysa hesap yapılamadı)
    """
    c_parca = _kol(df, "PARÇA NO_ÜRETİM_GİRİLEN", "PARÇA NO ÜRETİM GİRİLEN")
    c_op = _kol(df, "OPERASYON_NO", "OPERASYON NO")
    c_kisi = _kol(df, "Operator_Kod", "OPERATÖR")
    c_ct = _kol(df, "ÜRETİM_C/T", "ÜRETİM C/T")
    bos = pd.DataFrame()
    if any(c is None for c in [c_parca, c_op, c_kisi, _kol(df, "Durum"), _kol(df, "Sure_Saat"), _kol(df, "S/N"), _kol(df, "TARİH")]):
        return bos, bos, bos, {"mesaj": "Gerekli kolonlar yok (S/N DB'de olmayabilir)."}

    parca_mask = df[c_parca].astype(str).str.contains(parca_kisa, na=False)
    op_s = df[c_op].astype(str).str.strip()
    kapsam = {"uzun_kod": uzun_kod(parca_kisa), "operasyon": str(operasyon)}

    # ---- SET-UP (parça+operasyon bazlı)
    s_tum = df[parca_mask & (df["Durum"] == "Set_Up")]
    s_op = s_tum[op_s.loc[s_tum.index].str.startswith(str(operasyon))].copy()
    s_bel = s_tum[op_s.loc[s_tum.index].str.upper().eq("SETUP")]
    kapsam.update(setup_kayit=int(len(s_op)), setup_saat=float(s_op["Sure_Saat"].sum()),
                  setup_belirsiz_kayit=int(len(s_bel)), setup_belirsiz_saat=float(s_bel["Sure_Saat"].sum()))
    s_op["Operator"] = s_op[c_kisi].map(op_adi)
    setup = s_op[[c for c in ["TARİH", "VARDİYA", "TEZGAH", "Operator", c_op, "Sure_Saat", "S/N"] if c in s_op.columns]] \
        .sort_values("TARİH").reset_index(drop=True)

    # ---- ÜRETİM (S/N bazlı)
    u = df[parca_mask & (df["Durum"] == "Üretim") & (op_s == str(operasyon))].copy()
    kapsam.update(uretim_kaydi=int(len(u)), sn_bos_kayit=int(u["S/N"].isna().sum()),
                  sn_bos_saat=float(u.loc[u["S/N"].isna(), "Sure_Saat"].sum()))
    u = u[u["S/N"].notna()]
    if u.empty:
        return bos, bos, setup, kapsam

    u["Operator"] = u[c_kisi].map(op_adi)
    kol = [c for c in ["S/N", "TARİH", "VARDİYA", "BAŞLANGIÇ_SAAT", "BİTİŞ_SAAT", "TEZGAH", "Operator", "Sure_Saat"] if c in u.columns]
    sirala = ["S/N", "TARİH"] + (["BAŞLANGIÇ_SAAT"] if "BAŞLANGIÇ_SAAT" in u.columns else [])
    detay = u[kol].sort_values(sirala).reset_index(drop=True)

    agg = dict(
        Toplam_Saat=("Sure_Saat", "sum"), Kayit=("Sure_Saat", "size"),
        Ilk_Tarih=("TARİH", "min"), Son_Tarih=("TARİH", "max"),
        Operatorler=("Operator", lambda s: " + ".join(dict.fromkeys(s))),
    )
    if "VARDİYA" in u.columns:
        agg["Vardiyalar"] = ("VARDİYA", lambda s: " + ".join(dict.fromkeys(s.dropna().astype(str))))
    if "TEZGAH" in u.columns:
        agg["Tezgah"] = ("TEZGAH", lambda s: " / ".join(dict.fromkeys(s)))
    if c_ct:
        agg["Katalog_CT"] = (c_ct, "max")
    ozet = u.groupby("S/N").agg(**agg).reset_index()
    ozet["Adet"] = 1
    ozet["Bolunmus_Is"] = ozet["Kayit"] > 1
    med = ozet["Toplam_Saat"].median()
    ozet["Medyana_Gore_%"] = (ozet["Toplam_Saat"] - med) / med * 100 if med > 0 else np.nan
    if "Katalog_CT" in ozet:
        ref = ozet["Katalog_CT"].where(ozet["Katalog_CT"] > 0)
        ozet["Alarm"] = np.where(ref.notna() & (ozet["Toplam_Saat"] > ref * (1 + ALARM_TOLERANS)), "🔴 Yavaş", "🟢 Normal")
    ozet["Hafta"] = hafta_no(ozet["Son_Tarih"])
    ozet = ozet.sort_values("Son_Tarih").reset_index(drop=True)
    kapsam.update(sn_sayisi=int(len(ozet)), medyan_saat=float(med), bolunmus_sn=int(ozet["Bolunmus_Is"].sum()))
    return ozet.round({"Toplam_Saat": 2, "Medyana_Gore_%": 1}), detay.round({"Sure_Saat": 2}), setup, kapsam


def sn_hafta_ozeti(ozet: pd.DataFrame, setup: pd.DataFrame) -> pd.DataFrame:
    """Hafta (Per→Çar) başına: adet, ortalama süre/adet, günlük ortalama adet, set-up saati."""
    if ozet.empty:
        return pd.DataFrame()
    o = ozet.copy()
    o["Hafta_Baslangic"] = hafta_baslangic(o["Son_Tarih"])
    h = o.groupby(["Hafta", "Hafta_Baslangic"]).agg(
        Adet=("Adet", "sum"), Ort_Sure_Sa=("Toplam_Saat", "mean"), Medyan_Sure_Sa=("Toplam_Saat", "median"),
        Toplam_Uretim_Sa=("Toplam_Saat", "sum"), Bolunmus=("Bolunmus_Is", "sum"),
        Uretim_Gunu=("Son_Tarih", "nunique"),
    ).reset_index()
    h["Gunluk_Ort_Adet"] = h["Adet"] / h["Uretim_Gunu"]
    h["Hafta_Bitis"] = h["Hafta_Baslangic"] + pd.Timedelta(days=6)
    if setup is not None and not setup.empty:
        s = setup.copy()
        s["Hafta"] = hafta_no(s["TARİH"])
        h = h.merge(s.groupby("Hafta")["Sure_Saat"].sum().rename("Setup_Sa").reset_index(), on="Hafta", how="left")
    h["Setup_Sa"] = h.get("Setup_Sa", 0).fillna(0)
    return h.round({"Ort_Sure_Sa": 2, "Medyan_Sure_Sa": 2, "Toplam_Uretim_Sa": 1, "Gunluk_Ort_Adet": 2, "Setup_Sa": 1})


def sn_ai_baglami(df: pd.DataFrame, pilot=PILOT, op_adi=lambda k: k) -> str:
    """4_AI_Asistan.py bağlamına eklenecek metin. Sayfadaki tablolarla AYNI fonksiyondan üretilir."""
    parcalar = []
    for kisa, op in pilot:
        ozet, detay, setup, k = sn_izleme(df, kisa, op, op_adi)
        if k.get("mesaj"):
            parcalar.append(f"### TABLO 10: S/N İZLEME — {k['mesaj']} Bu yüzden S/N sorularına cevap VERİLEMEZ; kullanıcıya bunu söyle.")
            continue
        if ozet.empty:
            continue
        hf = sn_hafta_ozeti(ozet, setup)
        parcalar.append(
            f"### TABLO 10: S/N BAZLI ADET — {k['uzun_kod']} operasyon {op}\n"
            f"Her satır 1 ADET (1 seri numarası). ADET KURALI: aynı S/N'nin tüm kayıtları birleşir, 1 sayılır. "
            f"Toplam_Saat = S/N'nin tüm kayıtlarının toplamı; Bolunmus_Is=True: birden fazla kayıt (genelde vardiya değişimi). "
            f"Hafta: Perşembe başlar Çarşamba biter; S/N tamamlandığı (Son_Tarih) haftaya yazılır. "
            f"Alarm: Katalog_CT'yi %{int(ALARM_TOLERANS*100)}'den fazla aşarsa Yavaş. "
            f"Özet: {k['sn_sayisi']} adet, medyan {k['medyan_saat']:.2f} sa, {k['bolunmus_sn']} bölünmüş iş.\n"
            + ozet.to_csv(index=False)
            + f"\n### TABLO 11: S/N KAYIT DETAYI — {k['uzun_kod']} op {op} (her S/N için kim, hangi vardiya, saat aralığı, süre)\n"
            + detay.to_csv(index=False)
            + f"\n### TABLO 12: HAFTALIK ADET ÖZETİ — {k['uzun_kod']} op {op} (Perşembe→Çarşamba; Gunluk_Ort_Adet = Adet / üretim yapılan gün)\n"
            + hf.to_csv(index=False)
            + f"\n### TABLO 13: SET-UP KAYITLARI — {k['uzun_kod']} op {op} (set-up S/N'ye değil parça+operasyona bağlıdır; toplam {k['setup_saat']:.1f} sa, {k['setup_kayit']} kayıt)\n"
            + (setup.to_csv(index=False) if not setup.empty else "(kayıt yok)\n")
            + f"\n(Operasyonu belirsiz 'SETUP' kayıtları: {k['setup_belirsiz_kayit']} adet, {k['setup_belirsiz_saat']:.1f} sa — op {op}'a eklenmedi. "
              f"S/N'si boş üretim kaydı: {k['sn_bos_kayit']}, {k['sn_bos_saat']:.1f} sa — adede dahil değil.)"
        )
    return "\n\n".join(parcalar)


SN_AI_KURALI = (
    "6. Kullanıcı seri numarası (S/N), 'kaç adet/parça yapıldı', 'bu parça kaç saatte işlendi', 'kim çalıştı', "
    "'hangi vardiya', 'week/hafta N ne yaptı', 'günlük ortalama kaç adet', 'set-up' diye sorarsa TABLO 10-13'ü kullan. "
    "Adet = S/N sayısı (aynı S/N'nin kayıtları birleşir, 1 adet). Hafta Perşembe'den başlar. "
    "Cevaba S/N, adet, saat, operatör ve vardiyayı yaz; kullanıcı 'Parça S/N İzleme' sekmesinde doğrulayacak. "
    "Tablolar yalnızca pilot parça/operasyonu kapsar; başka parça sorulursa 'henüz S/N izleme yok' de."
)


# --------------------------------------------------------------------------- STREAMLIT
def render_sn_sekmesi(df: pd.DataFrame, pilot=PILOT, op_adi=lambda k: k):
    """app4.py: `with sekme[T_SN]: render_sn_sekmesi(df)`"""
    import streamlit as st

    if "S/N" not in df.columns:
        st.error("Veride **S/N** kolonu yok. config.CANONICAL_COLUMNS + ETL'e S/N eklenmeden bu sekme ve AI S/N sorularına cevap veremez.")
        return
    secenek = [f"{uzun_kod(k)} — op {o}" for k, o in pilot]
    i = st.selectbox("Parça / operasyon", range(len(pilot)), format_func=lambda x: secenek[x], key="sn_parca")
    kisa, op = pilot[i]
    ozet, detay, setup, k = sn_izleme(df, kisa, op, op_adi)
    if ozet.empty:
        st.info("Seçili filtrelerde bu parça/operasyon için S/N'li üretim kaydı yok.")
        return
    hf = sn_hafta_ozeti(ozet, setup)
    c1, c2, c3, c4, c5 = st.columns(5)
    c1.metric("Adet (S/N)", k["sn_sayisi"])
    c2.metric("Medyan süre/adet (sa)", f"{k['medyan_saat']:.2f}")
    c3.metric("Bölünmüş iş", k["bolunmus_sn"])
    c4.metric("Set-up (sa)", f"{k['setup_saat']:.1f}")
    c5.metric("Yavaş S/N", int((ozet.get("Alarm", pd.Series(dtype=str)) == "🔴 Yavaş").sum()))
    if k["sn_bos_kayit"]:
        st.warning(f"{k['sn_bos_kayit']} üretim kaydında S/N boş ({k['sn_bos_saat']:.1f} sa) — adede dahil değil.")
    if k["setup_belirsiz_kayit"]:
        st.caption(f"Operasyonu belirsiz 'SETUP' kaydı: {k['setup_belirsiz_kayit']} ({k['setup_belirsiz_saat']:.1f} sa) — op {op}'a eklenmedi.")

    st.markdown("#### 📅 Hafta (Perşembe→Çarşamba) bazlı adet")
    st.dataframe(hf, use_container_width=True, hide_index=True)
    st.markdown("#### 📦 S/N bazlı adet tablosu (her satır 1 adet)")
    st.dataframe(ozet, use_container_width=True, hide_index=True)
    st.markdown("#### 🔎 Tek S/N'yi doğrula")
    sn = st.selectbox("Seri numarası", ozet["S/N"].tolist(), key="sn_sec")
    st.dataframe(detay[detay["S/N"] == sn], use_container_width=True, hide_index=True)
    st.caption("Bunlar ham kayıtlardır; AI bir S/N için süre/operatör söylediğinde bu tabloyla birebir tutmalıdır.")
    if not setup.empty:
        st.markdown("#### 🛠️ Bu parça+operasyonun set-up kayıtları")
        st.dataframe(setup, use_container_width=True, hide_index=True)
    with st.expander("🤖 AI asistana giden S/N tabloları (birebir aynı veri)"):
        st.code(sn_ai_baglami(df, pilot, op_adi), language="csv")