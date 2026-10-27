from __future__ import annotations
import csv, html, io, json
from pathlib import Path

class ConversionError(ValueError):
    pass

def convert(data: bytes, filename: str, target: str) -> tuple[bytes, str]:
    if len(data) > 20 * 1024 * 1024:
        raise ConversionError('文件不能超过 20 MB')
    source = Path(filename).suffix.lower().lstrip('.')
    target = target.lower().lstrip('.')
    aliases = {'markdown': 'md', 'jpeg': 'jpg'}
    source, target = aliases.get(source, source), aliases.get(target, target)
    if target == 'pdf' and source in {'png', 'jpg', 'jpeg', 'webp'}:
        try:
            from PIL import Image
        except ImportError as exc:
            raise ConversionError('图片转 PDF 需要安装 Pillow') from exc
        image = Image.open(io.BytesIO(data)).convert('RGB')
        output = io.BytesIO()
        image.save(output, format='PDF')
        return output.getvalue(), Path(filename).with_suffix('.pdf').name
    if source == target:
        return data, filename
    if {source, target} == {'json', 'csv'}:
        if source == 'json':
            rows = json.loads(data.decode('utf-8'))
            if not isinstance(rows, list) or not all(isinstance(row, dict) for row in rows): raise ConversionError('JSON must be an array of objects')
            keys = list(dict.fromkeys(key for row in rows for key in row))
            out = io.StringIO(); writer = csv.DictWriter(out, fieldnames=keys); writer.writeheader(); writer.writerows(rows)
            return out.getvalue().encode('utf-8-sig'), Path(filename).with_suffix('.csv').name
        rows = list(csv.DictReader(io.StringIO(data.decode('utf-8-sig'))))
        return json.dumps(rows, ensure_ascii=False, indent=2).encode('utf-8'), Path(filename).with_suffix('.json').name
    if {source, target} == {'txt', 'md'}:
        return data, Path(filename).with_suffix('.' + target).name
    if source == 'md' and target == 'html':
        text = data.decode('utf-8'); lines = []
        for line in text.splitlines():
            if line.startswith('# '): lines.append(f'<h1>{html.escape(line[2:])}</h1>')
            elif line.startswith('## '): lines.append(f'<h2>{html.escape(line[3:])}</h2>')
            elif line: lines.append(f'<p>{html.escape(line)}</p>')
        return ('<!doctype html><html><body>' + ''.join(lines) + '</body></html>').encode('utf-8'), Path(filename).with_suffix('.html').name
    raise ConversionError(f'unsupported conversion: {source} -> {target}')
