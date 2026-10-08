"""
config.py — TEK MERKEZİ AYAR DOSYASI

Bu proje artık "miktar/fire" bazlı basit bir modelden, "Giriş" sayfasındaki
GERÇEK ham veriye (tezgah + operatör + zaman damgalı olay kaydı: Üretim /
Duruş / Set-up / Yemek / Rework) dayalı zengin bir OEE analiz sistemine
göre çalışıyor.

Yeni bir kural/kategori eklemek, bir vardiya saatini değiştirmek ya da bir
eşik değerini ayarlamak istediğinizde SADECE bu dosyayı değiştirin.
"""
import os
from pathlib import Path

# .env dosyası varsa oku (DATABASE_URL gibi gizli değerler kod içine
# YAZILMAZ, .env'e konur; .env .gitignore'dadır). python-dotenv kurulu
# değilse sessizce atlanır (ör. Streamlit Cloud gibi ortamlarda env
# değişkenleri zaten platform tarafından sağlanır).
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

# ---------------------------------------------------------------------------
# 1) KLASÖR / DOSYA YOLLARI
# ---------------------------------------------------------------------------
BASE_DIR = Path(__file__).resolve().parent

INCOMING_DIR = BASE_DIR / "data" / "incoming"
DB_DIR = BASE_DIR / "data"
DB_PATH = DB_DIR / "production.db"          # SQLite — yerel/offline fallback
PROCESSED_DIR = BASE_DIR / "data" / "processed"
ERROR_DIR = BASE_DIR / "data" / "errors"

# ---------------------------------------------------------------------------
# 1b) POSTGRES BAĞLANTISI (Supabase / Neon vb.)
# ---------------------------------------------------------------------------
# DATABASE_URL ortam değişkeni (.env dosyasında) TANIMLIYSA sistem Postgres'i
# ANA veri kaynağı olarak kullanır (hem etl.py yazarken hem db.py okurken).
# TANIMLI DEĞİLSE otomatik olarak yerel SQLite'a (DB_PATH) düşer — yani
# internetin/Postgres'in olmadığı bir yerde de (örn. sahada, ilk kurulumda)
# proje çalışmaya devam eder.
#
# .env dosyası örneği (.env.example'a bakın):
#   DATABASE_URL=postgresql://postgres:SIFREN@db.xxxxx.supabase.co:5432/postgres
DATABASE_URL = os.environ.get("DATABASE_URL", "").strip() or None
USE_POSTGRES = DATABASE_URL is not None

# GİZLİLİK: operatorler tablosuna GERÇEK isim yazılsın mı? Varsayılan
# HAYIR — Supabase/Neon gibi ÜÇÜNCÜ TARAF bir bulutta barındığı için,
# gerçek isim<->kod eşlemesi varsayılan olarak SADECE yerel
# OPERATOR_KOD_HARITASI_PATH dosyasında tutulur; Postgres'teki
# operatorler.isim_soyisim NULL yazılır. Gerçekten bulutta da isim
# tutulmasını istiyorsanız .env'e STORE_REAL_NAMES_IN_DB=true ekleyin.
STORE_REAL_NAMES_IN_DB = os.environ.get("STORE_REAL_NAMES_IN_DB", "false").strip().lower() in ("1", "true", "yes")

# Operatör isim -> kod eşlemesinin KALICI olarak saklandığı dosya.
# Neden gerekli: ETL her çalıştığında tüm veriyi baştan işler (full-refresh).
# Eğer Operator_Kod'lar her seferinde "o anki dosyadaki isimlerin alfabetik
# sırasına" göre atanırsa, yeni bir operatör eklendiğinde ya da bir günün
# verisi eksik geldiğinde ESKİ operatörlerin kodu değişebilir — bu da
# geçmiş karneleri/analizleri anlamsızlaştırır. Bu yüzden eşleme burada
# KALICI olarak tutulur; sadece YENİ operatöre yeni kod eklenir, var olanlar
# asla değişmez.
OPERATOR_KOD_HARITASI_PATH = DB_DIR / "operator_kod_haritasi.json"

# ---------------------------------------------------------------------------
# 2) HAM VERİ KAYNAĞI
# ---------------------------------------------------------------------------
SHEET_NAME = "Giriş"   # Ham veri hangi Excel sayfasında
HEADER_ROW = 1          # 0-index — gerçek başlıklar Excel'in 2. satırında
                         # (1. satır boş/birleşik hücre olduğu için pandas'ı şaşırtıyor)

# ---------------------------------------------------------------------------
# 3) TABLO İSİMLERİ (SQLite VE Postgres'te AYNI — tek isim, iki backend)
# ---------------------------------------------------------------------------
TABLE_NAME = "detay_uretim_loglari"      # satır bazlı, tüm feature'ların olduğu ana tablo
TEZGAH_OEE_TABLE = "tezgah_oee"          # tezgah bazlı özet OEE tablosu
OPERATOR_KARNE_TABLE = "operator_karne"  # operatör bazlı performans karnesi
TEZGAHLAR_TABLE = "tezgahlar"            # (sadece Postgres) FK referans tablosu
OPERATORLER_TABLE = "operatorler"        # (sadece Postgres) FK referans tablosu

