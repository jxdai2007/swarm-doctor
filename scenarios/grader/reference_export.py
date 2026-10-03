"""Trusted reference solution; copied into a workspace only for control runs."""
import csv
import io


def export_csv(rows, columns):
    columns = list(columns)
    if any(not isinstance(column, str) for column in columns):
        raise ValueError("column names must be strings")
    if len(set(columns)) != len(columns):
        raise ValueError("column names must be unique")
    output = io.StringIO(newline="")
    writer = csv.writer(output, lineterminator="\r\n")
    writer.writerow(columns)
    for row in rows:
        writer.writerow(["" if row.get(column) is None else str(row[column])
                         for column in columns])
    return output.getvalue()
