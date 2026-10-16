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
    
def nihai_biten_satirlar(d: pd.DataFrame) -> pd.Index:
    """Biten Parça kartındaki parçaların SON operasyon (FİNİSH) BİTTİ satırlarının indeksi.
    Tekrar işlenen parça için son bitişi alır. Ana tabloyu bu satırlara daraltmak için kullanılır."""
    s = d[(d["Durum"] == "Üretim") & d["S/N"].notna()]
    b = s[_nihai_biten_maske(s)]
    if b.empty:
        return b.index
    sira = "_sira" if "_sira" in b.columns else "_bas"
    return b.sort_values(sira).drop_duplicates(["Parça", "S/N"], keep="last").index
    
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

_DURUM_KOL = {"Üretim": "⚙️ Net Üretim (sa)", "Plansız_Duruş": "🛑 Plansız Duruş (sa)",
              "Set_Up": "🔧 Set-up (sa)", "Rework": "♻️ Rework (sa)"}


def saat_tablosu(d: pd.DataFrame, anahtar: str, tum: list | None = None) -> pd.DataFrame:
    """anahtar (Tezgah / Operatör) bazında Üretim, Duruş, Set-up, Rework saatleri + toplam.
    tum verilirse kaydı olmayanlar da 0.00 satır olarak gelir."""
    if d.empty:
        p = pd.DataFrame(0.0, index=pd.Index(tum or [], name=anahtar), columns=list(_DURUM_KOL))
    else:
        p = (d.pivot_table(index=anahtar, columns="Durum", values="Süre", aggfunc="sum", fill_value=0)
              .reindex(columns=list(_DURUM_KOL), fill_value=0))
        if tum is not None:
            p = p.reindex(tum, fill_value=0)
    p = p.rename(columns=_DURUM_KOL)
    p["⏱️ Toplam (sa)"] = p.sum(axis=1)
    return p.round(2).rename_axis(anahtar).reset_index()


