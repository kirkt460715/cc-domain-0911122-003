from pathlib import Path
from zipfile import ZipFile,ZIP_DEFLATED
W='http://schemas.openxmlformats.org/wordprocessingml/2006/main'
R='http://schemas.openxmlformats.org/officeDocument/2006/relationships'
def build(path):
    document=f"""<w:document xmlns:w="{W}" xmlns:r="{R}"><w:body>
<w:p><w:r><w:t>泵组维护说明</w:t></w:r></w:p>
<w:p><w:del w:id="1"><w:r><w:delText>先打开旧阀。</w:delText></w:r></w:del><w:ins w:id="2"><w:r><w:t>先关闭新阀。</w:t></w:r></w:ins></w:p>
<w:p><w:pPr><w:numPr><w:ilvl w:val="0"/><w:numId w:val="1"/></w:numPr></w:pPr><w:r><w:t>检查压力</w:t></w:r></w:p>
<w:p><w:pPr><w:numPr><w:ilvl w:val="1"/><w:numId w:val="1"/></w:numPr></w:pPr><w:r><w:t>记录读数</w:t></w:r></w:p>
<w:p><w:pPr><w:numPr><w:ilvl w:val="0"/><w:numId w:val="2"/></w:numPr></w:pPr><w:r><w:t>重新编号</w:t></w:r></w:p>
<w:tbl><w:tblPr/><w:tblGrid><w:gridCol w:w="2000"/><w:gridCol w:w="2000"/></w:tblGrid><w:tr><w:tc><w:tcPr><w:gridSpan w:val="2"/></w:tcPr><w:p><w:r><w:t>共同说明</w:t></w:r></w:p></w:tc></w:tr><w:tr><w:tc><w:p><w:r><w:t>温度</w:t></w:r></w:p></w:tc><w:tc><w:p><w:r><w:t>20 摄氏度</w:t></w:r></w:p></w:tc></w:tr></w:tbl>
<w:p><w:moveFrom w:id="3"><w:r><w:t>原位置提醒</w:t></w:r></w:moveFrom></w:p><w:p><w:moveTo w:id="4"><w:r><w:t>原位置提醒</w:t></w:r></w:moveTo><w:r><w:footnoteReference w:id="2"/></w:r><w:hyperlink r:id="link1"><w:r><w:t>说明链接</w:t></w:r></w:hyperlink></w:p>
<w:sectPr><w:headerReference w:type="default" r:id="header1"/></w:sectPr></w:body></w:document>"""
    numbering=f'<w:numbering xmlns:w="{W}"><w:abstractNum w:abstractNumId="0"><w:lvl w:ilvl="0"><w:start w:val="1"/><w:numFmt w:val="decimal"/><w:lvlText w:val="%1."/></w:lvl><w:lvl w:ilvl="1"><w:start w:val="1"/><w:numFmt w:val="decimal"/><w:lvlText w:val="%1.%2"/></w:lvl></w:abstractNum><w:num w:numId="1"><w:abstractNumId w:val="0"/></w:num><w:num w:numId="2"><w:abstractNumId w:val="0"/><w:lvlOverride w:ilvl="0"><w:startOverride w:val="5"/></w:lvlOverride></w:num></w:numbering>'
    notes=f'<w:footnotes xmlns:w="{W}"><w:footnote w:id="2"><w:p><w:r><w:t>断电后操作。</w:t></w:r></w:p></w:footnote><w:footnote w:id="9"><w:p><w:r><w:t>未引用文字。</w:t></w:r></w:p></w:footnote></w:footnotes>'
    relations=f'<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="num" Type="{R}/numbering" Target="numbering.xml"/><Relationship Id="notes" Type="{R}/footnotes" Target="footnotes.xml"/><Relationship Id="header1" Type="{R}/header" Target="header1.xml"/><Relationship Id="link1" Type="{R}/hyperlink" Target="https://example.invalid/manual" TargetMode="External"/></Relationships>'
    types='<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/>'+''.join(f'<Override PartName="/word/{name}.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.{kind}+xml"/>' for name,kind in [('document','document.main'),('numbering','numbering'),('footnotes','footnotes'),('header1','header')])+'</Types>'
    with ZipFile(path,'w',ZIP_DEFLATED) as z:
        for name,value in {'[Content_Types].xml':types,'_rels/.rels':f'<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="{R}/officeDocument" Target="word/document.xml"/></Relationships>','word/document.xml':document,'word/numbering.xml':numbering,'word/footnotes.xml':notes,'word/header1.xml':f'<w:hdr xmlns:w="{W}"><w:p><w:r><w:t>内部页眉</w:t></w:r></w:p></w:hdr>','word/_rels/document.xml.rels':relations}.items():z.writestr(name,value)
    return Path(path)
if __name__=='__main__':
    import argparse
    p=argparse.ArgumentParser();p.add_argument('path');a=p.parse_args();print(build(a.path))
