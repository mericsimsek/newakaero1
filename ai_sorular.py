# -*- coding: utf-8 -*-
"""
ai_sorular.py — AI Asistan S/N soru bankası: CEVAP ANAHTARI ham Excel'den (openpyxl, modülden bağımsız) hesaplanır.

İki mod:
  python ai_sorular.py            → ÇEVRİMDIŞI: asistanın göreceği bağlam metnini (sn_ai_baglami) metin olarak
                                    geri okuyup her sorunun cevabını oradan çıkarır ve cevap anahtarıyla kıyaslar.
                                    (Bağlam yeterli ve doğru mu? Model hatası burada ölçülmez.)
  python ai_sorular.py --gemini   → CANLI: aynı bağlamla gerçek Gemini'ye soruları sorar (GEMINI_API_KEY gerekir),
                                    yanıttaki sayı/isimleri cevap anahtarıyla kıyaslar.
"""
import sys, re, io, datetime as dt
from collections import defaultdict
sys.path.insert(0, '/tmp'); sys.path.insert(0, '/mnt/user-data/outputs')
import pandas as pd
from openpyxl import load_workbook
import sys, re, io, time, datetime as dt
YOL = 'data/incoming/puredata1.xlsx'
KISA, OP = 'M01G03', '60'
TOL = 0.20

def saat(t): return t.hour + t.minute / 60 + t.second / 3600
def tr_up(s): return " ".join(str(s).replace('i', 'İ').replace('ı', 'I').upper().split())

