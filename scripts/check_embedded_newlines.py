"""Check downloaded FAERS quarters for embedded \\r bytes or short rows."""

import re
import zipfile
from pathlib import Path

RAW_DIR = Path(__file__).resolve().parent.parent / "data" / "raw"
FAERS_TABLES = ["DEMO", "DRUG", "REAC", "OUTC", "RPSR", "THER", "INDI"]


def table_members(z: zipfile.ZipFile, table: str, quarter: str) -> list[str]:
    year_short = quarter[2:4]
    quarter_code = quarter[4:].upper()
    pattern = rf".*{table}{year_short}{quarter_code}.*\.TXT"
    return [m for m in z.namelist() if re.search(pattern, m.upper())]


def main():
    for zip_path in sorted(RAW_DIR.glob("*.zip")):
        quarter = zip_path.stem.removeprefix("aers_ascii_").removeprefix("faers_ascii_")
        with zipfile.ZipFile(zip_path) as z:
            for table in FAERS_TABLES:
                members = table_members(z, table, quarter)
                for member in members:
                    raw = z.read(member)

                    cr_count = raw.count(b"\r")

                    lines = raw.split(b"\n")
                    header, *data_lines = lines
                    expected = header.count(b"$") + 1

                    short_rows = 0
                    surplus_rows = 0
                    short_examples = []
                    for offset, line in enumerate(data_lines):
                        if not line:
                            continue
                        n = line.count(b"$") + 1
                        if n < expected:
                            short_rows += 1
                            if len(short_examples) < 3:
                                short_examples.append((offset + 2, n, line[:80]))
                        elif n > expected:
                            surplus_rows += 1

                    if cr_count or short_rows:
                        print(f"{quarter} {table} ({member}):")
                        print(f"  expected_fields={expected} lines={len(data_lines)}")
                        print(
                            f"  \\r bytes={cr_count} short_rows={short_rows} surplus_rows={surplus_rows}"
                        )
                        for ln, n, snippet in short_examples:
                            print(f"    line {ln}: fields={n} snippet={snippet!r}")
                        print()


if __name__ == "__main__":
    main()
