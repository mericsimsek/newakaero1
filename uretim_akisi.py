# -*- coding: utf-8 -*-
from __future__ import annotations
import re
import numpy as np
import pandas as pd
import streamlit as st

OPERATOR_SOZLUGU = {
    "Op_001": "AŞKIN SAKALLI", "Op_002": "BERAT TUNÇ", "Op_003": "CİHAN SARIGÜL", "Op_004": "EMRE TOPRAK",
    "Op_005": "EREN TAMER YELEKÇİ", "Op_006": "ERTUĞRUL ÖZDEMİR", "Op_007": "HAMZA KARAASLAN", "Op_008": "HÜSEYİN YENİCE",
    "Op_009": "HÜSEYİN YILMAZ", "Op_010": "İLKER SEZDİ", "Op_011": "MEHMET ALİ ÇOLAK", "Op_012": "MEHMET EMRE KARAKAŞ",
    "Op_013": "MERT KUŞCU", "Op_014": "MESUT ARDIÇ", "Op_015": "MUHAMMED KORKMAZ", "Op_016": "NAZİF GÜÇLÜDAĞ",
    "Op_017": "NURULLAH ZENGİN", "Op_018": "ÖZCAN ÇELİK", "Op_019": "TANER GÜÇLÜDAĞ", "Op_020": "UMUT ÖZKAN", "Op_021": "YAŞAR ŞENOL","Op_022": "ALİ ÖZTEKİN",
}
CALISMA = ["Üretim", "Set_Up", "Rework"]
_TR = str.maketrans("İıŞşĞğÜüÖöÇç", "IiSsGgUuOoCc")
_BITTI_ADAYLARI = ["finish", "durum2", "islem durumu"]

def op_ad(kod): return OPERATOR_SOZLUGU.get(kod, kod)

def _txt(v):
    if v is None or (not isinstance(v, str) and pd.isna(v)): return None
    s = str(v).strip()
    return None if s.lower() in ("", "nan", "none", "nat", "-") else s

def _opno(v):
    s = _txt(v)
    return s[:-2] if s and s.endswith(".0") else s

def _dk(v):
    if v is None: return np.nan
    if isinstance(v, (int, float, np.integer, np.floating)):
        return np.nan if pd.isna(v) else round((float(v) % 1) * 1440)
    if hasattr(v, "hour") and hasattr(v, "minute"):
        return v.hour * 60 + v.minute
    m = re.search(r"(\d{1,2})[:.](\d{2})", str(v))
    return int(m[1]) * 60 + int(m[2]) if m else np.nan

def _vardiya(v):
    m = re.findall(r"(\d{1,2})[:.](\d{2})", str(v or ""))
    if len(m) < 2: return np.nan, np.nan
    return int(m[0][0]) * 60 + int(m[0][1]), int(m[1][0]) * 60 + int(m[1][1])

def _hm(dk):
    return "-" if pd.isna(dk) else f"{int(dk) % 1440 // 60:02d}:{int(dk) % 60:02d}"

def _bas_zaman(tarih, vard, bas_dk):
    if pd.isna(tarih) or pd.isna(bas_dk):
        return pd.NaT
    return pd.Timestamp(tarih).normalize() + pd.Timedelta(minutes=float(bas_dk))
            


def _bitti_maske(s: pd.Series) -> pd.Series:
    return (s.astype(str).str.replace("\u0307", "", regex=False).str.translate(_TR)
            .str.upper().str.contains("BITTI", regex=False, na=False))

def _bitti_kolonu(x: pd.DataFrame):
    adaylar = [c for c in x.columns if str(c).strip() == "parça"]
    adaylar += [c for c in x.columns if c not in adaylar and str(c).strip().translate(_TR).lower() in _BITTI_ADAYLARI]
    for c in adaylar:
        if _bitti_maske(x[c]).any(): return c
    en, en_n = None, 0
    for c in x.columns:
        if pd.api.types.is_numeric_dtype(x[c]) or pd.api.types.is_datetime64_any_dtype(x[c]): continue
        n = int(_bitti_maske(x[c]).sum())
        if n > en_n: en, en_n = c, n
    return en or (adaylar[0] if adaylar else None)