# ------------------------------------------------------------------ CEVAP ANAHTARI (bağımsız)
def cevap_anahtari():
    wb = load_workbook(YOL, data_only=True, read_only=True)
    S = [r for r in wb['Giriş'].iter_rows(min_row=3, values_only=True) if r[0] is not None]
    (TEZ, OPE, TAR, VAR, DUR, PARCA, UZUN, SN, OPN, PRC, FIN, KOD, BAS, BIT, SURE, SURE1, UAL, SAL, CT, US, YS, SS, IMAL) = range(23)
    u = [r for r in S if r[DUR] == 'Ü' and KISA in str(r[PARCA]) and str(r[OPN]).strip() == OP]
    tot, kisi, vard, son, tez, kay = (defaultdict(float), defaultdict(list), defaultdict(list), {}, defaultdict(list), defaultdict(int))
    for r in u:
        k = str(r[SN]); tot[k] += saat(r[SURE]); kisi[k].append(tr_up(r[OPE])); vard[k].append(r[VAR]); tez[k].append(r[TEZ]); kay[k] += 1
        son[k] = max(son.get(k, r[TAR]), r[TAR])
    ct = max(r[CT] or 0 for r in u)
    A = {'adet': len(tot), 'toplam': sum(tot.values()), 'ort': sum(tot.values()) / len(tot)}
    v = sorted(tot.values()); A['medyan'] = (v[len(v)//2 - 1] + v[len(v)//2]) / 2 if len(v) % 2 == 0 else v[len(v)//2]
    A['bolunmus'] = sum(1 for x in kay.values() if x > 1)
    A['yavas'] = sum(1 for x in tot.values() if x > ct * (1 + TOL)); A['ct'] = ct
    # hafta (Perşembe çapa 2026-01-01)
    hf = defaultdict(int); hg = defaultdict(set)
    for k, d in son.items():
        w = (d - dt.datetime(2026, 1, 1)).days // 7 + 1; hf[w] += 1; hg[w].add(d)
    A['hafta'] = dict(hf); A['hafta_gunluk'] = {w: hf[w] / len(hg[w]) for w in hf}
    A['en_cok_hafta'] = max(hf, key=hf.get)
    sirali = sorted(tot, key=lambda k: (-tot[k], son[k]))
    A['en_yavas2'] = [(k, tot[k]) for k in sirali[:2]]
    sn = '2G7JE'; A['sn'] = {'s': sn, 'saat': tot[sn], 'kisi': list(dict.fromkeys(kisi[sn])), 'vard': list(dict.fromkeys(vard[sn]))}
    # operatör / vardiya / tezgah payı
    def pay(grup_fn):
        h, p, sset, solo = defaultdict(float), defaultdict(float), defaultdict(set), defaultdict(set)
        for r in u:
            g = grup_fn(r); k = str(r[SN]); x = saat(r[SURE]); h[g] += x; p[g] += x / tot[k]; sset[g].add(k)
            if len(set(kisi[k])) == 1: solo[g].add(k)
        return {g: dict(saat=h[g], pay=p[g], esd=h[g] / p[g], sn=len(sset[g]), solo=len(solo[g]),
                        solo_ort=(sum(tot[k] for k in solo[g]) / len(solo[g]) if solo[g] else None)) for g in h}
    A['op'] = pay(lambda r: tr_up(r[OPE])); A['vard_p'] = pay(lambda r: r[VAR]); A['tez_p'] = pay(lambda r: r[TEZ])
    A['en_hizli_op'] = min(A['op'], key=lambda g: A['op'][g]['esd'])
    A['en_yavas_vard'] = max(A['vard_p'], key=lambda g: A['vard_p'][g]['esd'])
    s_op = [r for r in S if r[DUR] == 'S' and KISA in str(r[PARCA]) and str(r[OPN]).strip().startswith(OP)]
    A['setup'] = sum(saat(r[SURE]) for r in s_op); A['setup_kayit'] = len(s_op)
    s_h32 = [r for r in s_op if (r[TAR] - dt.datetime(2026, 1, 1)).days // 7 + 1 == 32]; A['setup_h32'] = sum(saat(r[SURE]) for r in s_h32)
    # rota (2G7JA)
    rr = [r for r in S if r[DUR] == 'Ü' and str(r[SN]) == '2G7JA' and KISA in str(r[PARCA])]
    A['rota'] = {'toplam': sum(saat(r[SURE]) for r in rr), 'op15_kisi': tr_up([r[OPE] for r in rr if str(r[OPN]).strip() == '15'][0]),
                 'op60_kisi': tr_up([r[OPE] for r in rr if str(r[OPN]).strip() == '60'][0])}
    d = [r for r in S if r[DUR] == 'D' and KISA in str(r[PARCA])]
    A['durus'] = {'toplam': sum(saat(r[SURE]) for r in d), 'ITEM TEKRARI': (sum(1 for r in d if str(r[OPN]) == 'ITEM TEKRARI'),
                  sum(saat(r[SURE]) for r in d if str(r[OPN]) == 'ITEM TEKRARI'))}
    pk = defaultdict(set)
    for r in S:
        if r[SN]: pk[str(r[SN])].add(str(r[PARCA]))
    A['2G7JC_kodlar'] = sorted(pk['2G7JC'])
    return A

# ------------------------------------------------------------------ SORU BANKASI
# beklenen: [(tür, değer)]  tür: 'sayi' (±tol, alternatifli liste olabilir), 'metin' (Türkçe-duyarlı içerir), 'regex'
def sorular(A):
    op = A['op']; y = op['YAŞAR ŞENOL']; hy = op['HÜSEYİN YILMAZ']; n = A['sn']
    return [
     ('Q01', '2547M01G03 operasyon 60\'tan kaç adet parça üretildi?', [('sayi', [A['adet']])]),
     ('Q02', 'Bu parçanın ortalama ve medyan işlenme süresi kaç saat?', [('sayi', [A['ort']]), ('sayi', [A['medyan']])]),
     ('Q03', f"{n['s']} seri numaralı parça kaç saatte işlendi, kim çalıştı, hangi vardiya?",
        [('sayi', [n['saat']])] + [('metin', k) for k in n['kisi']] + [('metin', v) for v in n['vard']]),
     ('Q04', 'Hafta 34\'te (Perşembe başlangıçlı) kaç adet çıktı?', [('sayi', [A['hafta'][34]])]),
     ('Q05', 'Hangi hafta en çok adet çıktı, kaç adet?', [('sayi', [A['en_cok_hafta']]), ('sayi', [A['hafta'][A['en_cok_hafta']]])]),
     ('Q06', 'Hafta 35\'te günlük ortalama kaç adet çıkmış?', [('sayi', [A['hafta_gunluk'][35]])]),
     ('Q07', 'En yavaş iki S/N hangileri, kaç saat sürmüş?', [('metin', k) for k, _ in A['en_yavas2']] + [('sayi', [A['en_yavas2'][0][1]])]),
     ('Q08', 'Katalog C/T\'yi %20\'den fazla aşan kaç S/N var?', [('sayi', [A['yavas']])]),
     ('Q09', 'Kaç parça vardiya değişiminde ya da birden fazla kayıtta bölünerek yapılmış?', [('sayi', [A['bolunmus']])]),
     ('Q10', 'Yaşar Şenol\'un eşdeğer ortalama süresi ve tek başına bitirdiği adet kaç?', [('sayi', [y['esd']]), ('sayi', [y['solo']])]),
     ('Q11', 'Eşdeğer ortalama süreye göre en hızlı operatör kim, kaç saat?', [('metin', A['en_hizli_op']), ('sayi', [op[A['en_hizli_op']]['esd']])]),
     ('Q12', 'Hangi vardiya adet başına en yavaş, ortalaması kaç?', [('metin', A['en_yavas_vard']), ('sayi', [A['vard_p'][A['en_yavas_vard']]['esd']])]),
     ('Q13', 'Bu parça ve operasyon için toplam set-up süresi kaç saat?', [('sayi', [A['setup']])]),
     ('Q14', '2G7JA parçası tüm operasyonlarda toplam kaç saat harcadı? 15. ve 60. operasyonu kim yaptı?',
        [('sayi', [A['rota']['toplam']]), ('metin', A['rota']['op15_kisi']), ('metin', A['rota']['op60_kisi'])]),
     ('Q15', 'Bu parçaya yazılmış duruşlardan "ITEM TEKRARI" kaç kez ve toplam kaç saat?',
        [('sayi', [A['durus']['ITEM TEKRARI'][0]]), ('sayi', [A['durus']['ITEM TEKRARI'][1], round(A['durus']['ITEM TEKRARI'][1], 1)])]),
     ('Q16', 'AB-10 tezgahında kaç adet (S/N) işlendi?', [('sayi', [A['tez_p']['AB-10']['pay'], A['tez_p']['AB-10']['sn']])]),
     ('Q17', '2547M01G03 operasyon 10\'da kaç adet üretildi?', [('regex', r'yok|bulunm|izleme|kapsam|hesaplan|mevcut değil|veri')]),
     ('Q18', '2G7JC seri numarası hangi parça kodlarına yazılmış?', [('metin', k) for k in A['2G7JC_kodlar']]),
     ('Q19', 'Bu operasyonun katalog C/T değeri kaç saat?', [('sayi', [A['ct']])]),
     ('Q20', 'Hüseyin Yılmaz kaç S/N\'ye dokundu, toplam kaç saat çalıştı?', [('sayi', [hy['sn']]), ('sayi', [hy['saat']])]),
     ('Q21', 'Hafta 32\'de set-up kaç saat tutmuş?', [('sayi', [A['setup_h32']])]),
     ('Q22', 'Özcan Çelik\'in tek başına bitirdiği işlerin ortalama süresi kaç saat?', [('sayi', [op['ÖZCAN ÇELİK']['solo_ort']])]),
    ]

# ------------------------------------------------------------------ DEĞERLENDİRME
def sayilar(metin):
    m = re.sub(r'(?<=\d),(?=\d)', '.', metin)
    return [float(x) for x in re.findall(r'\d+(?:\.\d+)?', m)]

def degerlendir(cevap, beklenen):
    eksik = []
    for tur, v in beklenen:
        if tur == 'sayi':
            s = sayilar(cevap)
            if not any(abs(a - b) <= 0.06 for a in s for b in v): eksik.append(f'sayı {[round(x,2) for x in v]}')
        elif tur == 'metin':
            if tr_up(v).replace(' ', '') not in tr_up(cevap).replace(' ', ''): eksik.append(f'"{v}"')
        elif tur == 'regex':
            if not re.search(v, cevap, re.I): eksik.append('belirsizlik/uyarı ifadesi')
    return eksik

# ------------------------------------------------------------------ ÇEVRİMDIŞI: bağlamdan okuma
def bagla_tablolar(metin):
    parca = re.split(r'### TABLO (\d+): ', metin)
    T = {}
    for i in range(1, len(parca), 2):
        gov = parca[i + 1]; satirlar = gov.split('\n', 1)
        try: T[int(parca[i])] = pd.read_csv(io.StringIO(satirlar[1].split('\n(Operasyonu')[0]))
        except Exception: pass
    return T

def baglamdan_cevap(qid, metin, T, A):
    """Mükemmel okuyucu: cevabı YALNIZCA prompt metninden (CSV'leri geri okuyarak) üretir."""
    oz, hf, st_, op_, vd, tz, rt, ds, uc = (T.get(i) for i in (10, 12, 13, 15, 16, 17, 18, 19, 20))
    ust = metin.split('\n', 1)[0]
    kisi = lambda s: tr_up(s)
    
    # HATA VEREN Q01 ve Q02 SATIRLARI DÜZELTİLDİ (.format kullanıldı)
    if qid == 'Q01': return "{} adet; başlık: {}".format(len(oz), re.search(r'(\d+) adet', ust).group(1))
    if qid == 'Q02': return "ortalama {}, medyan {}".format(re.search(r'ortalama ([\d.]+)', ust).group(1), re.search(r'medyan ([\d.]+)', ust).group(1))
    
    if qid == 'Q03':
        r = oz[oz['S/N'] == '2G7JE'].iloc[0]; return f"{r.Toplam_Saat} sa, {r.Operatorler}, {r.Vardiyalar}"
    if qid == 'Q04': return str(hf[hf['Hafta'] == 34].iloc[0]['Adet'])
    if qid == 'Q05': r = hf.sort_values('Adet', ascending=False).iloc[0]; return f"hafta {r.Hafta}: {r.Adet}"
    if qid == 'Q06': return str(hf[hf['Hafta'] == 35].iloc[0]['Gunluk_Ort_Adet'])
    if qid == 'Q07': g = uc[uc['Grup'] == 'EN YAVAŞ 5'].head(2); return ' '.join(f"{a} {b}" for a, b in zip(g['S/N'], g['Toplam_Saat']))
    if qid == 'Q08': return re.search(r'(\d+) yavaş S/N', ust).group(1)
    if qid == 'Q09': return str(int(oz['Bolunmus_Is'].sum())) + ' ' + re.search(r'(\d+) bölünmüş', ust).group(1)
    if qid == 'Q10': r = op_[op_['Operator'] == 'YAŞAR ŞENOL'].iloc[0]; return f"{r.Esdeger_Ort_Sa} {r.Tek_Basina_Adet}"
    if qid == 'Q11': r = op_.sort_values('Esdeger_Ort_Sa').iloc[0]; return f"{r.Operator} {r.Esdeger_Ort_Sa}"
    if qid == 'Q12': r = vd.sort_values('Esdeger_Ort_Sa', ascending=False).iloc[0]; return f"{r['VARDİYA']} {r.Esdeger_Ort_Sa}"
    if qid == 'Q13': return re.search(r'set-up ([\d.]+) sa', ust).group(1)
    if qid == 'Q14':
        r = rt[rt['S/N'] == '2G7JA'].iloc[0]; return f"{r.Toplam_Sa_Tum_Op} {r.Op15_Kim} {r.Op60_Kim}"
    if qid == 'Q15': d = ds[ds['Sebep'] == 'ITEM TEKRARI']; return f"{len(d)} kez {d['Sure_Saat'].sum()} sa"
    if qid == 'Q16': r = tz[tz['TEZGAH'] == 'AB-10'].iloc[0]; return f"{r.Adet_Payi} / {r.SN_Sayisi}"
    if qid == 'Q17': return 'Bu parça/operasyon için S/N izleme verisi yok (yalnızca op 60 kapsanıyor)'
    if qid == 'Q18': r = rt[rt['S/N'] == '2G7JC'].iloc[0]; return f"M01G03, {r.Baska_Parca_Kodu}"
    if qid == 'Q19': return str(oz['Katalog_CT'].max())
    if qid == 'Q20': r = op_[op_['Operator'] == 'HÜSEYİN YILMAZ'].iloc[0]; return f"{r.SN_Sayisi} S/N, {r.Toplam_Saat} sa"
    if qid == 'Q21': return str(hf[hf['Hafta'] == 32].iloc[0]['Setup_Sa'])
    if qid == 'Q22': r = op_[op_['Operator'] == 'ÖZCAN ÇELİK'].iloc[0]; return str(r.Tek_Basina_Ort_Sa)

KURALLAR = """Sen bir fabrikanın üretim verisi asistanısın. Türkçe, net ve sayısal cevap ver.
KURALLAR:
1. Her sayısal iddianı hangi TABLO'dan geldiğini belirterek destekle.
2. Operatör isimlerini kullanarak listele.
5. Veride olmayan bir şeyi UYDURMA. Emin değilsen 'bu veriyle hesaplanamıyor' de.
"""

def main():
    from yukle import yukle
    from sn_izleme import sn_ai_baglami, SN_AI_KURALI
    A = cevap_anahtari(); Q = sorular(A)
    df = yukle(); metin = sn_ai_baglami(df)
    canli = '--gemini' in sys.argv
    print(f"Bağlam: {len(metin):,} karakter, {metin.count('### TABLO')} tablo | soru sayısı: {len(Q)} | mod: {'CANLI Gemini' if canli else 'çevrimdışı bağlam okuma'}\n")
    if canli:
        import os; from dotenv import load_dotenv; import google.generativeai as genai
        load_dotenv(); genai.configure(api_key=os.getenv('GEMINI_API_KEY'))
        model_adi = next((a for a in sys.argv if a.startswith('--model=')), '--model=gemini-2.0-flash').split('=')[1]
        sistem = KURALLAR + SN_AI_KURALI + "\n\n--- VERİ TABLOLARI (CSV) ---\n" + metin
    T = bagla_tablolar(metin); gecen = 0
    for qid, soru, bek in Q:
        if canli:
            cevap = genai.GenerativeModel(model_adi, system_instruction=sistem).start_chat().send_message(soru).text
            time.sleep(13)
        else:
            cevap = baglamdan_cevap(qid, metin, T, A)
        eksik = degerlendir(cevap, bek); gecen += not eksik
        print(f"{'✅' if not eksik else '❌'} {qid} {soru}\n     cevap: {cevap[:160].replace(chr(10), ' ')}\n" + (f"     EKSİK/YANLIŞ: {eksik}\n" if eksik else ''))
    print(f"SONUÇ: {gecen}/{len(Q)}")

if __name__ == '__main__':
    main()
