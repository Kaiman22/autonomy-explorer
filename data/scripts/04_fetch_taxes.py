#!/usr/bin/env python3
"""
Step 4: Fetch Swiss municipality tax multipliers (Steuerfüsse) from ESTV.
Primary: parse downloaded Excel export from swisstaxcalculator.
Fallback: try the API endpoint.
Outputs: data/processed/taxes.json

Excel structure (estv_income_rates.xlsx):
  Row 1: Title
  Row 2: Year
  Row 3: Category headers (Income tax, Wealth tax, etc.)
  Row 4: Column headers (Canton ID, Canton, SFO Commune ID, Commune, ...)
  Row 5+: Data rows

Key columns (0-indexed):
  0: Canton ID
  1: Canton abbreviation (e.g. "ZH")
  2: BFS Commune ID (SFO Commune ID)
  3: Commune name
  4: Income tax - Canton multiplier (%)
  5: Income tax - Commune multiplier (%)
"""
import glob
import json
import os

import openpyxl

from config import PROCESSED_DIR


def parse_excel(xlsx_path):
    """Parse ESTV Excel export into tax dictionary."""
    wb = openpyxl.load_workbook(xlsx_path, read_only=True)
    ws = wb.active

    taxes = {}
    for i, row in enumerate(ws.iter_rows(min_row=5, values_only=True)):
        # Skip rows without valid BFS number
        bfs_nr = row[2]
        if bfs_nr is None:
            continue
        try:
            bfs_nr = int(bfs_nr)
        except (ValueError, TypeError):
            continue

        canton = row[1] or ""
        name = row[3] or ""
        canton_multiplier = row[4]  # Income tax canton %
        commune_multiplier = row[5]  # Income tax commune %

        # Total income tax multiplier = canton + commune
        total = None
        if canton_multiplier is not None and commune_multiplier is not None:
            total = round(float(canton_multiplier) + float(commune_multiplier), 2)
        elif commune_multiplier is not None:
            total = round(float(commune_multiplier), 2)

        taxes[str(bfs_nr)] = {
            "name": name,
            "canton": canton,
            "multiplier": total,
            "canton_rate": round(float(canton_multiplier), 2) if canton_multiplier else None,
            "commune_rate": round(float(commune_multiplier), 2) if commune_multiplier else None,
        }

    wb.close()
    return taxes


def main():
    # Look for Excel file in Downloads or raw data dir
    search_paths = [
        os.path.expanduser("~/Downloads"),
        str(PROCESSED_DIR.parent / "raw"),
    ]

    xlsx_path = None
    for base in search_paths:
        files = glob.glob(os.path.join(base, "estv_income_rates*.xlsx"))
        if files:
            xlsx_path = max(files, key=os.path.getmtime)
            break

    if not xlsx_path:
        print("No ESTV Excel file found.")
        print("Please download from: https://swisstaxcalculator.estv.admin.ch/#/taxdata/tax-rates")
        print("Click 'Export tax multipliers' and save the .xlsx file to ~/Downloads/")
        return

    print(f"Parsing Excel: {xlsx_path}")
    taxes = parse_excel(xlsx_path)
    print(f"Parsed {len(taxes)} municipality tax records")

    if taxes:
        # Show sample
        sample = list(taxes.items())[:3]
        for bfs, t in sample:
            print(f"  {bfs}: {t['name']} ({t['canton']}) - total {t['multiplier']}%")

    out_path = PROCESSED_DIR / "taxes.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(taxes, f, ensure_ascii=False, indent=2)
    print(f"Saved to {out_path}")


if __name__ == "__main__":
    main()