def _op_sirala(v):
    return (not v.isdigit(), int(v) if v.isdigit() else 0, v)
FINAL_OP_HARITASI = {
    "1865M14P04": "20",
    "2243M35P03": "40",
    "2302M91P03": "110",
    "2383M85P03": "20",
    "2445M92P01": "20",
    "2466M52G04": "60",
    "2547M01G03": "60",
    "2547M40P02": "71",
    "2564M13P02": "71",
    "336-001-909-0": "20",
    "4013760-004P01": "20",
    "T160005": "20",
    "T160007": "20",
    "T160030": "20",
    "T160032": "20",
    "T170045": "20",
    "T170046": "20",
}

def _nihai_biten_maske(s: pd.DataFrame) -> pd.Series:
    """Son (FİNİSH) operasyonu BİTTİ olan satırlar. Uzun Ref haritada yoksa eski mantık (her BİTTİ)."""
    final_op = s["Uzun Ref"].astype(str).str.strip().map(FINAL_OP_HARITASI)
    op = s["Op"].fillna("").astype(str).str.strip()
    return (s["_adet"] == 1) & (final_op.isna() | (op == final_op))


def sn_sayilari(d: pd.DataFrame) -> tuple[int, int]:
    """(işlem gören, biten NİHAİ MAMUL) farklı fiziksel parça (Parça + S/N)."""
    s = d[(d["Durum"] == "Üretim") & d["S/N"].notna()]
    if s.empty:
        return 0, 0
    anahtar = s["Parça"].fillna("-") + "|" + s["S/N"]
    return int(anahtar.nunique()), int(anahtar[_nihai_biten_maske(s)].nunique())


def biten_parcalar_tablosu(d: pd.DataFrame) -> pd.DataFrame:
    """Biten Parça kartındaki parçaların listesi (aynı kural). Tekrar işlenen parça bir kez görünür."""
    kol = ["Tarih", "Parça", "Uzun Ref", "S/N", "Op", "Tezgah", "Operatör", "Saat"]
    s = d[(d["Durum"] == "Üretim") & d["S/N"].notna()]
    b = s[_nihai_biten_maske(s)]
    if b.empty:
        return pd.DataFrame(columns=kol)
    sira = "_sira" if "_sira" in b.columns else "_bas"
    b = b.sort_values(sira).drop_duplicates(["Parça", "S/N"], keep="last")
    b = b.sort_values(["Parça", sira])
    out = b[kol].copy()
    out["Tarih"] = pd.to_datetime(out["Tarih"]).dt.date
    return out.reset_index(drop=True)
    
def nihai_biten_metni(d: pd.DataFrame, max_sn: int = 150) -> str:
    """AI için: nihai mamul olarak biten parçalar, parça bazlı S/N listesiyle."""
    bt = biten_parcalar_tablosu(d)
    if bt.empty:
        return "NİHAİ BİTEN PARÇA (rotanın SON operasyonu BİTTİ): 0"
    satirlar = [f"NİHAİ BİTEN PARÇA (rotanın SON operasyonu BİTTİ; ara operasyonlar sayılmaz): toplam {len(bt)}"]
    for (p, o), g in bt.groupby(["Parça", "Op"]):
        liste = ", ".join(g["S/N"]) if len(bt) <= max_sn else "(liste çok uzun)"
        satirlar.append(f"  {p} (son Op {o}): {len(g)} adet — {liste}")
    return "\n".join(satirlar)


def biten_operasyon(d: pd.DataFrame) -> int:
    """Biten operasyon çıktısı: her (Parça, Op, S/N) için 1. Op 10 ve Op 20 ayrı sayılır."""
    return int(d["_adet"].sum())

