import random
import re
import zipfile
import polars as pl # type:ignore

CASE_ID_COLUMNS = ["caseid", "ISR", "CASE"]


def show_case(tables: dict[str, list[pl.DataFrame]]) -> None:
    """Show all records for a random case across all tables.

    Handles both modern (caseid) and legacy (ISR/CASE) identifiers.

    Args:
        tables: Dict mapping table names to list of DataFrames
    """
    demo_dfs = tables.get("DEMO", [])
    if not demo_dfs:
        print("No DEMO table found")
        return

    demo = pl.concat(demo_dfs)

    # Try modern caseid first, then legacy ISR/CASE
    case_col = None
    for col_name in CASE_ID_COLUMNS:
        if col_name in demo.columns:
            case_col = col_name
            break

    if not case_col:
        print(f"No case identifier found in DEMO. Columns: {demo.columns}")
        return

    random_case = demo.sample(1)[case_col].item()
    print(f"\n=== {case_col}: {random_case} ===\n")

    for table_name, dfs in tables.items():
        if not dfs:
            continue

        combined = pl.concat(dfs)

        table_case_col = next((c for c in combined.columns if c in CASE_ID_COLUMNS), None)
        if table_case_col is None:
            continue

        filtered = combined.filter(pl.col(table_case_col) == random_case)
        if filtered.height > 0:
            print(f"\n{table_name}:")
            print(filtered)


def read_all_tables(quarter: str) -> dict[str, list[pl.DataFrame]]:
    """Read all FAERS table files from a quarterly zip.

    Args:
        quarter: Quarter code (e.g. '2024q4')

    Returns:
        Dict mapping table names to list of DataFrames found for that table
    """
    quarter = quarter.lower()
    year = int(quarter[:4])
    q = int(quarter[5])

    prefix = "aers_ascii_" if (year, q) <= (2012, 3) else "faers_ascii_"
    z = zipfile.ZipFile(f"data/raw/{prefix}{quarter}.zip")

    FAERS_TABLES = ["DEMO", "DRUG", "REAC", "OUTC", "RPSR", "THER", "INDI"]
    results = {table: [] for table in FAERS_TABLES}

    year_short = quarter[2:4]
    quarter_code = quarter[4:].upper()

    for member in z.namelist():
        for table in FAERS_TABLES:
            pattern = rf".*{table}{year_short}{quarter_code}.*\.TXT"
            if re.search(pattern, member.upper()):
                with z.open(member) as f:
                    df = pl.read_csv(
                        f, separator="$", infer_schema_length=0, truncate_ragged_lines=True
                    )
                results[table].append(df)

    return results


if __name__ == "__main__":
    quarter = input("quarter (e.g. 2024q4): ").strip()

    tables = read_all_tables(quarter)

    for table_name, dfs in tables.items():
        if dfs:
            print(f"\n=== {table_name} (found {len(dfs)} file(s)) ===")
            print(dfs[0].head())
        else:
            print(f"\n=== {table_name}: not found ===")

    show_random = input("\nShow a random case? (y/n): ").strip().lower()
    if show_random == "y":
        show_case(tables)