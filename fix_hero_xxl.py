import zipfile, io, os, base64, requests
from dotenv import load_dotenv
load_dotenv()

ssl   = os.environ.get('SSL_VERIFY','true').lower() != 'false'
token = base64.b64encode(f"{os.environ['WP_USERNAME']}:{os.environ['WP_APP_PASSWORD']}".encode()).decode()
AUTH  = {'Authorization': f'Basic {token}', 'Content-Type': 'application/json'}
BASE  = 'https://premier-blog.com'

with zipfile.ZipFile('premier-blog-theme.zip') as z:
    hero_php  = z.read('template-parts/hero-asym.php').decode('utf-8')
    all_files = {n: z.read(n) for n in z.namelist()}

new_hero = hero_php.replace(
    '<h2 class="h-title hero-full-title"',
    '<h2 class="h-title xxl hero-full-title"'
)
print('変更あり:', hero_php != new_hero)

r = requests.post(
    f'{BASE}/wp-json/premier-blog/v1/update-file',
    json={'file': 'template-parts/hero-asym.php', 'content': new_hero},
    headers=AUTH, verify=ssl, timeout=30,
)
print('hero-asym.php:', 'OK' if r.ok else f'NG({r.status_code})')

all_files['template-parts/hero-asym.php'] = new_hero.encode('utf-8')
buf = io.BytesIO()
with zipfile.ZipFile(buf, 'w', zipfile.ZIP_DEFLATED) as zout:
    for n, d in all_files.items():
        zout.writestr(n, d)
with open('premier-blog-theme.zip', 'wb') as f:
    f.write(buf.getvalue())
print('zip: OK')
