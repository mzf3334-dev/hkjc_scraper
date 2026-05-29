"""
merge_csv.py
Merges all data/hkjc_results_YYYYMMDD.csv files into data/all_results.csv.
Run after every scrape, or manually to rebuild the merged file.
"""

import glob
import os

DATA_DIR  = os.path.join(os.path.dirname(__file__), 'data')
OUT_FILE  = os.path.join(DATA_DIR, 'all_results.csv')

def merge():
    pattern = os.path.join(DATA_DIR, 'hkjc_results_*.csv')
    files   = sorted(glob.glob(pattern))  # sort by filename = date order

    if not files:
        print('No result CSV files found.')
        return

    header_written = False
    total_rows     = 0

    with open(OUT_FILE, 'w', encoding='utf-8-sig', newline='') as out:
        for filepath in files:
            with open(filepath, encoding='utf-8-sig') as f:
                lines = f.readlines()
            if not lines:
                continue
            if not header_written:
                out.write(lines[0])  # write header once
                header_written = True
            for line in lines[1:]:   # skip header of each subsequent file
                if line.strip():
                    out.write(line)
                    total_rows += 1

    print(f'Merged {len(files)} files → {total_rows} rows → {OUT_FILE}')

if __name__ == '__main__':
    merge()
