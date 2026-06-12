import zipfile, io, os, base64, requests
from dotenv import load_dotenv
load_dotenv()

ssl   = os.environ.get('SSL_VERIFY','true').lower() != 'false'
token = base64.b64encode(f"{os.environ['WP_USERNAME']}:{os.environ['WP_APP_PASSWORD']}".encode()).decode()
AUTH  = {'Authorization': f'Basic {token}', 'Content-Type': 'application/json'}
BASE  = 'https://premier-blog.com'

with zipfile.ZipFile('premier-blog-theme.zip') as z:
    archive_php = z.read('archive.php').decode('utf-8')
    all_files   = {n: z.read(n) for n in z.namelist()}

# カテゴリフィルターブロックを削除
old_block = """
    <!-- カテゴリフィルター -->
    <div class="archive-filters">
      <span class="label"><?php _e('カテゴリ:', 'pl-bunseki'); ?></span>
      <?php
      $current_cat = is_category() ? get_queried_object() : null;

      // 全て
      $all_active = !is_category() ? ' active' : '';
      printf(
          '<a href="%s" class="archive-filter-pill%s">%s</a>',
          esc_url(get_post_type_archive_link('post') ?: home_url('/archives/')),
          esc_attr($all_active),
          esc_html__('すべて', 'pl-bunseki')
      );

      $filter_cats = [
          'match-reviews' => '試合レビュー',
          'tactics'       => '戦術分析',
          'united'        => 'ユナイテッド',
          'transfers'     => '移籍・噂',
          'data'          => 'データ',
          'column'        => 'コラム',
          'europe'        => '欧州',
      ];
      foreach ($filter_cats as $slug => $label) {
          $cat = get_category_by_slug($slug);
          if (!$cat) continue;
          $active = ($current_cat && $current_cat->slug === $slug) ? ' active' : '';
          printf(
              '<a href="%s" class="archive-filter-pill%s">%s</a>',
              esc_url(get_category_link($cat->term_id)),
              esc_attr($active),
              esc_html($label)
          );
      }
      ?>
    </div>
"""

new_archive = archive_php.replace(old_block, '\n')

print('変更あり:', archive_php != new_archive)

r = requests.post(
    f'{BASE}/wp-json/premier-blog/v1/update-file',
    json={'file': 'archive.php', 'content': new_archive},
    headers=AUTH, verify=ssl, timeout=30,
)
print('archive.php:', 'OK' if r.ok else f'NG({r.status_code})')

if r.ok:
    all_files['archive.php'] = new_archive.encode('utf-8')
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, 'w', zipfile.ZIP_DEFLATED) as zout:
        for n, d in all_files.items():
            zout.writestr(n, d)
    with open('premier-blog-theme.zip', 'wb') as f:
        f.write(buf.getvalue())
    print('zip: OK')
