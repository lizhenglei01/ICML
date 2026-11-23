"""Read declared live publication cells without changing the submitted workbook."""
from hashlib import sha256
from pathlib import Path
from tempfile import TemporaryDirectory
from zipfile import ZipFile

from lxml import etree
from openpyxl import load_workbook
from openpyxl.workbook.properties import CalcProperties

from .hotel_milestone_formulas import _recalculate


def workbook_signature(path):
    """Treat an explicit automatic calculation mode as the Excel default."""
    with ZipFile(path) as archive:
        root = etree.fromstring(archive.read('xl/workbook.xml'))
    calc = root.find('{*}calcPr')
    if calc is not None and calc.get('calcMode') == 'auto':
        del calc.attrib['calcMode']
    return sha256(etree.tostring(root, method='c14n')).hexdigest()


def publication_readback(path, cells, count_location=None):
    """Only read formula results freshly calculated from a disposable copy."""
    workbook = load_workbook(path, keep_links=False)
    try:
        computed = [binding for binding in cells
                    if workbook[binding['sheet']][binding['cell']].data_type == 'f']
        if not computed:
            return {}, None
        with TemporaryDirectory(prefix='hotel_publish_') as tmp:
            work = Path(tmp)
            (work / 'input').mkdir()
            (work / 'output').mkdir()
            workbook.calculation = CalcProperties(calcMode='auto', fullCalcOnLoad=True, forceFullCalc=True)
            workbook.save(work / 'input/report.xlsx')
            _recalculate(work, ['report.xlsx'], 30)
            recalculated = load_workbook(work / 'output/report.xlsx', data_only=True)
            try:
                readings = {}
                for binding in computed:
                    cell = recalculated[binding['sheet']][binding['cell']]
                    readings[binding['evidence_id']] = {'value': cell.value, 'type': cell.data_type}
                count = recalculated[count_location['sheet']][count_location['cell']].value if count_location else None
            finally:
                recalculated.close()
    finally:
        workbook.close()
    return readings, count