# detay_uretim_loglari'ye YAZILACAK kanonik kolon listesi. Excel'den gelen
# ama burada olmayan kolonlar (S/N, parça, ham SÜRE_SAAT, İMALAT YAPILAN
# SÜRE-1, OPERATÖR, OPERATÖR_norm gibi ham/gereksiz/hassas kolonlar) ETL
# tarafından ana tabloya YAZILMADAN ÖNCE elenir — hem Postgres tablosu
# bunlardan fazlasını kabul etmez (kolon hatası verir), hem de OPERATÖR /
# OPERATÖR_norm gibi ham isim kolonlarının ana fact tablosunda (her satırda
# tekrar tekrar) durmasını istemiyoruz — bkz. STORE_REAL_NAMES_IN_DB notu.
CANONICAL_COLUMNS = [
    "TEZGAH", "Operator_Kod", "TARİH", "VARDİYA",
    "Vardiya_Uzunluk_Saat", "Vardiya_Sinifi", "Vardiya_Ici_Pozisyon_Yuzde",
    "Durum", "Durus_Kategorisi", "Durus_Sebebi_Ham",
    "PARÇA NO_ÜRETİM_GİRİLEN", "OPERASYON_NO", "KOD & OPE.",
    "Son_Operasyon_mu", "BAŞLANGIÇ_SAAT", "BİTİŞ_SAAT", "Sure_Saat",
    "ÜRETİM_ALARM", "ÜRETİM_C/T", "Sapma_Saat", "Sapma_Yuzdesi",
    "ÜRETİM_SÜRESİ", "YEMEK_MOLA_SÜRESİ", "SET-UP_SÜRESİ",
    "Haftanin_Gunu", "Hafta_Sonu",
    "kaynak", "sisteme_kayit_zamani",
    "S/N",
    "parça",
    "PARÇA NO_UZUN_REFERANS"
]

# ---------------------------------------------------------------------------
# 4) ANALİZ EŞİKLERİ
# ---------------------------------------------------------------------------
KARNE_MIN_SAAT = 50          # operatör karnesine girmek için gereken min. toplam üretim saati
                              # (az veri olan operatörler için istatistik güvenilmez olur)
# Dar tarih aralıklarında (örn. 1 haftalık) KARNE_MIN_SAAT (50 sa) neredeyse
# HERKESİ eler — bu yüzden etl.py'deki compute_operator_karne, efektif eşiği
# min(KARNE_MIN_SAAT, KARNE_MIN_SAAT_GUNLUK * seçili_gun_sayisi) olarak
# dinamik hesaplar. 5 sa/gün, gerçek 6 günlük test haftasında 19 aktif
# operatörden 18'ini içeride tutup sadece gerçekten az çalışan 1 kişiyi
# dışarıda bırakan, ampirik olarak kalibre edilmiş bir değerdir.
KARNE_MIN_SAAT_GUNLUK = 5
EMSAL_MIN_TEKRAR = 3          # bir "KOD & OPE." için emsal medyan hesaplanması için min. tekrar sayısı
CT_DUSUK_GUVEN_ORANI = 0.3   # gerçek süre, referans C/T'nin bu oranından azsa "düşük güven" sayılır
                              # (muhtemelen yanlış/eksik zaman girişi anlamına gelir)

FIRE_ORANI_ESIK = None  # (bu veri setinde fire kavramı yok, kullanılmıyor)
PLANSIZ_DURUS_ORANI_ESIK = 15.0   # % — tezgahın toplam süresinin bu oranından fazlası duruşsa uyar
DIKKAT_ALARM_ORANI_ESIK = 10.0    # % — ÜRETİM_ALARM='DİKKAT' oranı bunu aşarsa uyar

# ---------------------------------------------------------------------------
# 5) DURUM KODU -> OKUNABİLİR İSİM
# ---------------------------------------------------------------------------
# "ÜRETİM\nDURUŞ\nSET-UP\nYEMEK" kolonundaki tek harfli kodun karşılığı
DURUM_MAP = {"Ü": "Üretim", "D": "Plansız_Duruş", "S": "Set_Up", "Y": "Yemek_Mola", "R": "Rework"}

# "KOD & OPE." metninde geçen anahtar kelimelere göre Durum düzeltmesi —
# bazı satırlar "Ü" (Üretim) olarak işaretlenmiş olsa da metninde aslında
# setup/rework/yemek/arıza geçebiliyor; bu kurallar bu yanlış etiketlemeyi
# düzeltir. Liste sırayla uygulanır (regex OR deseni).  
DURUM_DUZELTME_KURALLARI = [
    ("SETUP|AYAR|DATALAMA", "Set_Up"),
    ("REWORK", "Rework"),
    ("ÇAY", "Yemek_Mola"),
    ("ISITMA|DEBURR|GECİKME|BEKLEME|ARIZA", "Plansız_Duruş"),
]

