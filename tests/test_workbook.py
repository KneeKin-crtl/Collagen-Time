from io import BytesIO

import pytest
from openpyxl import Workbook

from collagen.workbook import import_workbook, workbook_summary, image_manifest


def workbook_bytes(rows=None):
    book = Workbook()
    sheet = book.active
    sheet.title = 'RT30min Length'
    sheet.append(['Image 1', 'Area', 'Length', None, 'Image 2', 'Area', 'Length'])
    for row in rows if rows is not None else [[1, 5, 12, None, 1, 4, 20], [2, 3, 8, None, 2, 2, 10], [None, None, 20], [None, None, 10]]:
        sheet.append(row)
    diameter = book.create_sheet('RTC30min Dia')
    diameter.append(['Image 1', 'Angle', 'Length'])
    diameter.append([1, -30, .8])
    # A pooled summary sheet must never be imported again.
    pooled = book.create_sheet('Length')
    pooled.append(['Length', 'RT30'])
    pooled.append([None, 12])
    buffer = BytesIO()
    book.save(buffer)
    return buffer.getvalue()


def test_pooled_rows_not_duplicated_and_diameter_not_assumed_paired():
    records = import_workbook(workbook_bytes())
    assert len(records) == 5
    assert records.metric.value_counts().to_dict() == {'length': 4, 'diameter': 1}
    assert records.loc[records.metric == 'diameter', 'value'].iloc[0] == .8
    assert set(records.source_sheet) == {'RT30min Length', 'RTC30min Dia'}
    assert records.iloc[0].source_cell == 'C2'
    assert records.iloc[0].roi_id == 1
    summary = workbook_summary(records).set_index('metric')
    assert summary.loc['length', 'measurement_count'] == 4
    assert summary.loc['length', 'image_count'] == 2
    assert summary.loc['length', 'mean_in_workbook_units'] == 12.5
    manifest = image_manifest(records)
    assert len(manifest) == 2
    assert manifest.original_filename.eq('').all()
    assert manifest.calibration_um_per_px.eq('').all()


@pytest.mark.parametrize('roi,value', [(0, 12), (1.2, 12), ('one', 12), (True, 12), (1, None), (1, -2), (1, 'bad')])
def test_invalid_numbered_records_not_silently_dropped(roi, value):
    with pytest.raises(ValueError):
        import_workbook(workbook_bytes([[roi, 3, value]]))


def test_duplicate_ids_rejected():
    with pytest.raises(ValueError, match='Duplicate'):
        import_workbook(workbook_bytes([[1, 3, 12], [1, 3, 9]]))


def test_formula_without_cached_measurement_rejected():
    with pytest.raises(ValueError, match='measurement'):
        import_workbook(workbook_bytes([[1, 3, '=6+6']]))


def test_invalid_xlsx():
    with pytest.raises(ValueError, match='XLSX'):
        import_workbook(b'not an Excel file')
