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
    vg_blok = []
    if "Vardiya Grubu" in dd.columns:
        vg_t = _ozet(dd, ["Gün", "Vardiya Grubu"]).reset_index().sort_values(["Gün", "Vardiya Grubu"])
        vg_blok = ["[A2] GÜN × VARDİYA GRUBU (Gündüz 07–15, Akşam 15–23, Gece 23–07; satır başlangıç saatine göre)", _csv(vg_t)]
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

    # d0) En uzun olaylar + duruş sebepleri toplamı (TÜM aralık; 'en uzun / en çok' soruları için hazır)
    eva = d[d["Durum"].isin(["Plansız_Duruş", "Set_Up", "Rework"])].copy()
    eva["Tür"] = eva["Durum"].map({"Plansız_Duruş": "Duruş", "Set_Up": "Set-up", "Rework": "Rework"})
    eva["Sure_sa"] = eva["Süre"].round(2)
    eva["Aciklama"] = [s_ if s_ else (po.split("– Op ", 1)[1] if "– Op " in po else po)
                       for s_, po in zip(eva["Sebep"].fillna(""), eva["Parça – Op"].fillna(""))]
    en_uzun = (eva.sort_values("Sure_sa", ascending=False).groupby("Tür").head(3)
                  .sort_values(["Tür", "Sure_sa"], ascending=[True, False])
                  [["Tür", "Gün", "Vardiya", "Tezgah", "Operatör", "Parça – Op", "Sure_sa", "Aciklama"]])
    sebep_top = (eva[eva["Tür"] == "Duruş"].groupby("Aciklama")["Sure_sa"].sum().round(2)
                    .sort_values(ascending=False).head(8).reset_index())
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
        *vg_blok,
        "[B1] TEZGAH aralık toplamı", _csv(tez_top),
        "[B2] OPERATÖR aralık toplamı", _csv(op_top),
        *pasif,
        "[B3] GÜN × TEZGAH", _csv(tez_gun),
        "[B4] GÜN × OPERATÖR (bir günde kim kaç saat çalıştı)", _csv(op_gun),
        "[C1] PARÇA toplamı (Son_Op = rotanın son operasyonu)", _csv(pr),
        "[C2] PARÇA × OP (Rota: SON = nihai operasyon, ara = ara operasyon; Nihai_Parca yalnızca SON satırında dolu)", _csv(po),
        "[D0] EN UZUN 3 OLAY (Duruş / Set-up / Rework ayrı ayrı; TÜM ARALIK). 'En uzun / en çok süren' sorularını BURADAN cevapla", _csv(en_uzun),
        "[D0b] PLANSIZ DURUŞ SEBEPLERİ — toplam saat (ilk 8, TÜM ARALIK)", sebep_top.to_csv(index=False),
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

def _devir_metni(ben, onceki, sonraki, onceki_sa=None) -> str:
    al = isinstance(onceki, str) and onceki != ben     # ardışık aynı kişi = devir değil
    ver = isinstance(sonraki, str) and sonraki != ben
    ek = f" ({onceki_sa:.2f} sa)" if al and onceki_sa is not None and pd.notna(onceki_sa) else ""
    if al and ver:
        return f"← {_ad_ek(onceki, 'den')} aldı{ek} ➔ {_ad_ek(sonraki, 'e')} devretti"
    if ver:
        return f"➔ {_ad_ek(sonraki, 'e')} devretti"
    if al:
        return f"← {_ad_ek(onceki, 'den')} devraldı{ek}"
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



GUN_MODU = "07_07"             # "07_07" = patronun kuralı; "23_23" = Excel Tarih aynen (yalnızca karşılaştırma)
GUN_BASLANGIC_SAAT = 7
EXCEL_GUN_SINIRI_DK = 23 * 60  # Excel günü 23:00'te döner: başlangıç >= 23:00 ise takvim tarihi = Tarih - 1
_GUN_KISA = ["Pzt", "Sal", "Çar", "Per", "Cum", "Cmt", "Paz"]


