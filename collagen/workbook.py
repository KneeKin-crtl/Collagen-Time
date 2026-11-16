"""Read the supplied ImageJ workbook layout without inferring fiber correspondence."""
from io import BytesIO
import math
import re

from openpyxl import load_workbook
from openpyxl.utils import get_column_letter
import pandas as pd

SHEETS = {
    'RT30min Length': ('RT30', 'length'),
    'RTC30min Dia': ('RT30', 'diameter'),
    '40C30min Length': ('40C30', 'length'),
    '40C30min Dia': ('40C30', 'diameter'),
    '40C1Hr Length': ('40C1', 'length'),
    '40C1Hr Dia': ('40C1', 'diameter'),
}
COLUMNS = ['condition', 'image_number', 'roi_id', 'metric', 'value', 'source_sheet', 'source_cell']


def import_workbook(data: bytes) -> pd.DataFrame:
    """Import only numbered per-image rows; pooled copies have no ROI ID.

    Values retain their original units. Length and diameter stay separate until
    the operator confirms correspondence and the diameter sampling method.
    No formulas or macros are executed.
    """
    try:
        book = load_workbook(BytesIO(data), read_only=True, data_only=True)
    except Exception as error:
        raise ValueError('Could not read the XLSX workbook.') from error
    records = []
    try:
        for sheet_name, (condition, metric) in SHEETS.items():
            if sheet_name not in book.sheetnames:
                continue
            sheet = book[sheet_name]
            rows = sheet.iter_rows()
            header = next(rows, ())
            starts = [i for i, cell in enumerate(header) if re.fullmatch(r'Image [1-9][0-9]*', str(cell.value))]
            if not starts:
                raise ValueError(f'{sheet_name}: no Image N column groups found.')
            groups = []
            for position, start in enumerate(starts):
                end = starts[position + 1] if position + 1 < len(starts) else len(header)
                measurements = [i for i in range(start + 1, end) if header[i].value == 'Length']
                if len(measurements) != 1:
                    raise ValueError(f'{sheet_name}: each Image N group needs one Length column.')
                groups.append((start, measurements[0], int(header[start].value.split()[1])))
            for row_number, row in enumerate(rows, start=2):
                for id_col, value_col, image_number in groups:
                    roi = row[id_col].value
                    if roi is None:
                        continue  # Excludes the pooled measurements appended below Image 1.
                    value = row[value_col].value
                    if (isinstance(roi, bool) or not isinstance(roi, (float, int))
                            or not math.isfinite(roi) or roi <= 0 or int(roi) != roi):
                        raise ValueError(f'{sheet_name}: invalid ROI ID at {get_column_letter(id_col + 1)}{row_number}.')
                    if (isinstance(value, bool) or not isinstance(value, (float, int))
                            or not math.isfinite(value) or value <= 0):
                        raise ValueError(f'{sheet_name}: missing or invalid measurement at {get_column_letter(value_col + 1)}{row_number}.')
                    records.append(dict(condition=condition, image_number=image_number, roi_id=int(roi),
                                        metric=metric, value=float(value), source_sheet=sheet_name,
                                        source_cell=row[value_col].coordinate))
    finally:
        book.close()
    result = pd.DataFrame(records, columns=COLUMNS)
    if result.empty:
        raise ValueError('No numbered ImageJ measurements found in the supported condition sheets.')
    if result.duplicated(['condition', 'image_number', 'roi_id', 'metric']).any():
        raise ValueError('Duplicate condition/image/ROI/metric records found.')
    return result


def workbook_summary(records):
    return records.groupby(['condition', 'metric'], as_index=False).agg(
        image_count=('image_number', 'nunique'), measurement_count=('value', 'size'),
        mean_in_workbook_units=('value', 'mean'), sd_in_workbook_units=('value', 'std'),
    )


def image_manifest(records):
    manifest = records[['condition', 'image_number']].drop_duplicates().sort_values(['condition', 'image_number']).reset_index(drop=True)
    for name in ('original_filename', 'labeled_filename', 'calibration_um_per_px', 'roi_zip_filename'):
        manifest[name] = ''
    return manifest
