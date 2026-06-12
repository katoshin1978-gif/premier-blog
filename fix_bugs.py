import zipfile, io, os, base64, requests
from dotenv import load_dotenv
load_dotenv()

ssl   = os.environ.get('SSL_VERIFY','true').lower() != 'false'
token = base64.b64encode(f"{os.environ['WP_USERNAME']}:{os.environ['WP_APP_PASSWORD']}".encode()).decode()
AUTH  = {'Authorization': f'Basic {token}', 'Content-Type': 'application/json'}
BASE  = 'https://premier-blog.com'

def push_file(filename, content):
    r = requests.post(
        f'{BASE}/wp-json/premier-blog/v1/update-file',
        json={'file': filename, 'content': content},
        headers=AUTH, verify=ssl, timeout=30,
    )
    status = 'OK' if r.ok else f'NG({r.status_code}) {r.text[:100]}'
    print(f'  [{status}] {filename}')
    return r.ok

with zipfile.ZipFile('premier-blog-theme.zip') as z:
    functions_php = z.read('functions.php').decode('utf-8')
    style_css     = z.read('style.css').decode('utf-8')
    all_files     = {n: z.read(n) for n in z.namelist()}

# 1. functions.php: pre_get_posts でdraft記事をアーカイブにも表示
pre_get_posts_code = """
// カテゴリ/アーカイブでdraft記事も表示
add_action('pre_get_posts', function($query) {
    if (!is_admin() && $query->is_main_query() && (is_category() || is_archive() || is_tag())) {
        $query->set('post_status', ['publish', 'draft']);
    }
});
"""
if 'pre_get_posts' not in functions_php:
    new_functions = functions_php.rstrip() + '\n' + pre_get_posts_code + '\n'
    print('[1] functions.php: pre_get_posts追加')
else:
    new_functions = functions_php
    print('[1] functions.php: pre_get_posts既存スキップ')

# 2. style.css: card-feature のモバイル対応（900px以下で1カラム化）
old_900 = '@media (max-width: 900px) {\n  .body-grid, .hero-asym { grid-template-columns: 1fr; }'
new_900 = '@media (max-width: 900px) {\n  .body-grid, .hero-asym, .card-feature { grid-template-columns: 1fr; }'
if old_900 in style_css:
    new_style = style_css.replace(old_900, new_900)
    print('[2] style.css: card-feature モバイル対応追加')
elif 'card-feature' in style_css[style_css.find('@media (max-width: 900px)'):style_css.find('@media (max-width: 900px)')+200]:
    new_style = style_css
    print('[2] style.css: card-feature 既存スキップ')
else:
    new_style = style_css
    print('[2] style.css: 対象文字列が見つからない - 手動確認要')

print('\n=== WordPress へ反映 ===')
ok1 = push_file('functions.php', new_functions)
ok2 = push_file('style.css', new_style)

if ok1 and ok2:
    all_files['functions.php'] = new_functions.encode('utf-8')
    all_files['style.css']     = new_style.encode('utf-8')
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, 'w', zipfile.ZIP_DEFLATED) as zout:
        for n, d in all_files.items():
            zout.writestr(n, d)
    with open('premier-blog-theme.zip', 'wb') as f:
        f.write(buf.getvalue())
    print('\n[完了] zip・サイト両方を更新済み')
else:
    print('\n[エラー] 一部更新失敗')
