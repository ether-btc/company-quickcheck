#!/usr/bin/env python3
"""
Merge processed batches into the final output file.
Usage: python merge_batches.py <existing_merged.xlsx> <new_batch.xlsx> <output.xlsx>
"""
import sys
import pandas as pd

if len(sys.argv) != 4:
    print("Usage: merge_batches.py <existing_merged.xlsx> <new_batch.xlsx> <output.xlsx>")
    sys.exit(1)

existing_path = sys.argv[1]
new_batch_path = sys.argv[2]
output_path = sys.argv[3]

existing = pd.read_excel(existing_path)
new_batch = pd.read_excel(new_batch_path)

# Verify no NaN in GELOESCHT of new batch
gelo_col = [c for c in new_batch.columns if 'GEL' in c][0]
nan_count = new_batch[gelo_col].isna().sum()
if nan_count > 0:
    print(f"WARNING: new batch has {nan_count} NaN in GELOESCHT column!")
    print("Rows with NaN:", new_batch[new_batch[gelo_col].isna()].index.tolist())

# Determine row ranges
existing_rows = len(existing)
new_batch_rows = len(new_batch)
print(f"Existing: {existing_rows} rows (0-{existing_rows-1})")
print(f"New batch: {new_batch_rows} rows ({existing_rows}-{existing_rows+new_batch_rows-1})")

# Append new batch to existing
merged = pd.concat([existing, new_batch], ignore_index=True)
print(f"Merged: {len(merged)} rows")
print(f"GELOESCHT NaN: {merged[gelo_col].isna().sum()}")
print(f"GELOESCHT value counts:\n{merged[gelo_col].value_counts().sort_index()}")

merged.to_excel(output_path, index=False)
print(f"Saved to {output_path}")