import pandas as pd, numpy as np
def yukle():
    df = pd.read_excel('data/incoming/puredata1.xlsx', sheet_name='Giriş', header=1)
    df.columns = [str(c).replace('\n',' ').strip() for c in df.columns]
    def saat(x):
        try: return x.hour + x.minute/60 + x.second/3600
        except: return np.nan
    df['Sure_Saat']=df['SÜRE SAAT'].map(saat)
    df['Durum']=df['ÜRETİM DURUŞ SET-UP YEMEK'].map({'Ü':'Üretim','S':'Set_Up','D':'Plansız_Duruş','Y':'Yemek_Mola'})
    df['S/N']=df['S/N'].astype('string')
    return df