def gunluk_ai_baglami_olustur(df_tablo: pd.DataFrame, secili_tarih) -> str:
    """Ekrandaki filtrelenmiş tabloyu AI için kısa bir metne çevirir. Sadece str döndürür."""
    if isinstance(secili_tarih, (tuple, list)) and len(secili_tarih) > 0:
        a, b = secili_tarih[0], secili_tarih[-1]
        tarih_txt = a.strftime("%d.%m.%Y") if a == b else f"{a.strftime('%d.%m.%Y')} – {b.strftime('%d.%m.%Y')}"
    else:
        tarih_txt = secili_tarih.strftime("%d.%m.%Y") if hasattr(secili_tarih, "strftime") else str(secili_tarih)

    if df_tablo is None or df_tablo.empty:
        return f"Tarih: {tarih_txt} | Bu filtrelerle kayıt yok."

    uretim = df_tablo.loc[df_tablo["Durum"] == "Üretim", "Süre"].sum()
    durus_m = df_tablo["Durum"].astype(str).str.contains("Duru", case=False, na=False)
    durus = df_tablo.loc[durus_m, "Süre"].sum()
    setup_rw = df_tablo.loc[df_tablo["Durum"].isin(["Set_Up", "Rework"]), "Süre"].sum()
    sn_islem, sn_biten = sn_sayilari(df_tablo)

    satirlar = [
        f"Tarih: {tarih_txt} | Listelenen satır: {len(df_tablo)}",
        f"Üretim: {uretim:.1f} sa | Duruş: {durus:.1f} sa | Set-up+Rework: {setup_rw:.1f} sa",
        f"İşlem gören farklı parça (S/N): {sn_islem} | Biten farklı parça (S/N): {sn_biten} | "
        f"Biten operasyon çıktısı: {int(df_tablo['_adet'].sum())}",
    ]

    d = df_tablo[durus_m]
    if not d.empty:
        sebep = (d[d["Sebep"].astype(str).str.strip() != ""]
                 .groupby("Sebep")["Süre"].sum().sort_values(ascending=False).head(3))
        if not sebep.empty:
            satirlar.append("En çok duruş sebebi: " + "; ".join(f"{k} ({v:.1f}sa)" for k, v in sebep.items()))
        tez = d.groupby("Tezgah")["Süre"].sum().sort_values(ascending=False).head(3)
        if not tez.empty:
            satirlar.append("En çok duran tezgah: " + "; ".join(f"{k} ({v:.1f}sa)" for k, v in tez.items()))

    # TEZGAH BAZLI GÜNCEL ÖZETİ HESAPLA VE AI'A GÖNDER
    p = df_tablo.pivot_table(index="Tezgah", columns="Durum", values="Süre", aggfunc="sum", fill_value=0)
    tz = pd.DataFrame({
        "Üretim_sa": p.get("Üretim", 0),
        "Plansız_Duruş_sa": p.get("Plansız_Duruş", 0),
        "SetUp_sa": p.get("Set_Up", 0),
        "Rework_sa": p.get("Rework", 0),
        "Net_Adet": df_tablo.groupby("Tezgah")["_adet"].sum(),
    }).fillna(0).round(2)
    
    satirlar.append("TEZGAH BAZLI ÖZET (bu tarih aralığı):\n" + tz.reset_index().to_csv(index=False))
    return "\n".join(satirlar)

_SESLI = "aıoueiöü"
_INCE = "eiöü"

def _tr_kucuk(s: str) -> str:
    return s.replace("İ", "i").replace("I", "ı").lower()

