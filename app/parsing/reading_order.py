"""Reading order for text rectangles on a single page."""
def by_columns(rows, width):
    middle = width / 2
    ordered = sorted(rows, key=lambda row: (row["bbox"][1], row["bbox"][0]))
    left = [row for row in ordered if row["bbox"][2] <= middle]
    right = [row for row in ordered if row["bbox"][0] >= middle]
    if len(left) < 2 or len(right) < 2:
        return ordered
    # Full-width headings delimit sections before the column-local ordering.
    result, group = [], []
    for row in ordered:
        a, _, b, _ = row["bbox"]
        if a < middle < b and b-a > .55 * width:
            result.extend(sorted(group, key=lambda item: (item["bbox"][0] >= middle, item["bbox"][1], item["bbox"][0])))
            result.append(row)
            group = []
        else:
            group.append(row)
    result.extend(sorted(group, key=lambda item: (item["bbox"][0] >= middle, item["bbox"][1], item["bbox"][0])))
    return result