def zaman_katmani_ekle(T, bas, gun_modu=GUN_MODU):
    """T["Tarih"] (Excel günü) + satır başlangıç dakikası -> gerçek takvim zamanı, üretim günü, hafta, vardiya grubu.
    T["Tarih"] artık ÜRETİM GÜNÜ olur (mevcut filtreler/gruplamalar otomatik bunu kullanır); ham değer "Ham Tarih"te."""
    bas_s = pd.Series(bas, index=T.index, dtype=float)
    ham = T["Tarih"]
    kaydir = (bas_s >= EXCEL_GUN_SINIRI_DK).astype(int)
    gercek = ham - pd.to_timedelta(kaydir, unit="D") + pd.to_timedelta(bas_s.fillna(12 * 60), unit="m")
    uretim = (gercek - pd.Timedelta(hours=GUN_BASLANGIC_SAAT)).dt.normalize() if gun_modu == "07_07" else ham
    iso = uretim.dt.isocalendar()

    def _aralik(p):
        if pd.isna(p):
            return "-"
        a, b, s = (p, p + pd.Timedelta(days=1), "07:00") if gun_modu == "07_07" else (p - pd.Timedelta(days=1), p, "23:00")
        return f"{_GUN_KISA[a.weekday()]} {s} → {_GUN_KISA[b.weekday()]} {s}"

    vg = pd.Series("-", index=T.index)
    vg[(bas_s >= 420) & (bas_s < 900)] = "Gündüz"
    vg[(bas_s >= 900) & (bas_s < 1380)] = "Akşam"
    vg[(bas_s >= 1380) | (bas_s < 420)] = "Gece"

    T["Ham Tarih"] = ham
    T["Tarih"] = uretim
    T["_bas"] = gercek            # gerçek kronolojik sıra (Tezgah/Operatör sıralaması ve devir mantığı)
    T["_sira"] = gercek
    T["_bas_dk"] = bas_s
    T["_yil"] = iso["year"].astype("Int64")
    T["_hno"] = iso["week"].astype("Int64")
    T["Hafta Günü"] = iso["day"].astype("Int64")
    T["Hafta Etiketi"] = [f"H{int(n)} · G{int(g)} ({_GUN_KISA[int(g) - 1]})" if pd.notna(n) else "-"
                          for n, g in zip(iso["week"], iso["day"])]
    T["Gün Aralığı"] = [_aralik(p) for p in uretim]
    T["Vardiya Grubu"] = vg
    return T


def zaman_tanilama_tablolari(A, B, gun):
    """A = 07_07 modunda T, B = 23_23 modunda T (ikisi de hazirla çıktısı).
    Döner: (ilk_baslangic, son_bitis, gun_tablosu, hafta_tablosu)."""
    def _saat(dk):
        return f"{int(dk) // 60 % 24:02d}:{int(dk) % 60:02d}"

    # 1) Excel günü sınırı varsayımı: her (Tarih, Tezgah) için ilk başlangıç / son bitiş (beklenen 23:00 / 23:00)
    x = B.dropna(subset=["Tarih", "_bas_dk", "_bit_dk"])
    off_b = (x["_bas_dk"] - EXCEL_GUN_SINIRI_DK) % 1440
    off_e = (x["_bit_dk"] - EXCEL_GUN_SINIRI_DK) % 1440
    g = (pd.DataFrame({"Tarih": x["Tarih"], "Tezgah": x["Tezgah"], "b": off_b, "e": off_e.where(off_e != 0, 1440)})
           .groupby(["Tarih", "Tezgah"]).agg(b=("b", "min"), e=("e", "max")))
    ilk = g["b"].map(lambda o: _saat(EXCEL_GUN_SINIRI_DK + o)).value_counts().head(6).rename_axis("İlk başlangıç").reset_index(name="Grup sayısı")
    son = g["e"].map(lambda o: _saat(EXCEL_GUN_SINIRI_DK + o)).value_counts().head(6).rename_axis("Son bitiş").reset_index(name="Grup sayısı")

    # 2) Seçili gün, iki modda yan yana
    def _ozet(T, d):
        s = T[T["Tarih"] == pd.Timestamp(d)]
        sm = lambda k: round(s.loc[s["Durum"] == k, "Süre"].sum(), 2)
        return {"Net Üretim (sa)": sm("Üretim"), "Plansız Duruş (sa)": sm("Plansız_Duruş"), "Set-up (sa)": sm("Set_Up"),
                "Rework (sa)": sm("Rework"), "Biten Parça": sn_sayilari(s)[1],
                "Aktif Tezgah": s.loc[s["Durum"].isin(CALISMA), "Tezgah"].nunique()}
    gun_t = (pd.DataFrame({"07:00→07:00 (patron)": _ozet(A, gun), "23:00→23:00 (Excel)": _ozet(B, gun)})
               .rename_axis("Metrik").reset_index())

    # 3) Seçili günün haftası (Pzt–Paz), G1–G7 yan yana
    p = pd.Timestamp(gun)
    pzt = p - pd.Timedelta(days=p.weekday())
    satir = []
    for i in range(7):
        d = pzt + pd.Timedelta(days=i)
        a, b = A[A["Tarih"] == d], B[B["Tarih"] == d]
        satir.append({"Gün": f"G{i + 1} ({_GUN_KISA[i]}) {d:%d.%m}",
                      "Net 07-07": round(a.loc[a["Durum"] == "Üretim", "Süre"].sum(), 2), "Biten 07-07": sn_sayilari(a)[1],
                      "Net 23-23": round(b.loc[b["Durum"] == "Üretim", "Süre"].sum(), 2), "Biten 23-23": sn_sayilari(b)[1]})
    return ilk, son, gun_t, pd.DataFrame(satir)

