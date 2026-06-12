import zipfile, io, os, base64, requests
from dotenv import load_dotenv
load_dotenv()

ssl   = os.environ.get('SSL_VERIFY','true').lower() != 'false'
token = base64.b64encode(f"{os.environ['WP_USERNAME']}:{os.environ['WP_APP_PASSWORD']}".encode()).decode()
AUTH  = {'Authorization': f'Basic {token}', 'Content-Type': 'application/json'}
BASE  = 'https://premier-blog.com'

with zipfile.ZipFile('premier-blog-theme.zip') as z:
    hero_php = z.read('template-parts/hero-asym.php').decode('utf-8')
    style_css = z.read('style.css').decode('utf-8')
    all_files = {n: z.read(n) for n in z.namelist()}

# --- hero-asym.php 修正 ---
# 変更前: .main の中に h2 がある
# 変更後: h2 を grid-column:1/-1 の独立要素に移動

OLD_MAIN = '''  <div class="main">

    <?php if ($kicker): ?>
      <div class="kicker" style="margin-bottom:14px;">
        <span class="dot"></span><?php echo esc_html($kicker); ?>
      </div>
    <?php endif; ?>

    <h2 class="h-title xxl">
      <a href="<?php echo esc_url(get_permalink($post_id)); ?>">
        <?php echo esc_html(get_the_title($post_id)); ?>
      </a>
    </h2>

    <p class="dek" style="margin-top:24px;">'''

NEW_MAIN = '''  <!-- タイトル: 画像と同幅・折り返しなし -->
  <h2 class="h-title hero-full-title" style="grid-column:1/-1;margin-bottom:20px;">
    <a href="<?php echo esc_url(get_permalink($post_id)); ?>">
      <?php echo esc_html(get_the_title($post_id)); ?>
    </a>
  </h2>

  <div class="main">

    <?php if ($kicker): ?>
      <div class="kicker" style="margin-bottom:14px;">
        <span class="dot"></span><?php echo esc_html($kicker); ?>
      </div>
    <?php endif; ?>

    <p class="dek" style="margin-top:0;">'''

new_hero = hero_php.replace(OLD_MAIN, NEW_MAIN)
print('hero-asym.php 変更あり:', hero_php != new_hero)

# --- style.css: hero-full-title クラス追加 ---
OLD_CSS = '.hero-asym .main h2 { margin-bottom: 20px; }'
NEW_CSS = '''.hero-full-title {
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  font-size: clamp(28px, 4.5vw, 64px);
  line-height: 1.05;
  letter-spacing: -0.03em;
}'''
new_style = style_css.replace(OLD_CSS, NEW_CSS)
print('style.css 変更あり:', style_css != new_style)

# WordPress へ反映
for fname, content in [('template-parts/hero-asym.php', new_hero), ('style.css', new_style)]:
    r = requests.post(
        f'{BASE}/wp-json/premier-blog/v1/update-file',
        json={'file': fname, 'content': content},
        headers=AUTH, verify=ssl, timeout=30,
    )
    print(f'{fname}: {"OK" if r.ok else f"NG({r.status_code})"}')

# zip 更新
all_files['template-parts/hero-asym.php'] = new_hero.encode('utf-8')
all_files['style.css'] = new_style.encode('utf-8')
buf = io.BytesIO()
with zipfile.ZipFile(buf, 'w', zipfile.ZIP_DEFLATED) as zout:
    for n, d in all_files.items():
        zout.writestr(n, d)
with open('premier-blog-theme.zip', 'wb') as f:
    f.write(buf.getvalue())
print('zip: OK')
