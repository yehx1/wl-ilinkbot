from app.conversion import convert

def test_json_csv_roundtrip():
    csv_data, name = convert(b'"name","age"\n"A",1\n', 'people.csv', 'json')
    assert name == 'people.json'
    assert b'"name": "A"' in csv_data

def test_markdown_html():
    data, name = convert(b'# Hello\nWorld', 'note.md', 'html')
    assert name == 'note.html'
    assert b'<h1>Hello</h1>' in data


def test_markdown_alias_and_html_escaping():
    data, name = convert(b'# <unsafe>', 'note.md', 'html')
    assert name == 'note.html'
    assert b'&lt;unsafe&gt;' in data
    data, name = convert(b'plain', 'note.txt', 'markdown')
    assert data == b'plain'
    assert name == 'note.md'