def hafta_secenekleri(T: pd.DataFrame) -> list:
    """[(etiket, pazartesi, pazar)] — en yeni hafta başta. T['Tarih'] = üretim günü."""
    import datetime as _dt
    h = (T.dropna(subset=["Tarih", "_yil", "_hno"])[["_yil", "_hno"]]
           .drop_duplicates().sort_values(["_yil", "_hno"], ascending=False))
    sonuc = []
    for y, n in zip(h["_yil"], h["_hno"]):
        pzt = pd.Timestamp(_dt.date.fromisocalendar(int(y), int(n), 1))
        paz = pzt + pd.Timedelta(days=6)
        sonuc.append((f"H{int(n)} ({pzt:%d.%m}–{paz:%d.%m})", pzt, paz))
    return sonuc
TEZGAH_HARIC = {"AB-12"}   # gerçek tezgah değil: pasif listelerinde gösterilmez


def tezgah_listesi(T: pd.DataFrame) -> list:
    return sorted(z for z in T["Tezgah"].unique() if z != "-" and z not in TEZGAH_HARIC)


def durum_saatleri(d: pd.DataFrame) -> dict:
    s = d.groupby("Durum")["Süre"].sum()
    return {"uretim": float(s.get("Üretim", 0.0)), "durus": float(s.get("Plansız_Duruş", 0.0)),
            "setup": float(s.get("Set_Up", 0.0)), "rework": float(s.get("Rework", 0.0))}
def kart_satiri(d: pd.DataFrame, c_bitti, onceki=None, onceki_biten=None):
    """5 kart tek satır. onceki (durum_saatleri çıktısı) verilirse fark oku gösterir. Sıfır = '—'."""
    s = durum_saatleri(d)
    biten = sn_sayilari(d)[1] if c_bitti else None
    sa = lambda v: "—" if not v else f"{v:,.2f} sa"

    def fark(a, b, tam=False):
        if b is None:
            return None
        f = a - b
        return f"{f:+,.0f}" if tam else f"{f:+,.2f} sa"

    g = (lambda k: None) if onceki is None else (lambda k: onceki[k])
    c = st.columns(5)
    c[0].metric("⚙️ Net Üretim", sa(s["uretim"]), fark(s["uretim"], g("uretim")))
    c[1].metric("🛑 Plansız Duruş", sa(s["durus"]), fark(s["durus"], g("durus")), delta_color="inverse")
    c[2].metric("🔧 Set-up", sa(s["setup"]), fark(s["setup"], g("setup")), delta_color="inverse")
    c[3].metric("♻️ Rework", sa(s["rework"]), fark(s["rework"], g("rework")), delta_color="inverse")
    c[4].metric("✅ Biten Parça", "—" if not biten else biten,
                None if biten is None else fark(biten, onceki_biten, True),
                help="Rotasının SON (FİNİSH) operasyonu BİTTİ olan farklı S/N sayısı (nihai mamul).")

def _kisa_adlar(op: pd.Series) -> pd.Series:
    """İlk ad; aynı ilk ada sahip birden fazla operatör varsa (MEHMET ALİ / MEHMET EMRE) ilk iki kelime."""
    ilk = op.map(lambda a: a.split()[0] if isinstance(a, str) and a != "-" else "?")
    tekil = pd.DataFrame({"t": op.dropna().unique()})
    tekil["i"] = tekil["t"].map(lambda a: a.split()[0] if a != "-" else "?")
    cakisan = set(tekil.loc[tekil["i"].duplicated(keep=False), "i"])
    return pd.Series([" ".join(a.split()[:2]) if (i in cakisan and i != "?") else i
                      for a, i in zip(op, ilk)], index=op.index, dtype=object)