# ---------------------------------------------------------------------------
# 6) VARDİYA SAAT ARALIĞI -> OKUNABİLİR SINIF İSMİ
# ---------------------------------------------------------------------------
VARDIYA_ETIKET_HARITASI = {
    "07:00 - 15:00": "Gündüz (8sa)",
    "15:00 - 23:00": "Akşam (8sa)",
    "23:00 - 07:00": "Gece (8sa)",
    "19:00 - 07:00": "Gece (12sa)",
    "07:00 - 19:00": "Gündüz (12sa)",
    "07:00 - 23:00": "Gündüz-Akşam (16sa)",
}

# ---------------------------------------------------------------------------
# 7) DURUŞ SEBEBİ -> KATEGORİ (Pareto / analiz için anlamlı gruplar)
# ---------------------------------------------------------------------------
# Yeni bir duruş sebebi metni görürseniz (örn. yeni bir arıza tipi), onu
# uygun kategorinin listesine ekleyin — başka hiçbir dosyayı değiştirmenize
# gerek yok.
CATEGORY_RULES = [
    ("Arıza / Ekipman Sorunu", ["ARIZA", "FAN ARIZASI", "SU HORTUMU", "KONVEYOR SIKIŞMASI",
                                "VİNÇ SIKIŞMASI", "KAPI TALAŞ SIKIŞMASI", "KAPI ÇIKMASI",
                                "INSERT KIRILMASI", "KIRILMASI"]),
    ("Servis", ["SERVİS GECİKMESİ"]),
    ("Bekleme (Takım/Malzeme)", ["TAKIM BEKLEME", "PARÇA GİRİŞ BEKLEME", "KANAL TAKIM BEKLEME"]),
    ("Takım/Ekipman Ayarı", ["TAKIM MODİFİYE", "TAKIM DATALAMA", "TAKIM AYARLAMA", "TAKIM BAĞLAMA",
                             "KLEMP", "DAYAMA", "INSERT DENEME", "INSERT AYARLANDI", "INSERT DAĞITILDI",
                             "DATALAMA", "KANAL TAKIM MODİFİYE", "CIVATA", "KAPAK CIVATA"]),
    ("Operasyon/Parça Geçişi (Changeover)", ["DÖNÜŞ", "SETUP İPTAL"]),
    ("Malzeme Hazırlık / Kütük Bağlama", ["KÜTÜK BAĞLAMA"]),
    ("Tezgah Isıtma", ["TEZGAH ISITMA"]),
    ("Temizlik", ["TEZGAH TEMİZLİĞİ", "TALAŞ TEMİZLEME", "ÇUVAL DEĞİŞİMİ"]),
    ("Tekrar / Kalite İlişkili", ["ITEM TEKRARI", "REWORK DESTEK", "DEBURR"]),
    ("İdari / Organizasyonel", ["TOPLANTI", "PLANLAMA", "İDARE İLE GÖRÜŞME", "EVRAK DÜZELTME", "EĞİTİM", "CUMA"]),
    ("Mola Hazırlığı (Çay/Yemek)", ["ÇAY HAZIRLIĞI", "YEMEK HAZIRLIK"]),
    ("Personel Desteği/Yardım", ["DESTEK"]),
]

# ---------------------------------------------------------------------------
# 8) OPERATÖR İSİM DÜZELTMELERİ
# ---------------------------------------------------------------------------
# Türkçe karakter normalizasyonundan (turkce_temizle) SONRA hâlâ kalan yazım
# hatalarını (aynı kişinin farklı yazılmış isimlerini) burada birleştirin.
OPERATOR_DUZELTME = {"HUZEYIN YENICE": "HUSEYIN YENICE"}

# ---------------------------------------------------------------------------
# 9) GÖRÜNÜM AYARLARI
# ---------------------------------------------------------------------------
APP_TITLE = "Üretim Dashboard"

# pandas.dt.day_name() İngilizce döner (Monday, Tuesday, ...) — Haftanin_Gunu
# kolonu bu yüzden İngilizce saklanıyor (veri/analiz tarafı buna dokunmasın
# diye). Sadece EKRANDA göstermek için Türkçe karşılıkları burada tutuyoruz
# (ui_common.gunluk_ozet_tablosu ve günlük görünüm bunu kullanır).
GUN_ISIMLERI_TR = {
    "Monday": "Pazartesi", "Tuesday": "Salı", "Wednesday": "Çarşamba",
    "Thursday": "Perşembe", "Friday": "Cuma", "Saturday": "Cumartesi", "Sunday": "Pazar",
}
