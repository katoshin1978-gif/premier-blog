import zipfile, io, os, base64, requests
from dotenv import load_dotenv
load_dotenv()

ssl   = os.environ.get('SSL_VERIFY','true').lower() != 'false'
token = base64.b64encode(f"{os.environ['WP_USERNAME']}:{os.environ['WP_APP_PASSWORD']}".encode()).decode()
AUTH  = {'Authorization': f'Basic {token}', 'Content-Type': 'application/json'}
BASE  = 'https://premier-blog.com'

with zipfile.ZipFile('premier-blog-theme.zip') as z:
    style_css = z.read('style.css').decode('utf-8')
    all_files = {n: z.read(n) for n in z.namelist()}

# hero-full-title: nowrap・ellipsis・フォントサイズ指定を除去
# grid-column:1/-1 はhero-asym.phpのinlineスタイルで付けているのでCSS側はシンプルに
OLD_CSS = """.hero-full-title {
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  font-size: clamp(28px, 4.5vw, 64px);
  line-height: 1.05;
  letter-spacing: -0.03em;
}"""

NEW_CSS = """.hero-full-title {
  line-height: 0.95;
  letter-spacing: -0.03em;
}"""

new_style = style_css.replace(OLD_CSS, NEW_CSS)
print('変更あり:', style_css != new_style)

r = requests.post(
    f'{BASE}/wp-json/premier-blog/v1/update-file',
    json={'file': 'style.css', 'content': new_style},
    headers=AUTH, verify=ssl, timeout=30,
)
print('style.css:', 'OK' if r.ok else f'NG({r.status_code})')

all_files['style.css'] = new_style.encode('utf-8')
buf = io.BytesIO()
with zipfile.ZipFile(buf, 'w', zipfile.ZIP_DEFLATED) as zout:
    for n, d in all_files.items():
        zout.writestr(n, d)
with open('premier-blog-theme.zip', 'wb') as f:
    f.write(buf.getvalue())
print('zip: OK')