def parca_durumu_df(d: pd.DataFrame) -> pd.DataFrame:
    """Üretim kaydı + S/N olan her fiziksel parça (Parça + S/N) için TEK satır.
    Durum: nihai (rotanın SON op'u BİTTİ) > ara (başka bir op BİTTİ) > devam (BİTTİ yok).
    Op: 'ara' parçanın en son biten operasyonu. Satır sayısı = sn_sayilari()[0]."""
    s = d[(d["Durum"] == "Üretim") & d["S/N"].notna()]
    if s.empty:
        return pd.DataFrame(columns=["Parça", "Durum", "Op"])
    sira = "_sira" if "_sira" in s.columns else "_bas"
    s = s.assign(Parça=s["Parça"].fillna("-"))
    s = s.assign(_k=s["Parça"] + "|" + s["S/N"])
    nihai_k = set(s.loc[_nihai_biten_maske(s), "_k"])
    tam = s[s["_adet"] == 1].sort_values(sira).drop_duplicates("_k", keep="last").set_index("_k")["Op"]
    k = s.drop_duplicates("_k")[["_k", "Parça"]].set_index("_k")
    k["Durum"] = np.where(k.index.isin(nihai_k), "nihai", np.where(k.index.isin(tam.index), "ara", "devam"))
    k["Op"] = tam.reindex(k.index).where(k["Durum"] == "ara", "-").fillna("-")
    return k.reset_index(drop=True)


def parca_durumu_tablosu(k: pd.DataFrame) -> pd.DataFrame:
    ct = pd.crosstab(k["Parça"], k["Durum"]).reindex(columns=["nihai", "ara", "devam"], fill_value=0)
    t = pd.DataFrame({"İşlem Gören": ct.sum(axis=1), "✅ Nihai": ct["nihai"],
                      "🔧 Ara Op. Bitti": ct["ara"], "⏳ Devam": ct["devam"]})
    ara = k[k["Durum"] == "ara"].groupby(["Parça", "Op"]).size().reset_index(name="n")
    ara["_o"] = pd.to_numeric(ara["Op"], errors="coerce").fillna(9999)
    ara = ara.sort_values(["Parça", "_o"])
    detay = {p: " · ".join(f"Op {o}: {n}" for o, n in zip(a["Op"], a["n"])) for p, a in ara.groupby("Parça")}
    t["Ara operasyon dağılımı"] = pd.Series(detay, dtype=object).reindex(t.index).fillna("")
    return t.sort_values("İşlem Gören", ascending=False).reset_index()


def parca_durumu_goster(d: pd.DataFrame, c_bitti):
    if not c_bitti:
        return
    k = parca_durumu_df(d)
    if k.empty:
        return
    n = k["Durum"].value_counts()
    st.caption(f"📦 {len(k)} parça işlem gördü → ✅ {n.get('nihai', 0)} nihai bitti (FİNİSH) · "
               f"🔧 {n.get('ara', 0)} ara operasyonu bitti, sonraki operasyonu bekliyor · "
               f"⏳ {n.get('devam', 0)} devam ediyor")
    with st.expander("📦 Parça durumu detayı"):
        st.dataframe(parca_durumu_tablosu(k), hide_index=True, use_container_width=True)