def _ad_ek(ad: str, hal: str) -> str:
    """İsme yönelme (-e/-a) veya ayrılma (-den/-dan) eki ekler: YAŞAR'a, EMRE'ye, HÜSEYİN'den, MERT'ten."""
    k = _tr_kucuk(ad)
    son_sesli = next((c for c in reversed(k) if c in _SESLI), "a")
    ince = son_sesli in _INCE
    if hal == "e":
        ek = ("y" if k[-1] in _SESLI else "") + ("e" if ince else "a")
    else:
        ek = ("t" if k[-1] in "çfhkpsşt" else "d") + ("en" if ince else "an")
    return f"{ad}'{ek}"

def _devir_metni(ben, onceki, sonraki) -> str:
    al = isinstance(onceki, str) and onceki != ben     # ardışık aynı kişi = devir değil
    ver = isinstance(sonraki, str) and sonraki != ben
    if al and ver:
        return f"← {_ad_ek(onceki, 'den')} aldı ➔ {_ad_ek(sonraki, 'e')} devretti"
    if ver:
        return f"➔ {_ad_ek(sonraki, 'e')} devretti"
    if al:
        return f"← {_ad_ek(onceki, 'den')} devraldı"
    return ""

def _fiziksel_zaman(U: pd.DataFrame) -> pd.Series:
    """S/N zinciri için gerçek zaman. Gece vardiyasının gece yarısı sonrası kayıtlarının
    hangi takvim gününe ait olduğunu zincirdeki diğer kayıtlardan çıkarır. Sadece U içindeki
    sıralama için kullanılır, ekranda görünen Tarih'i değiştirmez."""
    vv = U["Vardiya"].map(_vardiya)
    vb = vv.map(lambda t: t[0])
    ve = vv.map(lambda t: t[1])
    bas_dk = (U["_bas"] - U["Tarih"]).dt.total_seconds() / 60

    gece = ve < vb                    # gece vardiyası
    aksam = gece & (bas_dk >= vb)     # gece vardiyasının akşam kısmı (19:00-24:00)
    sabah = gece & (bas_dk < vb)      # gece yarısından sonrası (00:00-07:00)
    gun = ~gece

    aksam_var = set(zip(U.loc[aksam, "_a"], U.loc[aksam, "Tarih"]))
    gun_var = set(zip(U.loc[gun, "_a"], U.loc[gun, "Tarih"]))
    bir_gun = pd.Timedelta(days=1)

    def kayma(a, t, s):
        if not s:
            return 0
        if (a, t) in aksam_var:                                  # aynı gün akşamı da var: aynı vardiya, sabah ertesi güne düşer
            return 1
        if (a, t + bir_gun) in gun_var and (a, t) not in gun_var:  # gündüz kaydı ancak ertesi gün var
            return 1
        return 0                                                 # varsayılan: yazılan tarih sabahın takvim günü

    k = [kayma(a, t, s) for a, t, s in zip(U["_a"], U["Tarih"], sabah)]
    return U["_bas"] + pd.Series(pd.to_timedelta(k, unit="D"), index=U.index)
