"""Patch the shipped template in memory for import regression tests only."""
import io
from pathlib import Path
import zipfile
import xml.etree.ElementTree as ET

def fixture(edits=None):
    path=Path(__file__).resolve().parents[1]/'templates/CSGOJ节点导入模板.xlsx'
    changes={'C6':'192.0.2.10','C7':'192.0.2.11','C8':'192.0.2.20','C9':'192.0.2.21'}
    changes.update(edits or {})
    ns='http://schemas.openxmlformats.org/spreadsheetml/2006/main'
    output=io.BytesIO()
    with zipfile.ZipFile(path) as src,zipfile.ZipFile(output,'w') as dst:
        for file in src.infolist():
            data=src.read(file.filename)
            if file.filename=='xl/worksheets/sheet1.xml':
                xml=ET.fromstring(data)
                for address,value in changes.items():
                    rowid=''.join(x for x in address if x.isdigit())
                    row=xml.find(f'{{{ns}}}sheetData/{{{ns}}}row[@r="{rowid}"]')
                    if row is None:raise ValueError(address)
                    cell=row.find(f'{{{ns}}}c[@r="{address}"]')
                    if cell is None:cell=ET.SubElement(row,'{'+ns+'}c',{'r':address})
                    cell.clear();cell.set('r',address);cell.set('t','inlineStr')
                    inline=ET.SubElement(cell,'{'+ns+'}is');ET.SubElement(inline,'{'+ns+'}t').text=value
                data=ET.tostring(xml,encoding='utf-8',xml_declaration=True)
            dst.writestr(file,data)
    return output.getvalue()
