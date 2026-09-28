import pandas as pd
import os

base = r"C:\Users\Akaero\Documents\newakaeroproject1 - Kopya"
incoming = os.path.join(base, "data", "incoming")

if os.path.exists(incoming):
    xlsx_files = [f for f in os.listdir(incoming) if f.endswith('.xlsx')]
    print(f"XLSX dosyaları: {xlsx_files}")
    
    for xlsx_file in xlsx_files:
        filepath = os.path.join(incoming, xlsx_file)
        print(f"\n=== {xlsx_file} ===")
        xls = pd.ExcelFile(filepath, engine='openpyxl')
        print(f"Sheet'ler: {xls.sheet_names}")
        
        for sheet_name in xls.sheet_names:
            df = pd.read_excel(filepath, sheet_name=sheet_name, header=1, dtype=str)
            print(f"\n--- Sheet: {sheet_name} ---")
            print(f"Kolonlar: {list(df.columns)}")
            print(f"Satır sayısı: {len(df)}")
            print(f"Tipler: {df.dtypes.tolist()}")
            print("İlk 3 satır:")
            print(df.head(3).to_string())
else:
    print("incoming directory not found")