def gunluk_ai_baglami_olustur(df_tablo, secili_tarih, tum_tezgah=None, tum_operator=None, gun_siniri=14, detay_gun=7):
    """Seçili aralığı AI için kompakt, ÖN-HESAPLANMIŞ metne çevirir (AI toplama yapmasın). Sadece str döndürür."""
    if isinstance(secili_tarih, (tuple, list)) and len(secili_tarih) > 0:
        a, b = secili_tarih[0], secili_tarih[-1]
        tarih_txt = a.strftime("%d.%m.%Y") if a == b else f"{a.strftime('%d.%m.%Y')} – {b.strftime('%d.%m.%Y')}"
    else:
        tarih_txt = secili_tarih.strftime("%d.%m.%Y") if hasattr(secili_tarih, "strftime") else str(secili_tarih)
    if df_tablo is None or df_tablo.empty:
        return f"Tarih: {tarih_txt} | Bu filtrelerle kayıt yok."

    d = df_tablo.copy()
    d["Gün"] = pd.to_datetime(d["Tarih"]).dt.date
    d["Vardiya"] = d["Vardiya"].fillna("-")
    d["_nihai"] = d.index.isin(nihai_biten_satirlar(d)).astype(int)
    son_gunler = sorted(d["Gün"].unique())[-gun_siniri:]
    dd = d[d["Gün"].isin(son_gunler)]
    sira = "_sira" if "_sira" in d.columns else "_bas"

    def _ozet(x, anahtar):
        p = (x.pivot_table(index=anahtar, columns="Durum", values="Süre", aggfunc="sum", fill_value=0)
              .reindex(columns=["Üretim", "Plansız_Duruş", "Set_Up", "Rework"], fill_value=0))
        p.columns = ["Uretim_sa", "Durus_sa", "SetUp_sa", "Rework_sa"]
        p["Toplam_sa"] = p.sum(axis=1)
        p["Tamamlanan_Op"] = x.groupby(anahtar)["_adet"].sum().reindex(p.index).fillna(0).astype(int)
        p["Nihai_Parca"] = x.groupby(anahtar)["_nihai"].sum().reindex(p.index).fillna(0).astype(int)
        return p.round(2)

    def _csv(t):
        t = t.copy()
        if "Gün" in t.columns:
            t["Gün"] = t["Gün"].map(lambda g: f"{g:%d.%m.%Y}")
        return t.to_csv(index=False)

    # a) Gün × Vardiya (+ her gün için hazır TÜM GÜN toplamı)
    a1 = _ozet(dd, ["Gün", "Vardiya"]).reset_index()
    a2 = _ozet(dd, ["Gün"]).reset_index().assign(Vardiya="TÜM GÜN")
    ta = pd.concat([a1, a2], ignore_index=True)
    ta["_s"] = (ta["Vardiya"] == "TÜM GÜN").astype(int)
    ta = ta.sort_values(["Gün", "_s", "Vardiya"]).drop(columns="_s")
    ta = ta[["Gün", "Vardiya"] + [c for c in ta.columns if c not in ("Gün", "Vardiya")]]

    tum = _ozet(d.assign(_k="x"), ["_k"]).iloc[0]
    aralik = (f"TÜM ARALIK ({tarih_txt}): Üretim {tum['Uretim_sa']:.2f} sa | Plansız duruş {tum['Durus_sa']:.2f} sa | "
              f"Set-up {tum['SetUp_sa']:.2f} sa | Rework {tum['Rework_sa']:.2f} sa | Toplam {tum['Toplam_sa']:.2f} sa | "
              f"Tamamlanan_Op {int(tum['Tamamlanan_Op'])} | Nihai_Parca {int(tum['Nihai_Parca'])}")

    # b) Tezgah ve operatör (aralık toplamı + gün bazlı)
    dz = d[d["Tezgah"].notna() & (d["Tezgah"] != "-")]
    do = d[d["Operatör"].notna() & (d["Operatör"] != "-")]
    tez_top = _ozet(dz, ["Tezgah"]).reset_index()
    op_top = _ozet(do, ["Operatör"]).reset_index()
    tez_gun = _ozet(dz[dz["Gün"].isin(son_gunler)], ["Gün", "Tezgah"]).reset_index()
    op_gun = _ozet(do[do["Gün"].isin(son_gunler)], ["Gün", "Operatör"]).reset_index()
    pasif = []
    if tum_tezgah:
        p_t = [z for z in tum_tezgah if z not in set(dz["Tezgah"])]
        pasif.append("Pasif tezgah (bu aralıkta kaydı yok): " + (", ".join(p_t) or "yok"))
    if tum_operator:
        p_o = [o for o in tum_operator if o not in set(do["Operatör"])]
        pasif.append("Pasif operatör (bu aralıkta kaydı yok): " + (", ".join(p_o) or "yok"))

    # c) Parça ve operasyon
    lk = (d[d["Uzun Ref"].astype(str).str.strip().ne("-") & d["Parça"].notna()]
          .groupby("Parça")["Uzun Ref"].first().astype(str).str.strip())
    son_op = lk.map(FINAL_OP_HARITASI)
    pr = _ozet(d[d["Parça"].notna()], ["Parça"])
    pr["Islem_Goren_SN"] = (d[(d["Durum"] == "Üretim") & d["S/N"].notna()].groupby("Parça")["S/N"].nunique()
                            .reindex(pr.index).fillna(0).astype(int))
    pr["Son_Op"] = son_op.reindex(pr.index).fillna("?")
    pr = pr.reset_index()
    u = d[d["Durum"] == "Üretim"].copy()
    u["Parça"] = u["Parça"].fillna("-")
    u["Op"] = u["Op"].fillna("-")
    po = (u.groupby(["Parça", "Op"]).agg(Uretim_sa=("Süre", "sum"), Tamamlanan_Op=("_adet", "sum"),
                                         Nihai_Parca=("_nihai", "sum")).round(2).reset_index())
    po["Rota"] = [("SON" if o == son_op.get(p) else "ara") if pd.notna(son_op.get(p)) else "?"
                  for p, o in zip(po["Parça"], po["Op"])]
    po["_n"] = pd.to_numeric(po["Op"], errors="coerce").fillna(9999)
    po = po.sort_values(["Parça", "_n"]).drop(columns="_n")

    # d) Duruş / Set-up / Rework olayları (tek tek)
    ev = dd[dd["Durum"].isin(["Plansız_Duruş", "Set_Up", "Rework"])].sort_values(["Gün", sira]).copy()
    ev["Tür"] = ev["Durum"].map({"Plansız_Duruş": "Duruş", "Set_Up": "Set-up", "Rework": "Rework"})
    ev["Sure_sa"] = ev["Süre"].round(2)
    ev = ev[["Gün", "Vardiya", "Tezgah", "Operatör", "Parça – Op", "Sure_sa", "Tür", "Sebep"]]

    # e) Gün × Vardiya × Tezgah × Operatör × Parça-Op (S/N bazlı satırlar gruplanmış; son detay_gun gün)
    detay_gunler = son_gunler[-detay_gun:]
    ue = u[u["Gün"].isin(detay_gunler)].copy()
    ue["Parca_Op"] = ue["Parça"] + "-" + ue["Op"]
    anah = ["Gün", "Vardiya", "Tezgah", "Operatör", "Parca_Op"]
    ge = ue.groupby(anah).agg(Sure_sa=("Süre", "sum"), Tamamlanan_Op=("_adet", "sum"), Nihai=("_nihai", "sum")).round(2)
    ge["Nihai_SN"] = (ue[ue["_nihai"] == 1].groupby(anah)["S/N"].apply(lambda s: ";".join(s.astype(str)))
                      .reindex(ge.index).fillna(""))
    ge = ge.reset_index().sort_values(["Gün", "Vardiya", "Tezgah", "Operatör"])

    baslik = (f"GÜNLÜK ÖZET | Tarih aralığı: {tarih_txt} | Satır: {len(d)} | Gün bazlı tablolar SON {len(son_gunler)} günü kapsar. "
              "Tüm toplamlar HAZIR hesaplanmıştır: TOPLAM / TÜM GÜN / TÜM ARALIK satırlarını kullan, kendin toplama. "
              "Nihai_Parca = rotanın SON operasyonu BİTTİ olan parça (nihai mamul); Tamamlanan_Op = ara operasyonlar dahil biten operasyon sayısı. "
              "Gün = Excel'deki vardiya tarihi (gece vardiyasının sabah kayıtları vardiyanın başladığı güne yazılır).")
    return "\n".join([
        baslik, aralik,
        "\n[A] GÜN × VARDİYA (saat + Tamamlanan_Op + Nihai_Parca; her günün sonunda TÜM GÜN toplamı)", _csv(ta),
        "[B1] TEZGAH aralık toplamı", _csv(tez_top),
        "[B2] OPERATÖR aralık toplamı", _csv(op_top),
        *pasif,
        "[B3] GÜN × TEZGAH", _csv(tez_gun),
        "[B4] GÜN × OPERATÖR (bir günde kim kaç saat çalıştı)", _csv(op_gun),
        "[C1] PARÇA toplamı (Son_Op = rotanın son operasyonu)", _csv(pr),
        "[C2] PARÇA × OP (Rota: SON = nihai operasyon, ara = ara operasyon; Nihai_Parca yalnızca SON satırında dolu)", _csv(po),
        "[D] DURUŞ / SET-UP / REWORK OLAYLARI (tek tek; Parça – Op alanında sebep metni de olabilir)", _csv(ev),
        f"[E] GÜN × VARDİYA × TEZGAH × OPERATÖR × PARÇA-OP — yalnızca SON {len(detay_gunler)} gün: "
        + ", ".join(f"{g_:%d.%m}" for g_ in detay_gunler) + " (Nihai_SN = o grupta nihai biten S/N'ler)", _csv(ge),
    ])

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