def donem_sec(T: pd.DataFrame, anahtar: str, aralik_izni: bool = False):
    """Hafta + Gün seçici (07:00→07:00). Döner (d_bas, d_bit, kaydirma_gun) ya da None.
    kaydirma_gun: önceki dönemle kıyas için (gün=1, hafta=7, aralık=uzunluk)."""
    mn, mx = T["Tarih"].min().date(), T["Tarih"].max().date()
    haftalar = hafta_secenekleri(T)
    YOK = "— Tarih aralığı —"
    c_h, c_t = st.columns([2, 6])
    h_sec = c_h.selectbox("🗓️ Hafta", ([YOK] if aralik_izni else []) + [h[0] for h in haftalar],
                          key=f"{anahtar}_hafta")
    if h_sec == YOK:
        sec = c_t.date_input("📅 Üretim Günü", value=(mx, mx), min_value=mn, max_value=mx,
                             format="DD.MM.YYYY", key=f"{anahtar}_tarih")
        if isinstance(sec, (tuple, list)):
            if not sec:
                return None
            d_bas, d_bit = sec[0], sec[-1]
        else:
            d_bas = d_bit = sec
        return d_bas, d_bit, (d_bit - d_bas).days + 1

    _, h1, h2 = next(h for h in haftalar if h[0] == h_sec)
    gunler = ["Tüm Hafta"] + [f"G{i + 1} {_GUN_KISA[i]} {h1 + pd.Timedelta(days=i):%d.%m}" for i in range(7)]
    g_sec = c_t.radio("📅 Gün", gunler, horizontal=True, key=f"{anahtar}_gun_{h1:%Y%m%d}")
    if g_sec == "Tüm Hafta":
        c_t.caption(f"🕖 Pzt {h1:%d.%m} 07:00 → Pzt {h2 + pd.Timedelta(days=1):%d.%m} 07:00")
        return h1.date(), h2.date(), 7
    i = gunler.index(g_sec) - 1
    g = h1 + pd.Timedelta(days=i)
    c_t.caption(f"🕖 {_GUN_KISA[i]} {g:%d.%m} 07:00 → {_GUN_KISA[(i + 1) % 7]} {g + pd.Timedelta(days=1):%d.%m} 07:00")
    return g.date(), g.date(), 1


def olay_gunlugu(G: pd.DataFrame, tum_tez: list, d_bas, d_bit) -> pd.DataFrame:
    """Duruş / Set-up / Rework olayları (zamana göre) + dönemde hiç kaydı olmayan tezgahlar."""
    ev = G[G["Durum"].isin(["Plansız_Duruş", "Set_Up", "Rework"])].sort_values("_sira")
    ad = {"Plansız_Duruş": "Duruş", "Set_Up": "Set-up", "Rework": "Rework"}
    out = pd.DataFrame({
        "Üretim Günü": ev["Tarih"].dt.strftime("%d.%m.%Y"), "Vardiya": ev["Vardiya"], "Saat": ev["Saat"],
        "Tezgah": ev["Tezgah"], "Operatör": ev["Operatör"], "Olay": ev["Durum"].map(ad), "Sebep": ev["Sebep"],
        "Süre (sa)": ev["Süre"].round(2),
        "Parça/S-N": ev["Parça – Op"] + ev["S/N"].map(lambda s: f" · {s}" if isinstance(s, str) else ""),
    })
    pasif = [z for z in tum_tez if z not in set(G["Tezgah"])]
    if pasif:
        per = f"{d_bas:%d.%m.%Y}" + ("" if d_bas == d_bit else f" – {d_bit:%d.%m.%Y}")
        out = pd.concat([out, pd.DataFrame({"Üretim Günü": per, "Tezgah": pasif, "Olay": "Pasif Tezgah",
                                            "Sebep": "Dönemde hiç kayıt yok"})], ignore_index=True)
    for c in out.columns:
        if c != "Süre (sa)":
            out[c] = out[c].fillna("")
    return out.reset_index(drop=True)


@st.cache_data(show_spinner="Veri hazırlanıyor…")
def hazirla(veri: pd.DataFrame, gun_modu: str = GUN_MODU):
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
    zaman_katmani_ekle(T, bas, gun_modu)
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

    kisa = _kisa_adlar(U["Operatör"])
    g_ac = U["_ac"]
    onceki = kisa.groupby(g_ac).shift(1)
    sonraki = kisa.groupby(g_ac).shift(-1)
    blok = (kisa != onceki).astype(int).groupby(g_ac).cumsum()    # aynı kişinin ardışık satırları = 1 blok
    onceki_sa = U["Süre"].groupby([g_ac, blok]).transform("sum").groupby(g_ac).shift(1)
    U["İşlem Adımı"] = [_devir_metni(b, o, s, sa) for b, o, s, sa in zip(kisa, onceki, sonraki, onceki_sa)]

    T["_a"] = U["_a"].reindex(T.index)
    T["_pz"] = U["_pz"].reindex(T.index).fillna(T["_bas"])
    T["İşlem Adımı"] = U["İşlem Adımı"].reindex(T.index).fillna("")
    T["Adet"] = U["Adet"].reindex(T.index).fillna("")
    T["_adet"] = U["_adet"].reindex(T.index).fillna(0).astype(int)
    return T, c_bitti