@st.cache_data(show_spinner="Veri hazırlanıyor…")
def hazirla(veri: pd.DataFrame):
    x = veri.reset_index(drop=True)
    n = len(x)
    def k(ad): return x[ad].tolist() if ad in x.columns else [None] * n

    tarih = pd.to_datetime(x["TARİH"], errors="coerce").dt.normalize()
    vard = [_txt(v) for v in k("VARDİYA")]
    bas = [_dk(v) for v in k("BAŞLANGIÇ_SAAT")]




    bit = [_dk(v) for v in k("BİTİŞ_SAAT")]
    
    parca = [_txt(v) for v in k("PARÇA NO_ÜRETİM_GİRİLEN")]
    if all(p is None for p in parca):
        parca = [_txt(v) for v in k("KOD & OPE.")]
        
    uzun_ref = [_txt(v) for v in k("PARÇA NO_UZUN_REFERANS")]
    sebep = [_txt(v) for v in k("Durus_Sebebi_Ham")]
    op = [_opno(v) for v in k("OPERASYON_NO")]
    kisi = [_txt(v) for v in k("Operator_Kod")]
    c_bitti = _bitti_kolonu(x)

    T = pd.DataFrame({
        "Tarih": tarih,
        "Vardiya": [v or "-" for v in vard],
        "Saat": [f"{_hm(a)} – {_hm(b)}" for a, b in zip(bas, bit)],
        "_bas": pd.to_datetime([_bas_zaman(t, v, b) for t, v, b in zip(tarih, vard, bas)]),
        "_bit_dk": bit,
        "Tezgah": [_txt(v) or "-" for v in k("TEZGAH")],
        "Operatör": [op_ad(v) if v else "-" for v in kisi],
        "Parça": parca,
        "Uzun Ref": [v or "-" for v in uzun_ref],
        "Op": op,
        "Parça – Op": [(p or "-") + (f" – Op {o}" if o else "") for p, o in zip(parca, op)],
        "S/N": [_txt(v) for v in k("S/N")],
        "Durum": [_txt(v) or "-" for v in k("Durum")],
        "Sebep": [v or "" for v in sebep],
        "Süre": pd.to_numeric(x["Sure_Saat"], errors="coerce").fillna(0.0),
        "_bitti": _bitti_maske(x[c_bitti]).tolist() if c_bitti else [False] * n,
    })
    _vb = pd.Series([_vardiya(v)[0] for v in vard], index=T.index)      # vardiya başlangıcı (dk)
    _bd = pd.Series(bas, index=T.index, dtype=float)                    # kaydın başlangıcı (dk)
    _ofs = (_bd - _vb) % 1440                                           # vardiya başlangıcından geçen dk
    T["_sira"] = (T["Tarih"] + pd.to_timedelta(_vb + _ofs, unit="m")).where(_vb.notna() & _bd.notna(), T["_bas"])
    def format_durum(d, s):
        d_clean = str(d).replace("_", " ")
        if d_clean in ["Plansız Duruş", "Set Up"] and s:
            return f"{d_clean} ({s})"
        return d_clean
    T["Durum / Sebep"] = [format_durum(d, s) for d, s in zip(T["Durum"], T["Sebep"])]

        # --- S/N Yolculuğu: sıralı devir zinciri ---
        # --- S/N zinciri: Adet (son satır) + İşlem Adımı (devir) ---
    # --- S/N zinciri: TUR mantığı (BİTTİ turu kapatır) ---
    U = T[(T["Durum"] == "Üretim") & T["S/N"].notna()].copy()
    U["_a"] = U["Parça"].fillna("-") + "|" + U["Op"].fillna("-") + "|" + U["S/N"]
    U["_pz"] = U["_sira"]
    U = U.sort_values(["_a", "_pz", "_bit_dk"], na_position="last")

    _once = U.groupby("_a", sort=False)["_bitti"].shift(1, fill_value=False).astype(int)
    U["_ac"] = U["_a"] + "#" + _once.groupby(U["_a"]).cumsum().astype(str)   # tur anahtarı

    U["_adet"] = U["_bitti"].astype(int)
    U["Adet"] = np.where(U["_adet"] == 1, "1", "")

    kisa = U["Operatör"].map(lambda a: a.split()[0] if a and a != "-" else "?")
    onceki = kisa.groupby(U["_ac"]).shift(1)
    sonraki = kisa.groupby(U["_ac"]).shift(-1)
    U["İşlem Adımı"] = [_devir_metni(b, o, s) for b, o, s in zip(kisa, onceki, sonraki)]

    T["_a"] = U["_a"].reindex(T.index)
    T["_pz"] = U["_pz"].reindex(T.index).fillna(T["_bas"])
    T["İşlem Adımı"] = U["İşlem Adımı"].reindex(T.index).fillna("")
    T["Adet"] = U["Adet"].reindex(T.index).fillna("")
    T["_adet"] = U["_adet"].reindex(T.index).fillna(0).astype(int)
    return T, c_bitti